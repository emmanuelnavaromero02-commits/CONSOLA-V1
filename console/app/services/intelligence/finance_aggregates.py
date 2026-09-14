"""Cap-free SQL aggregates for the FINANCE domain (Mission 1).

Datasets (layer=gold, replicon cartridge, resolved through publication heads):

* ``consultor_mensual``       -> :func:`query_billable_hours_logged`
  (honest proxy for "unbilled validated hours")
* ``costo_consultor_mensual`` -> :func:`query_labor_cost_by_department`
  (honest proxy for "payroll cost by org unit")
* ``pnl_mensual``             -> :func:`query_project_margin`

Every function returns a dataclass with ``status`` ready|degraded|unavailable,
the numbers, ``evidence_refs`` (relation, run, generation, snapshot, filters),
``supported=True`` and a ``proxy_note`` the LLM can quote so it never
over-promises. Metrics without a Gold relation (budget vs actual by cost
center) intentionally have no function here: see ``docs/data_gaps.md``.

Pattern (identical to ``successfactors_talent_population``): dedicated asyncpg
connection to GOLD_DATABASE_URL, repeatable_read read-only transaction,
``set_config('app.tenant_id'/'app.workspace_id')``, relation resolved via
``omega_publication.dataset_publication_heads`` + ``to_regclass`` + column
contract, ``WHERE workspace_id/tenant_id`` explicit in every query, every value
bound as ``$n``, COUNT/SUM in SQL, top-N bounded by ``MAX_GROUP_ROWS``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.services.intelligence.domain_aggregate_support import (
    DEFAULT_TOP_N,
    GOLD_SCOPE_PREDICATE,
    STATUS_DEGRADED,
    STATUS_UNAVAILABLE,
    AggregateResult,
    GoldScope,
    add_months,
    as_float,
    as_int,
    as_of_date,
    clamp_months,
    clamp_top_n,
    gold_evidence,
    invalid_schema_error,
    missing_optional_columns,
    month_start,
    resolve_relation,
    run_gold_aggregate,
    status_for,
)

CONSULTOR_MENSUAL_DATASET = "consultor_mensual"
COSTO_CONSULTOR_DATASET = "costo_consultor_mensual"
PNL_MENSUAL_DATASET = "pnl_mensual"

# Column allowlists: the only identifiers that ever reach SQL text.
_BILLABLE_REQUIRED = frozenset({"mes", "proyecto", "horas_facturables"})
_BILLABLE_OPTIONAL = frozenset(
    {"billing_rate_usd", "project_name", "cliente", "consultor"}
)

_LABOR_REQUIRED = frozenset({"mes", "departamento", "costo_ejecutado"})
_LABOR_OPTIONAL = frozenset(
    {"costo_hundido", "costo_potencial_mes", "horas_ejecutadas", "userid"}
)

_MARGIN_REQUIRED = frozenset(
    {"mes", "proyecto", "revenue_base_amount", "cost_direct_base_amount"}
)
_MARGIN_OPTIONAL = frozenset(
    {
        "cost_sunk_base_amount",
        "original_billing_amount",
        "original_currency",
        "project_name",
        "cliente",
        "tipo_proyecto",
        "horas_facturables",
    }
)

BILLABLE_HOURS_PROXY_NOTE = (
    "Mide horas marcadas como facturables (isbillable) registradas en Replicon "
    "por proyecto dentro de la ventana, valoradas a la tarifa efectiva "
    "(billing_rate_usd). NO mide horas validadas/aprobadas ni horas pendientes "
    "de facturar: el estado de aprobacion del timesheet y el enlace a factura "
    "no existen en la capa Gold (Timesheet e InvoiceItem son silver). El monto "
    "es una estimacion a tarifa, no facturacion emitida."
)

LABOR_COST_PROXY_NOTE = (
    "Costo laboral estimado de consultores en Replicon (horas ejecutadas x "
    "tarifa de costo por hora) agrupado por departamento Replicon para el "
    "ultimo mes cerrado con datos. NO es nomina: SAP HCM PA0008 (BasicPay) no "
    "esta extraido y workforce_cost_monthly publica total_base_salary NULL; la "
    "compensacion de SuccessFactors llega cifrada y no es agregable."
)

PROJECT_MARGIN_PROXY_NOTE = (
    "Margen = revenue_base_amount - (cost_direct_base_amount + "
    "cost_sunk_base_amount) por proyecto en la ventana, calculado sobre montos "
    "en moneda base SIN verificar: pnl_mensual.base_currency es NULL y "
    "financial_status nunca llega a 'ready', por lo que las columnas *_usd del "
    "dataset son NULL. NO es margen sobre facturacion emitida ni esta "
    "convertido a USD; original_billing_amount se reporta aparte sin "
    "conversion y original_currency viaja en evidence_refs."
)


def _window(as_of: date | None, months: int) -> tuple[date, date]:
    """Last ``months`` calendar months including the current one.

    Returns (start inclusive, end exclusive) first-of-month dates.
    """
    current = month_start(as_of_date(as_of))
    return add_months(current, -(months - 1)), add_months(current, 1)


# ── F1: billable hours logged (proxy for unbilled validated hours) ───────────


@dataclass
class BillableHoursLogged(AggregateResult):
    window_start: date | None = None
    window_end: date | None = None  # exclusive
    months: int = 1
    billable_hours: float | None = None
    billable_amount_usd: float | None = None
    projects_affected: int | None = None
    contributors: int | None = None
    top_projects: list[dict[str, Any]] = field(default_factory=list)


async def query_billable_hours_logged(
    user: dict | None,
    *,
    months: int = 1,
    top_n: int = DEFAULT_TOP_N,
    as_of: date | None = None,
) -> BillableHoursLogged:
    """SUM of billable hours (and hours x rate) per project over the window."""
    months = clamp_months(months)
    top_n = clamp_top_n(top_n)
    window_start, window_end = _window(as_of, months)
    base = {
        "proxy_note": BILLABLE_HOURS_PROXY_NOTE,
        "window_start": window_start,
        "window_end": window_end,
        "months": months,
    }

    def _unavailable(error: str) -> BillableHoursLogged:
        return BillableHoursLogged(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> BillableHoursLogged:
        rel = await resolve_relation(
            scope,
            CONSULTOR_MENSUAL_DATASET,
            required=_BILLABLE_REQUIRED,
            optional=_BILLABLE_OPTIONAL,
        )
        if rel.missing_required:
            return BillableHoursLogged(
                status=STATUS_UNAVAILABLE,
                error=invalid_schema_error(rel),
                missing_columns=list(rel.missing_required),
                **base,
            )
        amount_expr = rel.expr(
            "billing_rate_usd",
            "SUM(horas_facturables * billing_rate_usd)::float8",
            "NULL::float8",
        )
        contributors_expr = rel.expr(
            "consultor", "COUNT(DISTINCT consultor)::bigint", "NULL::bigint"
        )
        totals_sql = f"""
            -- omega-aggregate: finance.billable_hours_logged.totals
            SELECT COALESCE(SUM(horas_facturables), 0)::float8 AS billable_hours,
                   {amount_expr} AS billable_amount_usd,
                   COUNT(DISTINCT proyecto)::bigint AS projects_affected,
                   {contributors_expr} AS contributors
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND mes::date >= $3::date AND mes::date < $4::date
        """
        totals = await scope.conn.fetchrow(
            totals_sql, *scope.scope_args, window_start, window_end
        )
        order_by = (
            "billable_amount_usd" if rel.has("billing_rate_usd") else "billable_hours"
        )
        top_sql = f"""
            -- omega-aggregate: finance.billable_hours_logged.top_projects
            SELECT proyecto,
                   {rel.expr("project_name", "MAX(project_name)", "NULL::text")} AS project_name,
                   {rel.expr("cliente", "MAX(cliente)", "NULL::text")} AS cliente,
                   SUM(horas_facturables)::float8 AS billable_hours,
                   {amount_expr} AS billable_amount_usd
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND mes::date >= $3::date AND mes::date < $4::date
             GROUP BY proyecto
             ORDER BY {order_by} DESC NULLS LAST, proyecto
             LIMIT $5
        """
        top_rows = await scope.conn.fetch(
            top_sql, *scope.scope_args, window_start, window_end, top_n
        )
        notes: list[str] = []
        if not rel.has("billing_rate_usd"):
            notes.append(
                "billing_rate_usd ausente: billable_amount_usd no calculable, "
                "top_projects ordenado por horas"
            )
        return BillableHoursLogged(
            status=status_for(rel),
            evidence_refs=[
                gold_evidence(
                    rel,
                    filters={
                        "window_start": window_start,
                        "window_end_exclusive": window_end,
                        "months": months,
                        "top_n": top_n,
                    },
                )
            ],
            missing_columns=missing_optional_columns(rel),
            notes=notes,
            billable_hours=as_float(totals["billable_hours"]),
            billable_amount_usd=as_float(totals["billable_amount_usd"]),
            projects_affected=as_int(totals["projects_affected"]),
            contributors=as_int(totals["contributors"]),
            top_projects=[
                {
                    "proyecto": row["proyecto"],
                    "project_name": row["project_name"],
                    "cliente": row["cliente"],
                    "billable_hours": as_float(row["billable_hours"]),
                    "billable_amount_usd": as_float(row["billable_amount_usd"]),
                }
                for row in top_rows
            ],
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


# ── F3: labor cost by department (proxy for payroll cost by org unit) ────────


@dataclass
class LaborCostByDepartment(AggregateResult):
    period: date | None = None  # last closed month with data (first day)
    departments_count: int | None = None
    headcount: int | None = None
    total_hours: float | None = None
    total_cost: float | None = None
    total_sunk_cost: float | None = None
    total_potential_cost: float | None = None
    departments: list[dict[str, Any]] = field(default_factory=list)


async def query_labor_cost_by_department(
    user: dict | None,
    *,
    top_n: int = 20,
    as_of: date | None = None,
) -> LaborCostByDepartment:
    """SUM of executed/sunk labor cost per Replicon department for the last
    closed month (strictly before the month of ``as_of``) that has rows."""
    top_n = clamp_top_n(top_n, default=20)
    current_month = month_start(as_of_date(as_of))
    base = {"proxy_note": LABOR_COST_PROXY_NOTE}

    def _unavailable(error: str) -> LaborCostByDepartment:
        return LaborCostByDepartment(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> LaborCostByDepartment:
        rel = await resolve_relation(
            scope,
            COSTO_CONSULTOR_DATASET,
            required=_LABOR_REQUIRED,
            optional=_LABOR_OPTIONAL,
        )
        if rel.missing_required:
            return LaborCostByDepartment(
                status=STATUS_UNAVAILABLE,
                error=invalid_schema_error(rel),
                missing_columns=list(rel.missing_required),
                **base,
            )
        period_sql = f"""
            -- omega-aggregate: finance.labor_cost_by_department.period
            SELECT MAX(mes)::date AS period
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND mes::date < $3::date
        """
        period = await scope.conn.fetchval(period_sql, *scope.scope_args, current_month)
        evidence = [
            gold_evidence(
                rel,
                filters={
                    "before_month": current_month,
                    "period": period,
                    "top_n": top_n,
                },
            )
        ]
        if period is None:
            return LaborCostByDepartment(
                status=STATUS_DEGRADED,
                evidence_refs=evidence,
                missing_columns=missing_optional_columns(rel),
                notes=["sin meses cerrados con datos en costo_consultor_mensual"],
                **base,
            )
        hours_expr = rel.expr(
            "horas_ejecutadas", "SUM(horas_ejecutadas)::float8", "NULL::float8"
        )
        sunk_expr = rel.expr(
            "costo_hundido", "SUM(costo_hundido)::float8", "NULL::float8"
        )
        potential_expr = rel.expr(
            "costo_potencial_mes", "SUM(costo_potencial_mes)::float8", "NULL::float8"
        )
        headcount_expr = rel.expr(
            "userid", "COUNT(DISTINCT userid)::bigint", "NULL::bigint"
        )
        totals_sql = f"""
            -- omega-aggregate: finance.labor_cost_by_department.totals
            SELECT COUNT(DISTINCT departamento)::bigint AS departments_count,
                   {headcount_expr} AS headcount,
                   {hours_expr} AS total_hours,
                   COALESCE(SUM(costo_ejecutado), 0)::float8 AS total_cost,
                   {sunk_expr} AS total_sunk_cost,
                   {potential_expr} AS total_potential_cost
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND mes::date = $3::date
        """
        totals = await scope.conn.fetchrow(totals_sql, *scope.scope_args, period)
        departments_sql = f"""
            -- omega-aggregate: finance.labor_cost_by_department.departments
            SELECT departamento,
                   {headcount_expr} AS headcount,
                   {hours_expr} AS hours,
                   COALESCE(SUM(costo_ejecutado), 0)::float8 AS cost,
                   {sunk_expr} AS sunk_cost,
                   {potential_expr} AS potential_cost
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND mes::date = $3::date
             GROUP BY departamento
             ORDER BY cost DESC NULLS LAST, departamento
             LIMIT $4
        """
        rows = await scope.conn.fetch(departments_sql, *scope.scope_args, period, top_n)
        departments_count = as_int(totals["departments_count"])
        notes: list[str] = []
        if departments_count is not None and departments_count > len(rows):
            notes.append(
                f"departments muestra los {len(rows)} departamentos con mayor costo de "
                f"{departments_count}; los totales cubren la poblacion completa"
            )
        return LaborCostByDepartment(
            status=status_for(rel),
            evidence_refs=evidence,
            missing_columns=missing_optional_columns(rel),
            notes=notes,
            period=period,
            departments_count=departments_count,
            headcount=as_int(totals["headcount"]),
            total_hours=as_float(totals["total_hours"]),
            total_cost=as_float(totals["total_cost"]),
            total_sunk_cost=as_float(totals["total_sunk_cost"]),
            total_potential_cost=as_float(totals["total_potential_cost"]),
            departments=[
                {
                    "departamento": row["departamento"],
                    "headcount": as_int(row["headcount"]),
                    "hours": as_float(row["hours"]),
                    "cost": as_float(row["cost"]),
                    "sunk_cost": as_float(row["sunk_cost"]),
                    "potential_cost": as_float(row["potential_cost"]),
                }
                for row in rows
            ],
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


# ── F4: project margin ───────────────────────────────────────────────────────


@dataclass
class ProjectMargin(AggregateResult):
    window_start: date | None = None
    window_end: date | None = None  # exclusive
    months: int = 1
    projects_count: int | None = None
    total_revenue_base: float | None = None
    total_cost_direct: float | None = None
    total_cost_sunk: float | None = None
    total_margin: float | None = None
    total_margin_pct: float | None = None
    total_original_billing: float | None = None
    original_currencies: list[str] = field(default_factory=list)
    top_projects: list[dict[str, Any]] = field(default_factory=list)
    bottom_projects: list[dict[str, Any]] = field(default_factory=list)


def _project_row(row: Any) -> dict[str, Any]:
    return {
        "proyecto": row["proyecto"],
        "project_name": row["project_name"],
        "cliente": row["cliente"],
        "tipo_proyecto": row["tipo_proyecto"],
        "revenue_base": as_float(row["revenue_base"]),
        "cost_direct": as_float(row["cost_direct"]),
        "cost_sunk": as_float(row["cost_sunk"]),
        "margin": as_float(row["margin"]),
        "margin_pct": as_float(row["margin_pct"]),
        "billable_hours": as_float(row["billable_hours"]),
    }


async def query_project_margin(
    user: dict | None,
    *,
    months: int = 1,
    top_n: int = DEFAULT_TOP_N,
    as_of: date | None = None,
) -> ProjectMargin:
    """Margin per project (revenue base - direct - sunk cost) with top/bottom N."""
    months = clamp_months(months)
    top_n = clamp_top_n(top_n)
    window_start, window_end = _window(as_of, months)
    base = {
        "proxy_note": PROJECT_MARGIN_PROXY_NOTE,
        "window_start": window_start,
        "window_end": window_end,
        "months": months,
    }

    def _unavailable(error: str) -> ProjectMargin:
        return ProjectMargin(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> ProjectMargin:
        rel = await resolve_relation(
            scope,
            PNL_MENSUAL_DATASET,
            required=_MARGIN_REQUIRED,
            optional=_MARGIN_OPTIONAL,
        )
        if rel.missing_required:
            return ProjectMargin(
                status=STATUS_UNAVAILABLE,
                error=invalid_schema_error(rel),
                missing_columns=list(rel.missing_required),
                **base,
            )
        sunk_sum = rel.expr(
            "cost_sunk_base_amount", "COALESCE(SUM(cost_sunk_base_amount), 0)", "0"
        )
        sunk_out = rel.expr(
            "cost_sunk_base_amount",
            "SUM(cost_sunk_base_amount)::float8",
            "NULL::float8",
        )
        billing_expr = rel.expr(
            "original_billing_amount",
            "SUM(original_billing_amount)::float8",
            "NULL::float8",
        )
        currencies_expr = rel.expr(
            "original_currency",
            "array_agg(DISTINCT original_currency) FILTER (WHERE original_currency IS NOT NULL)",
            "NULL::text[]",
        )
        hours_expr = rel.expr(
            "horas_facturables", "SUM(horas_facturables)::float8", "NULL::float8"
        )
        margin_sql = (
            "(COALESCE(SUM(revenue_base_amount), 0) - COALESCE(SUM(cost_direct_base_amount), 0)"
            f" - {sunk_sum})"
        )
        totals_sql = f"""
            -- omega-aggregate: finance.project_margin.totals
            SELECT COUNT(DISTINCT proyecto)::bigint AS projects_count,
                   COALESCE(SUM(revenue_base_amount), 0)::float8 AS total_revenue_base,
                   COALESCE(SUM(cost_direct_base_amount), 0)::float8 AS total_cost_direct,
                   {sunk_out} AS total_cost_sunk,
                   {margin_sql}::float8 AS total_margin,
                   CASE WHEN COALESCE(SUM(revenue_base_amount), 0) > 0
                        THEN ({margin_sql} / SUM(revenue_base_amount) * 100)::float8
                   END AS total_margin_pct,
                   {billing_expr} AS total_original_billing,
                   {currencies_expr} AS original_currencies
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND mes::date >= $3::date AND mes::date < $4::date
        """
        totals = await scope.conn.fetchrow(
            totals_sql, *scope.scope_args, window_start, window_end
        )
        project_select = f"""
            SELECT proyecto,
                   {rel.expr("project_name", "MAX(project_name)", "NULL::text")} AS project_name,
                   {rel.expr("cliente", "MAX(cliente)", "NULL::text")} AS cliente,
                   {rel.expr("tipo_proyecto", "MAX(tipo_proyecto)", "NULL::text")} AS tipo_proyecto,
                   COALESCE(SUM(revenue_base_amount), 0)::float8 AS revenue_base,
                   COALESCE(SUM(cost_direct_base_amount), 0)::float8 AS cost_direct,
                   {sunk_out} AS cost_sunk,
                   {margin_sql}::float8 AS margin,
                   CASE WHEN COALESCE(SUM(revenue_base_amount), 0) > 0
                        THEN ({margin_sql} / SUM(revenue_base_amount) * 100)::float8
                   END AS margin_pct,
                   {hours_expr} AS billable_hours
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND mes::date >= $3::date AND mes::date < $4::date
             GROUP BY proyecto
        """
        top_sql = f"""
            -- omega-aggregate: finance.project_margin.top_projects
            {project_select}
             ORDER BY margin DESC NULLS LAST, proyecto
             LIMIT $5
        """
        bottom_sql = f"""
            -- omega-aggregate: finance.project_margin.bottom_projects
            {project_select}
             ORDER BY margin ASC NULLS LAST, proyecto
             LIMIT $5
        """
        top_rows = await scope.conn.fetch(
            top_sql, *scope.scope_args, window_start, window_end, top_n
        )
        bottom_rows = await scope.conn.fetch(
            bottom_sql, *scope.scope_args, window_start, window_end, top_n
        )
        currencies = [
            str(item)
            for item in (totals["original_currencies"] or [])
            if item is not None
        ]
        return ProjectMargin(
            status=status_for(rel),
            evidence_refs=[
                gold_evidence(
                    rel,
                    filters={
                        "window_start": window_start,
                        "window_end_exclusive": window_end,
                        "months": months,
                        "top_n": top_n,
                        "original_currencies": currencies,
                        "base_currency": "unverified (pnl_mensual.base_currency is NULL)",
                    },
                )
            ],
            missing_columns=missing_optional_columns(rel),
            projects_count=as_int(totals["projects_count"]),
            total_revenue_base=as_float(totals["total_revenue_base"]),
            total_cost_direct=as_float(totals["total_cost_direct"]),
            total_cost_sunk=as_float(totals["total_cost_sunk"]),
            total_margin=as_float(totals["total_margin"]),
            total_margin_pct=as_float(totals["total_margin_pct"]),
            total_original_billing=as_float(totals["total_original_billing"]),
            original_currencies=currencies,
            top_projects=[_project_row(row) for row in top_rows],
            bottom_projects=[_project_row(row) for row in bottom_rows],
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


__all__ = [
    "BILLABLE_HOURS_PROXY_NOTE",
    "BillableHoursLogged",
    "CONSULTOR_MENSUAL_DATASET",
    "COSTO_CONSULTOR_DATASET",
    "LABOR_COST_PROXY_NOTE",
    "LaborCostByDepartment",
    "PNL_MENSUAL_DATASET",
    "PROJECT_MARGIN_PROXY_NOTE",
    "ProjectMargin",
    "query_billable_hours_logged",
    "query_labor_cost_by_department",
    "query_project_margin",
]
