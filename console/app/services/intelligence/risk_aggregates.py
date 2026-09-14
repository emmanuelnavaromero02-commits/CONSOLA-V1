"""Cap-free SQL aggregates for the RISK domain (Mission 1).

Datasets (layer=gold, resolved through publication heads):

* ``sap_successfactors_talent_retention_risk`` (+ ``..._talent_action_candidates``)
  -> :func:`query_attrition_risk_population`  (reuses Talent's score, no recompute)
* ``sap_successfactors_employee_360``
  -> :func:`query_employment_end_expiry`       (proxy for contract expiry)
* ``salesforce_deals_en_riesgo``
  -> :func:`query_deal_slippage`

Metrics without a Gold relation (cost center overrun: no budget data anywhere)
intentionally have no function here: see ``docs/data_gaps.md``.

Same pattern as ``successfactors_talent_population``: dedicated asyncpg
connection to GOLD_DATABASE_URL, repeatable_read read-only transaction,
``set_config`` GUCs, head resolution + ``to_regclass`` + column contract,
explicit workspace/tenant predicate, ``$n`` parameters, COUNT/SUM in SQL,
top-N bounded by ``MAX_GROUP_ROWS``. Person-level datasets are only ever
aggregated; no user_id, name or seller is returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.services.intelligence.domain_aggregate_support import (
    DEFAULT_TOP_N,
    GOLD_SCOPE_PREDICATE,
    STATUS_DEGRADED,
    STATUS_UNAVAILABLE,
    AggregateResult,
    GoldScope,
    as_float,
    as_int,
    as_of_date,
    clamp_named_rows,
    clamp_top_n,
    gold_evidence,
    invalid_schema_error,
    missing_optional_columns,
    resolve_optional_relation,
    resolve_relation,
    run_gold_aggregate,
    status_for,
)

RETENTION_RISK_DATASET = "sap_successfactors_talent_retention_risk"
ACTION_CANDIDATES_DATASET = "sap_successfactors_talent_action_candidates"
EMPLOYEE_360_DATASET = "sap_successfactors_employee_360"
DEALS_AT_RISK_DATASET = "salesforce_deals_en_riesgo"

RETENTION_ACTION_ID = "talent_retention_risk"
# employee_360.end_date uses a far-future sentinel (2030+) for open-ended
# employment (see sap_successfactors_talent_attrition_by_cohort_month.sql).
# Bound as a date parameter so the comparison stays date < date in Postgres.
EMPLOYMENT_END_SENTINEL_DATE = date(2030, 1, 1)
EXPIRY_WINDOWS_DAYS = (30, 60, 90)
# Bound for GROUP BY breakdowns (stages, risk reasons) in deal_slippage.
BREAKDOWN_ROWS = 20

_RETENTION_REQUIRED = frozenset({"risk_band"})
# Only columns the SQL below actually uses: a missing optional column must mean
# the metric really lost something (avg score, department breakdown).
_RETENTION_OPTIONAL = frozenset({"retention_risk_score", "department_name"})
_ACTION_REQUIRED = frozenset({"action_id", "affected_count"})
_ACTION_OPTIONAL = frozenset({"severity"})

_EMPLOYEE_REQUIRED = frozenset({"end_date"})
_EMPLOYEE_OPTIONAL = frozenset({"is_active", "department_name"})

_DEALS_REQUIRED = frozenset({"close_date"})
_DEALS_OPTIONAL = frozenset(
    {"amount", "stage_name", "opportunity_name", "dias_sin_actividad", "motivo_riesgo"}
)

EMPLOYMENT_END_PROXY_NOTE = (
    "Cuenta empleados de SuccessFactors (employee_360) cuyo end_date de empleo "
    "cae dentro de 30/60/90 dias desde as_of, excluyendo el centinela 2030+ "
    "que marca empleo indefinido. Es el fin del registro de empleo, NO un "
    "elemento contractual: los contratos SAP HCM PA0016 "
    "(sap_hcm_contractdata_latest, contract_type/valid_to) son silver y no son "
    "consultables desde Gold."
)

ATTRITION_NOTES = [
    "Score y bandas calculados por el dataset Talent "
    "(retention_risk_score >= 70 high, >= 45 medium, resto low; "
    "insufficient_data cuando C/P/A es invalido); no se recalcula aqui.",
    "recommendation_only: riesgo de salida sin datos de compensacion.",
]

DEAL_SLIPPAGE_NOTES = [
    "amount es el Amount de Salesforce sin conversion de moneda.",
    "salesforce_deals_en_riesgo ya excluye oportunidades cerradas (NOT is_closed) "
    "al materializar; el snapshot corresponde al published_at del head.",
]


# ── R1: attrition risk population (reuses Talent) ───────────────────────────


@dataclass
class AttritionRiskPopulation(AggregateResult):
    total: int | None = None
    high: int | None = None
    medium: int | None = None
    low: int | None = None
    insufficient_data: int | None = None
    avg_score_valid: float | None = None
    departments_top: list[dict[str, Any]] = field(default_factory=list)
    talent_action: dict[str, Any] | None = None


async def query_attrition_risk_population(
    user: dict | None,
    *,
    top_n: int = 10,
) -> AttritionRiskPopulation:
    """COUNT employees per retention risk band; high-risk departments top-N."""
    top_n = clamp_top_n(top_n, default=10)

    def _unavailable(error: str) -> AttritionRiskPopulation:
        return AttritionRiskPopulation(status=STATUS_UNAVAILABLE, error=error)

    async def _compute(scope: GoldScope) -> AttritionRiskPopulation:
        rel = await resolve_relation(
            scope,
            RETENTION_RISK_DATASET,
            required=_RETENTION_REQUIRED,
            optional=_RETENTION_OPTIONAL,
        )
        if rel.missing_required:
            return AttritionRiskPopulation(
                status=STATUS_UNAVAILABLE,
                error=invalid_schema_error(rel),
                missing_columns=list(rel.missing_required),
            )
        avg_expr = rel.expr(
            "retention_risk_score",
            "AVG(retention_risk_score) FILTER (WHERE risk_band IN ('high', 'medium', 'low'))::float8",
            "NULL::float8",
        )
        bands_sql = f"""
            -- omega-aggregate: risk.attrition_risk_population.bands
            SELECT COUNT(*)::bigint AS total,
                   COUNT(*) FILTER (WHERE risk_band = 'high')::bigint AS high,
                   COUNT(*) FILTER (WHERE risk_band = 'medium')::bigint AS medium,
                   COUNT(*) FILTER (WHERE risk_band = 'low')::bigint AS low,
                   COUNT(*) FILTER (WHERE risk_band = 'insufficient_data')::bigint AS insufficient_data,
                   {avg_expr} AS avg_score_valid
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
        """
        bands = await scope.conn.fetchrow(bands_sql, *scope.scope_args)
        evidence = [gold_evidence(rel, filters={"top_n": top_n})]
        notes = list(ATTRITION_NOTES)

        departments: list[dict[str, Any]] = []
        if rel.has("department_name"):
            departments_sql = f"""
                -- omega-aggregate: risk.attrition_risk_population.departments
                SELECT department_name,
                       COUNT(*)::bigint AS total,
                       COUNT(*) FILTER (WHERE risk_band = 'high')::bigint AS high,
                       COUNT(*) FILTER (WHERE risk_band = 'medium')::bigint AS medium
                  FROM {rel.sql}
                 WHERE {GOLD_SCOPE_PREDICATE}
                 GROUP BY department_name
                HAVING COUNT(*) FILTER (WHERE risk_band = 'high') > 0
                 ORDER BY high DESC, total DESC, department_name
                 LIMIT $3
            """
            rows = await scope.conn.fetch(departments_sql, *scope.scope_args, top_n)
            departments = [
                {
                    "department_name": row["department_name"],
                    "total": as_int(row["total"]),
                    "high": as_int(row["high"]),
                    "medium": as_int(row["medium"]),
                }
                for row in rows
            ]
        else:
            notes.append("department_name ausente: sin desglose por departamento")

        talent_action: dict[str, Any] | None = None
        action_rel = await resolve_optional_relation(
            scope,
            ACTION_CANDIDATES_DATASET,
            required=_ACTION_REQUIRED,
            optional=_ACTION_OPTIONAL,
        )
        if action_rel is not None and not action_rel.missing_required:
            severity_expr = action_rel.expr("severity", "MAX(severity)", "NULL::text")
            action_sql = f"""
                -- omega-aggregate: risk.attrition_risk_population.talent_action
                SELECT MAX(affected_count)::bigint AS affected_count,
                       {severity_expr} AS severity,
                       COUNT(*)::bigint AS action_rows
                  FROM {action_rel.sql}
                 WHERE {GOLD_SCOPE_PREDICATE}
                   AND action_id = $3
            """
            action = await scope.conn.fetchrow(
                action_sql, *scope.scope_args, RETENTION_ACTION_ID
            )
            action_rows = as_int(action["action_rows"]) or 0
            talent_action = {
                "action_id": RETENTION_ACTION_ID,
                "affected_count": as_int(action["affected_count"])
                if action_rows
                else 0,
                "severity": action["severity"] if action_rows else "low",
                "present": bool(action_rows),
            }
            evidence.append(
                gold_evidence(action_rel, filters={"action_id": RETENTION_ACTION_ID})
            )
            if not action_rows:
                notes.append(
                    "talent_action_candidates no emite fila talent_retention_risk: "
                    "Talent reporta 0 empleados en riesgo alto"
                )
        else:
            notes.append(
                "sap_successfactors_talent_action_candidates no disponible: "
                "sin cifra oficial de Talent (affected_count)"
            )

        return AttritionRiskPopulation(
            status=status_for(rel),
            evidence_refs=evidence,
            missing_columns=missing_optional_columns(rel, action_rel),
            notes=notes,
            total=as_int(bands["total"]),
            high=as_int(bands["high"]),
            medium=as_int(bands["medium"]),
            low=as_int(bands["low"]),
            insufficient_data=as_int(bands["insufficient_data"]),
            avg_score_valid=as_float(bands["avg_score_valid"]),
            departments_top=departments,
            talent_action=talent_action,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


# ── R3: employment end expiry (proxy for contract expiry) ───────────────────


@dataclass
class EmploymentEndExpiry(AggregateResult):
    as_of: date | None = None
    within_30: int | None = None
    within_60: int | None = None
    within_90: int | None = None
    active_filter_applied: bool = False
    sentinel_excluded_from: date = EMPLOYMENT_END_SENTINEL_DATE
    departments_top: list[dict[str, Any]] = field(default_factory=list)


async def query_employment_end_expiry(
    user: dict | None,
    *,
    top_n: int = 10,
    as_of: date | None = None,
) -> EmploymentEndExpiry:
    """COUNT employees whose employment end_date falls within 30/60/90 days."""
    top_n = clamp_top_n(top_n, default=10)
    today = as_of_date(as_of)
    d30, d60, d90 = (today + timedelta(days=days) for days in EXPIRY_WINDOWS_DAYS)
    base = {"proxy_note": EMPLOYMENT_END_PROXY_NOTE, "as_of": today}

    def _unavailable(error: str) -> EmploymentEndExpiry:
        return EmploymentEndExpiry(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> EmploymentEndExpiry:
        rel = await resolve_relation(
            scope,
            EMPLOYEE_360_DATASET,
            required=_EMPLOYEE_REQUIRED,
            optional=_EMPLOYEE_OPTIONAL,
        )
        if rel.missing_required:
            return EmploymentEndExpiry(
                status=STATUS_UNAVAILABLE,
                error=invalid_schema_error(rel),
                missing_columns=list(rel.missing_required),
                **base,
            )
        active_filter = rel.expr("is_active", "AND is_active IS TRUE", "")
        # $3 as_of, $4 +30d, $5 +60d, $6 +90d, $7 sentinel date (excluded from)
        population_predicate = f"""
               {GOLD_SCOPE_PREDICATE}
               AND end_date IS NOT NULL
               AND end_date::date < $7::date
               AND end_date::date > $3::date
               AND end_date::date <= $6::date
               {active_filter}
        """
        windows_sql = f"""
            -- omega-aggregate: risk.employment_end_expiry.windows
            SELECT COUNT(*) FILTER (WHERE end_date::date <= $4::date)::bigint AS within_30,
                   COUNT(*) FILTER (WHERE end_date::date <= $5::date)::bigint AS within_60,
                   COUNT(*)::bigint AS within_90
              FROM {rel.sql}
             WHERE {population_predicate}
        """
        args = (*scope.scope_args, today, d30, d60, d90, EMPLOYMENT_END_SENTINEL_DATE)
        windows = await scope.conn.fetchrow(windows_sql, *args)
        departments: list[dict[str, Any]] = []
        notes: list[str] = []
        if rel.has("department_name"):
            departments_sql = f"""
                -- omega-aggregate: risk.employment_end_expiry.departments
                SELECT department_name,
                       COUNT(*) FILTER (WHERE end_date::date <= $4::date)::bigint AS within_30,
                       COUNT(*) FILTER (WHERE end_date::date <= $5::date)::bigint AS within_60,
                       COUNT(*)::bigint AS within_90
                  FROM {rel.sql}
                 WHERE {population_predicate}
                 GROUP BY department_name
                 ORDER BY within_90 DESC, within_30 DESC, department_name
                 LIMIT $8
            """
            rows = await scope.conn.fetch(departments_sql, *args, top_n)
            departments = [
                {
                    "department_name": row["department_name"],
                    "within_30": as_int(row["within_30"]),
                    "within_60": as_int(row["within_60"]),
                    "within_90": as_int(row["within_90"]),
                }
                for row in rows
            ]
        else:
            notes.append("department_name ausente: sin desglose por departamento")
        if not rel.has("is_active"):
            notes.append(
                "is_active ausente: se cuentan todos los registros con end_date futuro"
            )
        return EmploymentEndExpiry(
            status=status_for(rel),
            evidence_refs=[
                gold_evidence(
                    rel,
                    filters={
                        "as_of": today,
                        "windows_days": list(EXPIRY_WINDOWS_DAYS),
                        "sentinel_excluded_from": EMPLOYMENT_END_SENTINEL_DATE,
                        "active_only": rel.has("is_active"),
                        "top_n": top_n,
                    },
                )
            ],
            missing_columns=missing_optional_columns(rel),
            notes=notes,
            within_30=as_int(windows["within_30"]),
            within_60=as_int(windows["within_60"]),
            within_90=as_int(windows["within_90"]),
            active_filter_applied=rel.has("is_active"),
            departments_top=departments,
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


# ── R4: deal slippage ────────────────────────────────────────────────────────


@dataclass
class DealSlippage(AggregateResult):
    as_of: date | None = None
    deals: int | None = None
    amount_total: float | None = None
    buckets: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_stage: list[dict[str, Any]] = field(default_factory=list)
    by_reason: list[dict[str, Any]] = field(default_factory=list)
    # Named deals: only when top_n > 0 (controlled exception, max 10 rows).
    top_deals: list[dict[str, Any]] = field(default_factory=list)


async def query_deal_slippage(
    user: dict | None,
    *,
    top_n: int = 0,
    as_of: date | None = None,
) -> DealSlippage:
    """Open Salesforce deals whose close_date is already in the past.

    Totals, overdue buckets, stages and risk reasons are aggregates. ``top_n``
    is a CONTROLLED EXCEPTION to the aggregates-only principle (Mission 2
    product decision): the default 0 returns aggregates only; a value above 0
    additionally returns up to ``MAX_NAMED_ROWS`` (10) named deals (opportunity
    name, stage, amount, days overdue). The seller (``vendedor``) is never
    returned.
    """
    top_n = clamp_named_rows(top_n)
    today = as_of_date(as_of)
    base = {"as_of": today}

    def _unavailable(error: str) -> DealSlippage:
        return DealSlippage(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> DealSlippage:
        rel = await resolve_relation(
            scope,
            DEALS_AT_RISK_DATASET,
            required=_DEALS_REQUIRED,
            optional=_DEALS_OPTIONAL,
        )
        if rel.missing_required:
            return DealSlippage(
                status=STATUS_UNAVAILABLE,
                error=invalid_schema_error(rel),
                missing_columns=list(rel.missing_required),
                **base,
            )
        has_amount = rel.has("amount")
        amount_sum = (
            "COALESCE(SUM(amount), 0)::float8" if has_amount else "NULL::float8"
        )
        overdue_days = "($3::date - close_date::date)"

        def bucket_amount(predicate: str) -> str:
            if not has_amount:
                return "NULL::float8"
            return f"COALESCE(SUM(amount) FILTER (WHERE {predicate}), 0)::float8"

        b1 = f"{overdue_days} BETWEEN 1 AND 30"
        b2 = f"{overdue_days} BETWEEN 31 AND 60"
        b3 = f"{overdue_days} > 60"
        totals_sql = f"""
            -- omega-aggregate: risk.deal_slippage.totals
            SELECT COUNT(*)::bigint AS deals,
                   {amount_sum} AS amount_total,
                   COUNT(*) FILTER (WHERE {b1})::bigint AS deals_1_30,
                   {bucket_amount(b1)} AS amount_1_30,
                   COUNT(*) FILTER (WHERE {b2})::bigint AS deals_31_60,
                   {bucket_amount(b2)} AS amount_31_60,
                   COUNT(*) FILTER (WHERE {b3})::bigint AS deals_over_60,
                   {bucket_amount(b3)} AS amount_over_60
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND close_date::date < $3::date
        """
        totals = await scope.conn.fetchrow(totals_sql, *scope.scope_args, today)
        notes = list(DEAL_SLIPPAGE_NOTES)
        if not has_amount:
            notes.append(
                "amount ausente: montos no calculables, desgloses ordenados por numero de deals"
            )

        by_stage: list[dict[str, Any]] = []
        if rel.has("stage_name"):
            stage_sql = f"""
                -- omega-aggregate: risk.deal_slippage.by_stage
                SELECT stage_name,
                       COUNT(*)::bigint AS deals,
                       {amount_sum} AS amount
                  FROM {rel.sql}
                 WHERE {GOLD_SCOPE_PREDICATE}
                   AND close_date::date < $3::date
                 GROUP BY stage_name
                 ORDER BY {"amount" if has_amount else "deals"} DESC NULLS LAST, stage_name
                 LIMIT $4
            """
            rows = await scope.conn.fetch(
                stage_sql, *scope.scope_args, today, BREAKDOWN_ROWS
            )
            by_stage = [
                {
                    "stage_name": row["stage_name"],
                    "deals": as_int(row["deals"]),
                    "amount": as_float(row["amount"]),
                }
                for row in rows
            ]
        else:
            notes.append("stage_name ausente: sin desglose por etapa")

        # Aggregates tell the slippage story (buckets, stages, risk reasons);
        # named deals below are opt-in only (top_n > 0).
        by_reason: list[dict[str, Any]] = []
        if rel.has("motivo_riesgo"):
            reason_sql = f"""
                -- omega-aggregate: risk.deal_slippage.by_reason
                SELECT motivo_riesgo AS risk_reason,
                       COUNT(*)::bigint AS deals,
                       {amount_sum} AS amount,
                       MAX({overdue_days})::int AS max_days_overdue
                  FROM {rel.sql}
                 WHERE {GOLD_SCOPE_PREDICATE}
                   AND close_date::date < $3::date
                 GROUP BY motivo_riesgo
                 ORDER BY {"amount" if has_amount else "deals"} DESC NULLS LAST, risk_reason
                 LIMIT $4
            """
            rows = await scope.conn.fetch(
                reason_sql, *scope.scope_args, today, BREAKDOWN_ROWS
            )
            by_reason = [
                {
                    "risk_reason": row["risk_reason"],
                    "deals": as_int(row["deals"]),
                    "amount": as_float(row["amount"]),
                    "max_days_overdue": as_int(row["max_days_overdue"]),
                }
                for row in rows
            ]
        else:
            notes.append("motivo_riesgo ausente: sin desglose por motivo de riesgo")

        top_deals: list[dict[str, Any]] = []
        if top_n:
            top_sql = f"""
                -- omega-aggregate: risk.deal_slippage.top_deals
                SELECT {rel.expr("opportunity_name", "opportunity_name", "NULL::text")} AS opportunity_name,
                       {rel.expr("stage_name", "stage_name", "NULL::text")} AS stage_name,
                       {rel.expr("amount", "amount::float8", "NULL::float8")} AS amount,
                       close_date::date AS close_date,
                       {overdue_days}::int AS days_overdue,
                       {rel.expr("motivo_riesgo", "motivo_riesgo", "NULL::text")} AS risk_reason
                  FROM {rel.sql}
                 WHERE {GOLD_SCOPE_PREDICATE}
                   AND close_date::date < $3::date
                 ORDER BY {"amount DESC NULLS LAST, days_overdue DESC" if has_amount else "days_overdue DESC"}
                 LIMIT $4
            """
            top_rows = await scope.conn.fetch(top_sql, *scope.scope_args, today, top_n)
            top_deals = [
                {
                    "opportunity_name": row["opportunity_name"],
                    "stage_name": row["stage_name"],
                    "amount": as_float(row["amount"]),
                    "close_date": row["close_date"],
                    "days_overdue": as_int(row["days_overdue"]),
                    "risk_reason": row["risk_reason"],
                }
                for row in top_rows
            ]
            notes.append(
                f"top_deals: {len(top_deals)} deals nombrados a peticion explicita (top_n)"
            )
        return DealSlippage(
            status=status_for(rel),
            evidence_refs=[
                gold_evidence(
                    rel,
                    filters={
                        "as_of": today,
                        "predicate": "close_date < as_of (dataset already excludes closed deals)",
                        "top_n": top_n,
                        "breakdown_limit": BREAKDOWN_ROWS,
                    },
                )
            ],
            missing_columns=missing_optional_columns(rel),
            notes=notes,
            deals=as_int(totals["deals"]),
            amount_total=as_float(totals["amount_total"]),
            buckets={
                "1_30": {
                    "deals": as_int(totals["deals_1_30"]),
                    "amount": as_float(totals["amount_1_30"]),
                },
                "31_60": {
                    "deals": as_int(totals["deals_31_60"]),
                    "amount": as_float(totals["amount_31_60"]),
                },
                "over_60": {
                    "deals": as_int(totals["deals_over_60"]),
                    "amount": as_float(totals["amount_over_60"]),
                },
            },
            by_stage=by_stage,
            by_reason=by_reason,
            top_deals=top_deals,
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


__all__ = [
    "ACTION_CANDIDATES_DATASET",
    "ATTRITION_NOTES",
    "AttritionRiskPopulation",
    "DEALS_AT_RISK_DATASET",
    "DEAL_SLIPPAGE_NOTES",
    "DealSlippage",
    "EMPLOYEE_360_DATASET",
    "EMPLOYMENT_END_PROXY_NOTE",
    "EMPLOYMENT_END_SENTINEL_DATE",
    "EmploymentEndExpiry",
    "RETENTION_RISK_DATASET",
    "query_attrition_risk_population",
    "query_deal_slippage",
    "query_employment_end_expiry",
]
