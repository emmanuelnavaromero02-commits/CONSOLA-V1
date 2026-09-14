"""Cap-free SQL aggregates for the OPERATIONS domain (Mission 1).

Functions:

* :func:`query_pipeline_health`             (console DB)  supported
* :func:`query_data_freshness_by_cartridge` (console DB)  partial: SLA is a parameter
* :func:`query_absence_rate_company_by_type` (Gold DB)    proxy: company level, not org unit

WHY TWO DSNs
------------
``pipeline_health`` and ``data_freshness`` read ``pipeline_runs`` and
``extraction_runs``. Those tables live in the console operational database
(``DATABASE_URL``, role ``omega_console``, created by ``infra/init/*.sql``),
not in the Gold lakehouse database (``GOLD_DATABASE_URL``, ``infra/init_gold``).
They are operational run logs, not published datasets: there is no
``dataset_publication_heads`` row for them, so the Talent head-resolution step
does not apply. Everything else of the contract is kept: the read runs on a
pooled connection (``auth.pool()``) inside a ``repeatable_read`` read-only
transaction, ``db_scope.scoped_db`` sets the ``app.tenant_id`` /
``app.workspace_id`` GUCs consumed by the RLS policies (``infra/init/99p_*``),
and every query repeats the workspace/tenant predicate explicitly
(``workspace_id = $1::uuid AND ($2::uuid IS NULL OR tenant_id = $2::uuid)``,
the same shape ``proactive_service`` uses).

``absence_rate_company_by_type`` reads Gold datasets (``sap_hcm``) through the
shared Gold scope, exactly like Finance and Risk.

Metrics without a supporting relation (shift coverage) intentionally have no
function here: see ``docs/data_gaps.md``.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

import asyncpg
from fastapi import HTTPException

from app.services import auth
from app.services.db_scope import scoped_db, workspace_scope_from_user
from app.services.intelligence.domain_aggregate_support import (
    GOLD_SCOPE_PREDICATE,
    MAX_GROUP_ROWS,
    STATUS_DEGRADED,
    STATUS_READY,
    STATUS_UNAVAILABLE,
    AggregateResult,
    GoldScope,
    as_float,
    as_int,
    as_of_date,
    as_of_datetime,
    business_days_in_month,
    clamp_top_n,
    gold_evidence,
    http_reason,
    invalid_schema_error,
    missing_optional_columns,
    month_start,
    resolve_optional_relation,
    resolve_relation,
    run_gold_aggregate,
    status_for,
)

ABSENCE_DATASET = "absence_by_type_and_month"
HEADCOUNT_DATASET = "headcount_by_department"

FRESHNESS_SLA_ENV = "OPERATIONS_FRESHNESS_SLA_HOURS"
DEFAULT_FRESHNESS_SLA_HOURS = 24.0
RECENT_FAILURES_LIMIT = 10

# Console run-log tables and the only columns referenced from them.
PIPELINE_RUNS_TABLE = "pipeline_runs"
EXTRACTION_RUNS_TABLE = "extraction_runs"
# Every console aggregate binds $1 = workspace_id (uuid) and $2 = tenant_id (uuid|NULL).
CONSOLE_SCOPE_PREDICATE = (
    "workspace_id = $1::uuid AND ($2::uuid IS NULL OR tenant_id = $2::uuid)"
)

_ABSENCE_REQUIRED = frozenset({"absence_month", "absence_type", "total_days_workable"})
_ABSENCE_OPTIONAL = frozenset({"employees_affected", "absence_records"})
_HEADCOUNT_REQUIRED = frozenset({"headcount"})

FRESHNESS_PROXY_NOTE = (
    "Horas transcurridas desde la ultima extraccion exitosa por cartucho, "
    "calculadas sobre extraction_runs y pipeline_runs de la base de console. "
    "NO mide cumplimiento de un SLA de negocio: OMEGA no tiene un SLA de "
    "frescura configurado y el umbral sla_hours es un parametro (o la variable "
    "OPERATIONS_FRESHNESS_SLA_HOURS). Solo SuccessFactors espeja sus corridas "
    "en pipeline_runs; los demas cartuchos registran en extraction_runs. Los "
    "cartuchos sin ninguna corrida exitosa visible NO aparecen en la lista."
)

ABSENCE_RATE_PROXY_NOTE = (
    "Tasa de ausentismo a nivel EMPRESA por tipo de ausencia: dias habiles de "
    "ausencia del ultimo mes cerrado (absence_by_type_and_month, SAP HCM PA2001) "
    "divididos entre headcount activo actual x dias habiles del mes. NO esta "
    "desglosada por unidad organizativa (el dataset gold de ausencias no trae "
    "org_unit y el puente pernr->org_unit es silver) y el headcount es el "
    "snapshot actual de headcount_by_department, no el del mes analizado."
)


# ── Console scope (pool + repeatable_read + scoped_db GUCs) ─────────────────


@dataclass
class ConsoleScope:
    conn: Any
    tenant_id: str | None
    workspace_id: str

    @property
    def scope_args(self) -> tuple[str, str | None]:
        """Positional args matching CONSOLE_SCOPE_PREDICATE ($1 workspace, $2 tenant)."""
        return (self.workspace_id, self.tenant_id or None)


@asynccontextmanager
async def open_console_scope(
    user: dict | None, pool: Any
) -> AsyncIterator[ConsoleScope]:
    """Pooled console connection with repeatable_read/readonly + RLS GUCs.

    ``scoped_db`` receives the raw connection (not the pool) so the GUC
    statement runs inside the transaction opened here; see module docstring.
    """
    tenant_id, workspace_id = workspace_scope_from_user(user)
    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            async with scoped_db(conn, tenant_id, workspace_id) as scoped:
                yield ConsoleScope(
                    conn=scoped, tenant_id=tenant_id, workspace_id=workspace_id
                )


ResultT = TypeVar("ResultT", bound=AggregateResult)


async def run_console_aggregate(
    user: dict | None,
    compute: Callable[[ConsoleScope], Awaitable[ResultT]],
    unavailable: Callable[[str], ResultT],
) -> ResultT:
    try:
        pool = await auth.pool()
        async with open_console_scope(user, pool) as scope:
            return await compute(scope)
    except HTTPException as exc:
        return unavailable(f"{http_reason(exc)}: {exc.detail}")
    except (asyncpg.PostgresError, OSError) as exc:
        return unavailable(f"unavailable: {exc}")


def _console_evidence(table: str, *, filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "console_table",
        "table": table,
        "database": "DATABASE_URL",
        "filters": filters,
    }


# ── O1: pipeline health ──────────────────────────────────────────────────────


@dataclass
class PipelineHealth(AggregateResult):
    as_of: datetime | None = None
    window_24h_start: datetime | None = None
    window_7d_start: datetime | None = None
    cartridges: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)
    cartridges_with_failures_24h: int | None = None
    recent_failures: list[dict[str, Any]] = field(default_factory=list)


def _health_sql(table: str) -> str:
    return f"""
        -- omega-aggregate: operations.pipeline_health.{table}
        SELECT cartridge_id,
               COUNT(*) FILTER (WHERE status = 'failed'  AND started_at >= $3)::bigint AS failed_24h,
               COUNT(*) FILTER (WHERE status = 'success' AND started_at >= $3)::bigint AS success_24h,
               COUNT(*) FILTER (WHERE status = 'failed')::bigint  AS failed_7d,
               COUNT(*) FILTER (WHERE status = 'success')::bigint AS success_7d,
               COUNT(*) FILTER (WHERE status = 'partial')::bigint AS partial_7d,
               MAX(finished_at) FILTER (WHERE status = 'failed') AS last_failed_at
          FROM {table}
         WHERE {CONSOLE_SCOPE_PREDICATE}
           AND started_at >= $4
         GROUP BY cartridge_id
         ORDER BY failed_7d DESC, cartridge_id
         LIMIT $5
    """


_RECENT_FAILURES_SQL = {
    PIPELINE_RUNS_TABLE: f"""
        -- omega-aggregate: operations.pipeline_health.recent_failures.pipeline_runs
        SELECT cartridge_id, dag_id AS source_ref, entity, finished_at, started_at,
               LEFT(error_message, 200) AS error_message
          FROM pipeline_runs
         WHERE {CONSOLE_SCOPE_PREDICATE}
           AND status = 'failed' AND started_at >= $3
         ORDER BY COALESCE(finished_at, started_at) DESC
         LIMIT $4
    """,
    EXTRACTION_RUNS_TABLE: f"""
        -- omega-aggregate: operations.pipeline_health.recent_failures.extraction_runs
        SELECT cartridge_id, run_type AS source_ref, entity_name AS entity, finished_at, started_at,
               LEFT(error_message, 200) AS error_message
          FROM extraction_runs
         WHERE {CONSOLE_SCOPE_PREDICATE}
           AND status = 'failed' AND started_at >= $3
         ORDER BY COALESCE(finished_at, started_at) DESC
         LIMIT $4
    """,
}


def _merge_health_rows(per_table: dict[str, list[Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for table, rows in per_table.items():
        for row in rows:
            cartridge = str(row["cartridge_id"])
            item = merged.setdefault(
                cartridge,
                {
                    "cartridge_id": cartridge,
                    "failed_24h": 0,
                    "success_24h": 0,
                    "failed_7d": 0,
                    "success_7d": 0,
                    "partial_7d": 0,
                    "last_failed_at": None,
                    "sources": [],
                },
            )
            for key in (
                "failed_24h",
                "success_24h",
                "failed_7d",
                "success_7d",
                "partial_7d",
            ):
                item[key] += as_int(row[key]) or 0
            last_failed = row["last_failed_at"]
            if last_failed is not None and (
                item["last_failed_at"] is None or last_failed > item["last_failed_at"]
            ):
                item["last_failed_at"] = last_failed
            item["sources"].append(table)
    out = []
    for item in merged.values():
        runs_7d = item["failed_7d"] + item["success_7d"]
        item["failure_rate_7d"] = (
            round(item["failed_7d"] / runs_7d, 4) if runs_7d > 0 else None
        )
        out.append(item)
    out.sort(key=lambda item: (-item["failed_7d"], item["cartridge_id"]))
    return out


async def query_pipeline_health(
    user: dict | None,
    *,
    as_of: datetime | None = None,
    recent_failures: int = RECENT_FAILURES_LIMIT,
) -> PipelineHealth:
    """Failed/successful runs per cartridge in the last 24h and 7d."""
    now = as_of_datetime(as_of)
    start_24h = now - timedelta(hours=24)
    start_7d = now - timedelta(days=7)
    recent_limit = clamp_top_n(recent_failures, default=RECENT_FAILURES_LIMIT)
    base = {"as_of": now, "window_24h_start": start_24h, "window_7d_start": start_7d}

    def _unavailable(error: str) -> PipelineHealth:
        return PipelineHealth(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: ConsoleScope) -> PipelineHealth:
        per_table: dict[str, list[Any]] = {}
        for table in (PIPELINE_RUNS_TABLE, EXTRACTION_RUNS_TABLE):
            per_table[table] = await scope.conn.fetch(
                _health_sql(table),
                *scope.scope_args,
                start_24h,
                start_7d,
                MAX_GROUP_ROWS,
            )
        cartridges = _merge_health_rows(per_table)
        failures: list[dict[str, Any]] = []
        for table, sql in _RECENT_FAILURES_SQL.items():
            rows = await scope.conn.fetch(
                sql, *scope.scope_args, start_7d, recent_limit
            )
            failures.extend(
                {
                    "source": table,
                    "cartridge_id": row["cartridge_id"],
                    "source_ref": row["source_ref"],
                    "entity": row["entity"],
                    "finished_at": row["finished_at"] or row["started_at"],
                    "error_message": row["error_message"],
                }
                for row in rows
            )
        failures.sort(key=lambda item: item["finished_at"] or now, reverse=True)
        totals = {
            key: sum(item[key] for item in cartridges)
            for key in (
                "failed_24h",
                "success_24h",
                "failed_7d",
                "success_7d",
                "partial_7d",
            )
        }
        filters = {
            "window_24h_start": now - timedelta(hours=24),
            "window_7d_start": start_7d,
            "as_of": now,
            "workspace_id": scope.workspace_id,
            "tenant_id": scope.tenant_id,
        }
        return PipelineHealth(
            status=STATUS_READY,
            evidence_refs=[
                _console_evidence(PIPELINE_RUNS_TABLE, filters=filters),
                _console_evidence(EXTRACTION_RUNS_TABLE, filters=filters),
            ],
            notes=[
                "solo SuccessFactors espeja extraction_runs en pipeline_runs; "
                "los conteos suman ambas tablas por cartucho"
            ],
            cartridges=cartridges,
            totals=totals,
            cartridges_with_failures_24h=sum(
                1 for item in cartridges if item["failed_24h"] > 0
            ),
            recent_failures=failures[:recent_limit],
            **base,
        )

    return await run_console_aggregate(user, _compute, _unavailable)


# ── O2: data freshness by cartridge ─────────────────────────────────────────


@dataclass
class DataFreshnessByCartridge(AggregateResult):
    as_of: datetime | None = None
    sla_hours: float | None = None
    sla_source: str | None = None  # param | env | default
    cartridges: list[dict[str, Any]] = field(default_factory=list)
    cartridges_count: int | None = None
    exceeding_sla: int | None = None


def resolve_sla_hours(sla_hours: float | None) -> tuple[float, str]:
    """Explicit parameter > OPERATIONS_FRESHNESS_SLA_HOURS env > default 24h."""
    if sla_hours is not None:
        try:
            value = float(sla_hours)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value, "param"
    raw = os.environ.get(FRESHNESS_SLA_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if value > 0:
            return value, "env"
    return DEFAULT_FRESHNESS_SLA_HOURS, "default"


def _freshness_sql(table: str) -> str:
    return f"""
        -- omega-aggregate: operations.data_freshness.{table}
        SELECT cartridge_id,
               MAX(finished_at) AS last_success_at,
               COUNT(*)::bigint AS success_runs
          FROM {table}
         WHERE {CONSOLE_SCOPE_PREDICATE}
           AND status = 'success'
         GROUP BY cartridge_id
         ORDER BY cartridge_id
         LIMIT $3
    """


async def query_data_freshness_by_cartridge(
    user: dict | None,
    *,
    sla_hours: float | None = None,
    as_of: datetime | None = None,
) -> DataFreshnessByCartridge:
    """Hours since the last successful run per cartridge vs an SLA threshold."""
    now = as_of_datetime(as_of)
    sla_value, sla_source = resolve_sla_hours(sla_hours)
    base = {
        "proxy_note": FRESHNESS_PROXY_NOTE,
        "as_of": now,
        "sla_hours": sla_value,
        "sla_source": sla_source,
    }

    def _unavailable(error: str) -> DataFreshnessByCartridge:
        return DataFreshnessByCartridge(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: ConsoleScope) -> DataFreshnessByCartridge:
        merged: dict[str, dict[str, Any]] = {}
        for table in (EXTRACTION_RUNS_TABLE, PIPELINE_RUNS_TABLE):
            rows = await scope.conn.fetch(
                _freshness_sql(table), *scope.scope_args, MAX_GROUP_ROWS
            )
            for row in rows:
                cartridge = str(row["cartridge_id"])
                item = merged.setdefault(
                    cartridge,
                    {
                        "cartridge_id": cartridge,
                        "last_success_at": None,
                        "success_runs": 0,
                        "sources": [],
                    },
                )
                last = row["last_success_at"]
                if last is not None and (
                    item["last_success_at"] is None or last > item["last_success_at"]
                ):
                    item["last_success_at"] = last
                item["success_runs"] += as_int(row["success_runs"]) or 0
                item["sources"].append(table)
        cartridges = []
        for item in merged.values():
            last = item["last_success_at"]
            hours = None
            if last is not None:
                hours = round((now - as_of_datetime(last)).total_seconds() / 3600.0, 2)
            item["hours_since_success"] = hours
            item["exceeds_sla"] = bool(hours is not None and hours > sla_value)
            cartridges.append(item)
        cartridges.sort(
            key=lambda item: (
                -(item["hours_since_success"] or 0.0),
                item["cartridge_id"],
            )
        )
        filters = {
            "status": "success",
            "as_of": now,
            "sla_hours": sla_value,
            "sla_source": sla_source,
            "workspace_id": scope.workspace_id,
            "tenant_id": scope.tenant_id,
        }
        return DataFreshnessByCartridge(
            status=STATUS_DEGRADED if sla_source == "default" else STATUS_READY,
            evidence_refs=[
                _console_evidence(EXTRACTION_RUNS_TABLE, filters=filters),
                _console_evidence(PIPELINE_RUNS_TABLE, filters=filters),
            ],
            notes=(
                ["sla_hours no configurado: se usa el default de 24h"]
                if sla_source == "default"
                else []
            ),
            cartridges=cartridges,
            cartridges_count=len(cartridges),
            exceeding_sla=sum(1 for item in cartridges if item["exceeds_sla"]),
            **base,
        )

    return await run_console_aggregate(user, _compute, _unavailable)


# ── O3: absence rate (company level, by type) ───────────────────────────────


@dataclass
class AbsenceRateCompanyByType(AggregateResult):
    period: date | None = None  # last closed month with data (first day)
    working_days: int | None = None
    headcount: int | None = None
    total_days_workable: float | None = None
    absence_rate: float | None = None
    types_count: int | None = None
    by_type: list[dict[str, Any]] = field(default_factory=list)


async def query_absence_rate_company_by_type(
    user: dict | None,
    *,
    top_n: int = 20,
    as_of: date | None = None,
) -> AbsenceRateCompanyByType:
    """Absence workable days for the last closed month, by type, over headcount."""
    top_n = clamp_top_n(top_n, default=20)
    current_month = month_start(as_of_date(as_of))
    base = {"proxy_note": ABSENCE_RATE_PROXY_NOTE}

    def _unavailable(error: str) -> AbsenceRateCompanyByType:
        return AbsenceRateCompanyByType(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> AbsenceRateCompanyByType:
        absence = await resolve_relation(
            scope,
            ABSENCE_DATASET,
            required=_ABSENCE_REQUIRED,
            optional=_ABSENCE_OPTIONAL,
        )
        if absence.missing_required:
            return AbsenceRateCompanyByType(
                status=STATUS_UNAVAILABLE,
                error=invalid_schema_error(absence),
                missing_columns=list(absence.missing_required),
                **base,
            )
        period_sql = f"""
            -- omega-aggregate: operations.absence_rate.period
            SELECT MAX(absence_month)::date AS period
              FROM {absence.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND absence_month::date < $3::date
        """
        period = await scope.conn.fetchval(period_sql, *scope.scope_args, current_month)
        evidence = [
            gold_evidence(
                absence,
                filters={
                    "before_month": current_month,
                    "period": period,
                    "top_n": top_n,
                },
            )
        ]
        notes: list[str] = []
        if period is None:
            return AbsenceRateCompanyByType(
                status=STATUS_DEGRADED,
                evidence_refs=evidence,
                missing_columns=missing_optional_columns(absence),
                notes=["sin meses cerrados con datos en absence_by_type_and_month"],
                **base,
            )
        affected_expr = absence.expr(
            "employees_affected", "SUM(employees_affected)::bigint", "NULL::bigint"
        )
        records_expr = absence.expr(
            "absence_records", "SUM(absence_records)::bigint", "NULL::bigint"
        )
        totals_sql = f"""
            -- omega-aggregate: operations.absence_rate.totals
            SELECT COALESCE(SUM(total_days_workable), 0)::float8 AS total_days_workable,
                   COUNT(DISTINCT absence_type)::bigint AS types_count
              FROM {absence.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND absence_month::date = $3::date
        """
        totals = await scope.conn.fetchrow(totals_sql, *scope.scope_args, period)
        by_type_sql = f"""
            -- omega-aggregate: operations.absence_rate.by_type
            SELECT absence_type,
                   COALESCE(SUM(total_days_workable), 0)::float8 AS days_workable,
                   {affected_expr} AS employees_affected,
                   {records_expr} AS absence_records
              FROM {absence.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND absence_month::date = $3::date
             GROUP BY absence_type
             ORDER BY days_workable DESC NULLS LAST, absence_type
             LIMIT $4
        """
        type_rows = await scope.conn.fetch(
            by_type_sql, *scope.scope_args, period, top_n
        )

        headcount_rel = await resolve_optional_relation(
            scope, HEADCOUNT_DATASET, required=_HEADCOUNT_REQUIRED
        )
        headcount: int | None = None
        if headcount_rel is None or headcount_rel.missing_required:
            notes.append(
                "headcount_by_department no disponible: absence_rate no calculable, "
                "solo dias de ausencia"
            )
        else:
            headcount_sql = f"""
                -- omega-aggregate: operations.absence_rate.headcount
                SELECT COALESCE(SUM(headcount), 0)::bigint AS headcount
                  FROM {headcount_rel.sql}
                 WHERE {GOLD_SCOPE_PREDICATE}
            """
            headcount = as_int(
                await scope.conn.fetchval(headcount_sql, *scope.scope_args)
            )
            evidence.append(
                gold_evidence(headcount_rel, filters={"snapshot": "current"})
            )

        working_days = business_days_in_month(period)
        total_days = as_float(totals["total_days_workable"]) or 0.0
        denominator = (headcount or 0) * working_days
        rate = round(total_days / denominator, 4) if denominator > 0 else None
        by_type = []
        for row in type_rows:
            days = as_float(row["days_workable"]) or 0.0
            by_type.append(
                {
                    "absence_type": row["absence_type"],
                    "days_workable": days,
                    "employees_affected": as_int(row["employees_affected"]),
                    "absence_records": as_int(row["absence_records"]),
                    "rate": round(days / denominator, 4) if denominator > 0 else None,
                }
            )
        status = status_for(absence)
        if headcount is None or (
            headcount_rel is not None and headcount_rel.missing_required
        ):
            status = STATUS_DEGRADED
        return AbsenceRateCompanyByType(
            status=status,
            evidence_refs=evidence,
            missing_columns=missing_optional_columns(absence),
            notes=notes,
            period=period,
            working_days=working_days,
            headcount=headcount,
            total_days_workable=total_days,
            absence_rate=rate,
            types_count=as_int(totals["types_count"]),
            by_type=by_type,
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


__all__ = [
    "ABSENCE_DATASET",
    "ABSENCE_RATE_PROXY_NOTE",
    "AbsenceRateCompanyByType",
    "CONSOLE_SCOPE_PREDICATE",
    "DataFreshnessByCartridge",
    "FRESHNESS_PROXY_NOTE",
    "FRESHNESS_SLA_ENV",
    "HEADCOUNT_DATASET",
    "PipelineHealth",
    "open_console_scope",
    "query_absence_rate_company_by_type",
    "query_data_freshness_by_cartridge",
    "query_pipeline_health",
    "resolve_sla_hours",
    "run_console_aggregate",
]
