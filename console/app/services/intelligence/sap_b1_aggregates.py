from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.services.intelligence.domain_aggregate_support import (
    GOLD_SCOPE_PREDICATE,
    STATUS_DEGRADED,
    STATUS_UNAVAILABLE,
    AggregateResult,
    GoldScope,
    add_months,
    as_float,
    as_int,
    as_of_date,
    clamp_named_rows,
    clamp_top_n,
    gold_evidence,
    invalid_schema_error,
    month_start,
    resolve_relation,
    run_gold_aggregate,
    status_for,
)

CONSOLIDATED_DATASET = "sap_b1_margin_consolidated_month"
CUSTOMER_DATASET = "sap_b1_margin_by_customer_month"
COMPANY_DATASET = "sap_b1_margin_by_company_month"
ITEM_DATASET = "sap_b1_margin_by_item_month"
RECONCILIATION_DATASET = "sap_b1_margin_reconciliation_month"
DATA_QUALITY_DATASET = "sap_b1_data_quality"
SCORECARD_DATASET = "sap_b1_distributor_scorecard_month"
EXPIRY_DATASET = "sap_b1_batch_expiry"
COVERAGE_DATASET = "sap_b1_item_coverage"

_CONSOLIDATED_REQUIRED = frozenset(
    {
        "doc_month",
        "local_currency",
        "external_revenue_net_local",
        "external_gross_profit_local",
        "consolidated_gross_profit_local",
        "unrealized_profit_open_local",
        "unrealized_profit_close_local",
    }
)
_CUSTOMER_REQUIRED = frozenset(
    {
        "company",
        "doc_month",
        "scope",
        "card_code",
        "card_name",
        "revenue_net_local",
        "gross_profit_net_local",
        "min_margin_pct",
        "below_min",
        "negative_margin",
    }
)
_COMPANY_REQUIRED = frozenset(
    {"company", "doc_month", "scope", "revenue_net_local", "gross_profit_net_local", "min_margin_pct"}
)
_ITEM_REQUIRED = frozenset(
    {
        "company",
        "doc_month",
        "scope",
        "item_group_name",
        "revenue_net_local",
        "gross_profit_net_local",
        "below_min_revenue_local",
        "negative_margin_revenue_local",
    }
)
_RECONCILIATION_REQUIRED = frozenset(
    {
        "company",
        "doc_month",
        "status",
        "revenue_diff_pct",
        "cogs_diff_pct",
        "gross_profit_diff_pct",
        "tolerance_pct",
    }
)
_DATA_QUALITY_REQUIRED = frozenset(
    {"company", "check_code", "check_group", "total", "failing", "pct_ok", "min_pct", "status"}
)

_SCORECARD_REQUIRED = frozenset(
    {
        "distributor", "doc_month", "sell_out_revenue_local", "sell_out_qty", "sell_in_qty",
        "growth_mom_pct", "growth_yoy_pct", "sell_through_3m_pct", "channel_days", "margin_pct",
        "expiry_exposed_pct", "growth_color", "sell_through_color", "channel_days_color",
        "margin_color", "expiry_color", "overall_color",
    }
)
_EXPIRY_REQUIRED = frozenset(
    {"company", "item_code", "as_of_date", "bucket", "within_horizon", "qty", "value_local",
     "at_risk_qty", "at_risk_value_local", "transfer_candidate"}
)
_COVERAGE_REQUIRED = frozenset(
    {"company", "item_code", "as_of_date", "daily_consumption", "coverage_days", "coverage_with_orders_days",
     "lead_time_days", "stockout_date", "coverage_color", "suggested_qty", "suggested_action",
     "suggested_value_local", "order_by_date", "reorder_point", "b1_min_stock", "open_po_qty",
     "open_production_qty"}
)
MIN_STOCK_DRIFT = 0.2
_COLOR_METRICS = (
    ("growth_color", "crecimiento de sell-out", "growth_yoy_pct"),
    ("sell_through_color", "sell-through", "sell_through_3m_pct"),
    ("channel_days_color", "dias de inventario en canal", "channel_days"),
    ("margin_color", "margen de la distribuidora", "margin_pct"),
    ("expiry_color", "stock expuesto a caducidad", "expiry_exposed_pct"),
)

GROUP_MARGIN_DROP_PP = 3.0
RECONCILIATION_MONTHS = 12

GROUP_MARGIN_PROXY_NOTE = (
    "Margen bruto del grupo: venta externa de todas las empresas menos el costo "
    "del grupo, eliminando la utilidad intercompania que sigue en el inventario "
    "de la empresa compradora. NO convierte monedas: cada moneda local se "
    "reporta por separado y aqui se toma la de mayor venta."
)
COMPANY_MARGIN_PROXY_NOTE = (
    "Margen neto por empresa de la venta a clientes externos: facturas menos "
    "notas de credito en ingreso y costo, sin documentos cancelados. NO incluye "
    "ventas entre empresas del grupo ni costos que no pasan por documentos."
)
CUSTOMER_MARGIN_PROXY_NOTE = (
    "Clientes externos del ultimo mes cerrado contra el margen minimo "
    "configurado: cuantos quedan por debajo, que parte de la venta representan y "
    "cuantos tienen margen negativo. NO une clientes entre empresas sin un RFC "
    "valido."
)
ITEM_FAMILY_MARGIN_PROXY_NOTE = (
    "Margen y mezcla de venta externa por grupo de articulo del ultimo mes "
    "cerrado. NO usa jerarquias de producto que no esten en el grupo de "
    "articulo de Business One."
)
BELOW_MIN_PROXY_NOTE = (
    "Parte de la venta externa del ultimo mes cerrado en lineas con margen por "
    "debajo del minimo configurado y en lineas vendidas bajo costo. NO evalua "
    "notas de credito linea por linea."
)
RECONCILIATION_PROXY_NOTE = (
    "Meses cerrados por empresa comparados con los totales de control de "
    "finanzas: dentro de tolerancia, fuera de tolerancia o sin totales de "
    "control. NO sustituye el cierre contable: explica la diferencia entre "
    "documentos y contabilidad por desfase y por costos fuera de documentos."
)
DATA_QUALITY_PROXY_NOTE = (
    "Revisiones de calidad por empresa: RFC de clientes, relaciones completas "
    "entre documentos y maestros, costo en lineas de articulo, lotes con "
    "caducidad e intercompania. NO corrige datos en Business One."
)


@dataclass
class B1Result(AggregateResult):
    period: str | None = None
    breaches: list[str] = field(default_factory=list)


def _period_label(value: date | None) -> str | None:
    return value.strftime("%Y-%m") if value else None


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return round(100.0 * numerator / denominator, 2)


async def _closed_period(scope: GoldScope, rel: Any, as_of: date | None, marker: str) -> date | None:
    sql = f"""
        -- omega-aggregate: sap_b1.{marker}.closed_period
        SELECT MAX(doc_month)::date AS period
          FROM {rel.sql}
         WHERE {GOLD_SCOPE_PREDICATE}
           AND doc_month::date < $3::date
    """
    row = await scope.conn.fetchrow(sql, *scope.scope_args, month_start(as_of_date(as_of)))
    return row["period"] if row else None


def _no_period(result_type: type, base: dict[str, Any], rel: Any) -> Any:
    return result_type(
        status=STATUS_DEGRADED,
        notes=["sin meses cerrados con datos"],
        evidence_refs=[gold_evidence(rel, filters={})],
        **base,
    )


@dataclass
class GroupMargin(B1Result):
    currency: str | None = None
    external_revenue: float | None = None
    external_gross_profit: float | None = None
    consolidated_gross_profit: float | None = None
    external_margin_pct: float | None = None
    consolidated_margin_pct: float | None = None
    unrealized_profit_change: float | None = None
    trailing_margin_pct: float | None = None
    currencies: list[str] = field(default_factory=list)


async def query_group_margin(user: dict | None, *, as_of: date | None = None) -> GroupMargin:
    base = {"proxy_note": GROUP_MARGIN_PROXY_NOTE}

    def _unavailable(error: str) -> GroupMargin:
        return GroupMargin(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> GroupMargin:
        rel = await resolve_relation(scope, CONSOLIDATED_DATASET, required=_CONSOLIDATED_REQUIRED)
        if rel.missing_required:
            return GroupMargin(status=STATUS_UNAVAILABLE, error=invalid_schema_error(rel),
                               missing_columns=list(rel.missing_required), **base)
        sql = f"""
            -- omega-aggregate: sap_b1.group_margin.months
            SELECT doc_month::date AS doc_month,
                   local_currency,
                   SUM(external_revenue_net_local)::float8 AS revenue,
                   SUM(external_gross_profit_local)::float8 AS external_gp,
                   SUM(consolidated_gross_profit_local)::float8 AS consolidated_gp,
                   SUM(unrealized_profit_close_local - unrealized_profit_open_local)::float8 AS up_change
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date < $3::date
             GROUP BY 1, 2
             ORDER BY 1 DESC, 2
             LIMIT $4
        """
        rows = await scope.conn.fetch(
            sql, *scope.scope_args, month_start(as_of_date(as_of)), clamp_top_n(16, upper=16)
        )
        if not rows:
            return _no_period(GroupMargin, base, rel)
        revenue_by_currency: dict[str, float] = {}
        for row in rows:
            key = str(row["local_currency"] or "")
            revenue_by_currency[key] = revenue_by_currency.get(key, 0.0) + (as_float(row["revenue"]) or 0.0)
        currency = max(revenue_by_currency, key=lambda item: revenue_by_currency[item])
        months = [row for row in rows if str(row["local_currency"] or "") == currency]
        latest, trailing = months[0], months[1:4]
        revenue = as_float(latest["revenue"])
        consolidated = as_float(latest["consolidated_gp"])
        margin = _pct(consolidated, revenue)
        trailing_margins = [
            value for value in (_pct(as_float(r["consolidated_gp"]), as_float(r["revenue"])) for r in trailing)
            if value is not None
        ]
        trailing_margin = round(sum(trailing_margins) / len(trailing_margins), 2) if trailing_margins else None
        period = _period_label(latest["doc_month"])
        breaches: list[str] = []
        if margin is not None and trailing_margin is not None and margin < trailing_margin - GROUP_MARGIN_DROP_PP:
            breaches.append(
                f"El margen del grupo de {period} fue {margin}% y cayo "
                f"{round(trailing_margin - margin, 2)} puntos contra el promedio de los tres meses previos ({trailing_margin}%)."
            )
        notes = [] if len(revenue_by_currency) == 1 else ["hay varias monedas locales: se reporta la de mayor venta"]
        return GroupMargin(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": period})],
            notes=notes,
            period=period,
            breaches=breaches,
            currency=currency or None,
            external_revenue=revenue,
            external_gross_profit=as_float(latest["external_gp"]),
            consolidated_gross_profit=consolidated,
            external_margin_pct=_pct(as_float(latest["external_gp"]), revenue),
            consolidated_margin_pct=margin,
            unrealized_profit_change=as_float(latest["up_change"]),
            trailing_margin_pct=trailing_margin,
            currencies=sorted(item for item in revenue_by_currency if item),
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


@dataclass
class CompanyMargin(B1Result):
    companies: list[dict[str, Any]] = field(default_factory=list)


async def query_company_margin(user: dict | None, *, as_of: date | None = None) -> CompanyMargin:
    base = {"proxy_note": COMPANY_MARGIN_PROXY_NOTE}

    def _unavailable(error: str) -> CompanyMargin:
        return CompanyMargin(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> CompanyMargin:
        rel = await resolve_relation(scope, COMPANY_DATASET, required=_COMPANY_REQUIRED)
        if rel.missing_required:
            return CompanyMargin(status=STATUS_UNAVAILABLE, error=invalid_schema_error(rel),
                                 missing_columns=list(rel.missing_required), **base)
        period = await _closed_period(scope, rel, as_of, "company_margin")
        if period is None:
            return _no_period(CompanyMargin, base, rel)
        sql = f"""
            -- omega-aggregate: sap_b1.company_margin.companies
            SELECT company,
                   SUM(revenue_net_local)::float8 AS revenue,
                   SUM(gross_profit_net_local)::float8 AS gross_profit,
                   MAX(min_margin_pct)::float8 AS min_margin_pct
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date = $3::date
               AND scope = 'external'
             GROUP BY company
             ORDER BY company
             LIMIT $4
        """
        rows = await scope.conn.fetch(sql, *scope.scope_args, period, clamp_top_n(50))
        label = _period_label(period)
        companies, breaches = [], []
        for row in rows:
            margin = _pct(as_float(row["gross_profit"]), as_float(row["revenue"]))
            minimum = as_float(row["min_margin_pct"])
            companies.append({
                "company": row["company"],
                "revenue": as_float(row["revenue"]),
                "gross_profit": as_float(row["gross_profit"]),
                "margin_pct": margin,
                "min_margin_pct": minimum,
            })
            if margin is not None and minimum is not None and margin < minimum:
                breaches.append(
                    f"La empresa {row['company']} cerro {label} con margen de {margin}%, "
                    f"por debajo del minimo de {minimum}%."
                )
        return CompanyMargin(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label})],
            period=label,
            breaches=breaches,
            companies=companies,
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


@dataclass
class CustomerMargin(B1Result):
    customers: int | None = None
    customers_below_min: int | None = None
    customers_negative: int | None = None
    revenue: float | None = None
    revenue_below_min_pct: float | None = None
    worst_customers: list[dict[str, Any]] = field(default_factory=list)


async def query_customer_margin(
    user: dict | None, *, as_of: date | None = None, top_n: int = 0
) -> CustomerMargin:
    top_n = clamp_named_rows(top_n)
    base = {"proxy_note": CUSTOMER_MARGIN_PROXY_NOTE}

    def _unavailable(error: str) -> CustomerMargin:
        return CustomerMargin(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> CustomerMargin:
        rel = await resolve_relation(scope, CUSTOMER_DATASET, required=_CUSTOMER_REQUIRED)
        if rel.missing_required:
            return CustomerMargin(status=STATUS_UNAVAILABLE, error=invalid_schema_error(rel),
                                  missing_columns=list(rel.missing_required), **base)
        period = await _closed_period(scope, rel, as_of, "customer_margin")
        if period is None:
            return _no_period(CustomerMargin, base, rel)
        totals_sql = f"""
            -- omega-aggregate: sap_b1.customer_margin.totals
            SELECT COUNT(*)::bigint AS customers,
                   COUNT(*) FILTER (WHERE below_min)::bigint AS below_min,
                   COUNT(*) FILTER (WHERE negative_margin)::bigint AS negative,
                   COALESCE(SUM(revenue_net_local), 0)::float8 AS revenue,
                   COALESCE(SUM(revenue_net_local) FILTER (WHERE below_min), 0)::float8 AS revenue_below_min
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date = $3::date
               AND scope = 'external'
        """
        totals = await scope.conn.fetchrow(totals_sql, *scope.scope_args, period)
        worst: list[Any] = []
        if top_n:
            worst_sql = f"""
                -- omega-aggregate: sap_b1.customer_margin.worst
                SELECT company, card_name,
                       SUM(revenue_net_local)::float8 AS revenue,
                       SUM(gross_profit_net_local)::float8 AS gross_profit
                  FROM {rel.sql}
                 WHERE {GOLD_SCOPE_PREDICATE}
                   AND doc_month::date = $3::date
                   AND scope = 'external'
                 GROUP BY company, card_code, card_name
                HAVING SUM(revenue_net_local) <> 0
                 ORDER BY SUM(gross_profit_net_local) / SUM(revenue_net_local), company, card_code
                 LIMIT $4
            """
            worst = await scope.conn.fetch(worst_sql, *scope.scope_args, period, top_n)
        label = _period_label(period)
        below = as_int(totals["below_min"]) or 0
        negative = as_int(totals["negative"]) or 0
        revenue = as_float(totals["revenue"])
        below_pct = _pct(as_float(totals["revenue_below_min"]), revenue)
        breaches = []
        if negative:
            breaches.append(f"{negative} clientes externos cerraron {label} con margen negativo.")
        if below:
            breaches.append(
                f"{below} clientes externos quedaron bajo el margen minimo en {label} "
                f"({below_pct}% de la venta del mes)."
            )
        return CustomerMargin(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label, "top_n": top_n})],
            period=label,
            breaches=breaches,
            customers=as_int(totals["customers"]),
            customers_below_min=below,
            customers_negative=negative,
            revenue=revenue,
            revenue_below_min_pct=below_pct,
            worst_customers=[
                {
                    "company": row["company"],
                    "customer": row["card_name"],
                    "revenue": as_float(row["revenue"]),
                    "margin_pct": _pct(as_float(row["gross_profit"]), as_float(row["revenue"])),
                }
                for row in worst
            ],
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


@dataclass
class ItemFamilyMargin(B1Result):
    families: list[dict[str, Any]] = field(default_factory=list)


async def query_item_family_margin(
    user: dict | None, *, as_of: date | None = None, top_n: int = 10
) -> ItemFamilyMargin:
    limit = clamp_top_n(top_n, default=10)
    base = {"proxy_note": ITEM_FAMILY_MARGIN_PROXY_NOTE}

    def _unavailable(error: str) -> ItemFamilyMargin:
        return ItemFamilyMargin(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> ItemFamilyMargin:
        rel = await resolve_relation(scope, ITEM_DATASET, required=_ITEM_REQUIRED)
        if rel.missing_required:
            return ItemFamilyMargin(status=STATUS_UNAVAILABLE, error=invalid_schema_error(rel),
                                    missing_columns=list(rel.missing_required), **base)
        period = await _closed_period(scope, rel, as_of, "item_family_margin")
        if period is None:
            return _no_period(ItemFamilyMargin, base, rel)
        sql = f"""
            -- omega-aggregate: sap_b1.item_family_margin.families
            SELECT item_group_name,
                   SUM(revenue_net_local)::float8 AS revenue,
                   SUM(gross_profit_net_local)::float8 AS gross_profit,
                   SUM(SUM(revenue_net_local)) OVER ()::float8 AS total_revenue
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date = $3::date
               AND scope = 'external'
             GROUP BY item_group_name
             ORDER BY SUM(revenue_net_local) DESC, item_group_name
             LIMIT $4
        """
        rows = await scope.conn.fetch(sql, *scope.scope_args, period, limit)
        label = _period_label(period)
        families, breaches = [], []
        for row in rows:
            gross_profit = as_float(row["gross_profit"])
            families.append({
                "family": row["item_group_name"],
                "revenue": as_float(row["revenue"]),
                "gross_profit": gross_profit,
                "margin_pct": _pct(gross_profit, as_float(row["revenue"])),
                "mix_pct": _pct(as_float(row["revenue"]), as_float(row["total_revenue"])),
            })
            if gross_profit is not None and gross_profit < 0:
                breaches.append(f"La familia {row['item_group_name']} cerro {label} con margen negativo.")
        return ItemFamilyMargin(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label, "top_n": limit})],
            period=label,
            breaches=breaches,
            families=families,
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


@dataclass
class BelowMinSales(B1Result):
    revenue: float | None = None
    below_min_revenue: float | None = None
    below_min_pct: float | None = None
    below_cost_revenue: float | None = None
    below_cost_pct: float | None = None


async def query_below_min_sales(user: dict | None, *, as_of: date | None = None) -> BelowMinSales:
    base = {"proxy_note": BELOW_MIN_PROXY_NOTE}

    def _unavailable(error: str) -> BelowMinSales:
        return BelowMinSales(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> BelowMinSales:
        rel = await resolve_relation(scope, ITEM_DATASET, required=_ITEM_REQUIRED)
        if rel.missing_required:
            return BelowMinSales(status=STATUS_UNAVAILABLE, error=invalid_schema_error(rel),
                                 missing_columns=list(rel.missing_required), **base)
        period = await _closed_period(scope, rel, as_of, "below_min_sales")
        if period is None:
            return _no_period(BelowMinSales, base, rel)
        sql = f"""
            -- omega-aggregate: sap_b1.below_min_sales.totals
            SELECT COALESCE(SUM(revenue_net_local), 0)::float8 AS revenue,
                   COALESCE(SUM(below_min_revenue_local), 0)::float8 AS below_min,
                   COALESCE(SUM(negative_margin_revenue_local), 0)::float8 AS below_cost
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date = $3::date
               AND scope = 'external'
        """
        row = await scope.conn.fetchrow(sql, *scope.scope_args, period)
        label = _period_label(period)
        revenue = as_float(row["revenue"])
        below_cost = as_float(row["below_cost"])
        breaches = []
        if below_cost:
            breaches.append(
                f"En {label} se facturaron {round(below_cost, 2)} de venta externa por debajo del costo."
            )
        return BelowMinSales(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label})],
            period=label,
            breaches=breaches,
            revenue=revenue,
            below_min_revenue=as_float(row["below_min"]),
            below_min_pct=_pct(as_float(row["below_min"]), revenue),
            below_cost_revenue=below_cost,
            below_cost_pct=_pct(below_cost, revenue),
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


@dataclass
class Reconciliation(B1Result):
    months: int = RECONCILIATION_MONTHS
    company_months: int | None = None
    within_tolerance: int | None = None
    out_of_tolerance: int | None = None
    without_controls: int | None = None
    outliers: list[dict[str, Any]] = field(default_factory=list)


async def query_reconciliation(user: dict | None, *, as_of: date | None = None) -> Reconciliation:
    base = {"proxy_note": RECONCILIATION_PROXY_NOTE}
    window_end = month_start(as_of_date(as_of))
    window_start = add_months(window_end, -RECONCILIATION_MONTHS)

    def _unavailable(error: str) -> Reconciliation:
        return Reconciliation(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> Reconciliation:
        rel = await resolve_relation(scope, RECONCILIATION_DATASET, required=_RECONCILIATION_REQUIRED)
        if rel.missing_required:
            return Reconciliation(status=STATUS_UNAVAILABLE, error=invalid_schema_error(rel),
                                  missing_columns=list(rel.missing_required), **base)
        totals_sql = f"""
            -- omega-aggregate: sap_b1.reconciliation.totals
            SELECT COUNT(*)::bigint AS company_months,
                   COUNT(*) FILTER (WHERE status = 'ok')::bigint AS within,
                   COUNT(*) FILTER (WHERE status = 'fuera_tolerancia')::bigint AS outside,
                   COUNT(*) FILTER (WHERE status = 'sin_control')::bigint AS without
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date >= $3::date AND doc_month::date < $4::date
        """
        totals = await scope.conn.fetchrow(totals_sql, *scope.scope_args, window_start, window_end)
        outliers_sql = f"""
            -- omega-aggregate: sap_b1.reconciliation.outliers
            SELECT company, doc_month::date AS doc_month,
                   revenue_diff_pct::float8 AS revenue_diff_pct,
                   cogs_diff_pct::float8 AS cogs_diff_pct,
                   gross_profit_diff_pct::float8 AS gross_profit_diff_pct,
                   tolerance_pct::float8 AS tolerance_pct
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date >= $3::date AND doc_month::date < $4::date
               AND status = 'fuera_tolerancia'
             ORDER BY doc_month DESC, company
             LIMIT $5
        """
        rows = await scope.conn.fetch(outliers_sql, *scope.scope_args, window_start, window_end, clamp_top_n(10))
        outliers, breaches = [], []
        for row in rows:
            label = _period_label(row["doc_month"])
            outliers.append({
                "company": row["company"],
                "period": label,
                "revenue_diff_pct": as_float(row["revenue_diff_pct"]),
                "cogs_diff_pct": as_float(row["cogs_diff_pct"]),
                "gross_profit_diff_pct": as_float(row["gross_profit_diff_pct"]),
                "tolerance_pct": as_float(row["tolerance_pct"]),
            })
            breaches.append(
                f"La reconciliacion de {row['company']} en {label} esta fuera de tolerancia "
                f"(ingreso {as_float(row['revenue_diff_pct'])}%, utilidad bruta {as_float(row['gross_profit_diff_pct'])}%)."
            )
        notes = []
        without = as_int(totals["without"]) or 0
        if without and without == (as_int(totals["company_months"]) or 0):
            notes.append("finanzas todavia no entrego totales de control")
        return Reconciliation(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"window_start": window_start, "window_end_exclusive": window_end})],
            notes=notes,
            breaches=breaches,
            company_months=as_int(totals["company_months"]),
            within_tolerance=as_int(totals["within"]),
            out_of_tolerance=as_int(totals["outside"]),
            without_controls=without,
            outliers=outliers,
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


@dataclass
class DataQuality(B1Result):
    checks: int | None = None
    checks_below_min: int | None = None
    failing: list[dict[str, Any]] = field(default_factory=list)


async def query_data_quality(user: dict | None) -> DataQuality:
    base = {"proxy_note": DATA_QUALITY_PROXY_NOTE}

    def _unavailable(error: str) -> DataQuality:
        return DataQuality(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> DataQuality:
        rel = await resolve_relation(scope, DATA_QUALITY_DATASET, required=_DATA_QUALITY_REQUIRED)
        if rel.missing_required:
            return DataQuality(status=STATUS_UNAVAILABLE, error=invalid_schema_error(rel),
                               missing_columns=list(rel.missing_required), **base)
        totals_sql = f"""
            -- omega-aggregate: sap_b1.data_quality.totals
            SELECT COUNT(*)::bigint AS checks,
                   COUNT(*) FILTER (WHERE status = 'bajo_umbral')::bigint AS below
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
        """
        totals = await scope.conn.fetchrow(totals_sql, *scope.scope_args)
        failing_sql = f"""
            -- omega-aggregate: sap_b1.data_quality.failing
            SELECT company, check_code, check_group,
                   total::bigint AS total, failing::bigint AS failing,
                   pct_ok::float8 AS pct_ok, min_pct::float8 AS min_pct
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND status = 'bajo_umbral'
             ORDER BY pct_ok, company, check_code
             LIMIT $3
        """
        rows = await scope.conn.fetch(failing_sql, *scope.scope_args, clamp_top_n(10))
        failing, breaches = [], []
        for row in rows:
            failing.append({
                "company": row["company"],
                "check": row["check_code"].replace("_", " "),
                "group": row["check_group"],
                "total": as_int(row["total"]),
                "failing": as_int(row["failing"]),
                "pct_ok": as_float(row["pct_ok"]),
                "min_pct": as_float(row["min_pct"]),
            })
            breaches.append(
                f"Calidad de datos en {row['company']}: {row['check_code'].replace('_', ' ')} "
                f"al {as_float(row['pct_ok'])}% (minimo {as_float(row['min_pct'])}%)."
            )
        return DataQuality(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={})],
            breaches=breaches,
            checks=as_int(totals["checks"]),
            checks_below_min=as_int(totals["below"]),
            failing=failing,
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


SCORECARD_PROXY_NOTE = (
    "Semaforo mensual por distribuidora del grupo: sell-in desde el grupo, "
    "sell-out a clientes externos y su crecimiento, sell-through de tres meses, "
    "dias de inventario en canal, margen de la distribuidora y stock expuesto a "
    "caducidad, cada uno contra su umbral. NO identifica distribuidoras por "
    "nombre de empresa sino por quien compra al grupo."
)
EXPIRY_PROXY_NOTE = (
    "Lotes con existencia al corte del inventario: vencidos, dentro del horizonte "
    "de caducidad y unidades que se venceran sin venderse al ritmo de venta de "
    "90 dias con salida por fecha de caducidad. NO considera promociones ni "
    "traspasos ya en camino."
)


@dataclass
class DistributorScorecard(B1Result):
    distributors: list[dict[str, Any]] = field(default_factory=list)
    red: int | None = None
    yellow: int | None = None
    green: int | None = None
    without_thresholds: int | None = None
    sell_in_qty: float | None = None
    sell_out_qty: float | None = None


async def query_distributor_scorecard(user: dict | None, *, as_of: date | None = None) -> DistributorScorecard:
    base = {"proxy_note": SCORECARD_PROXY_NOTE}

    def _unavailable(error: str) -> DistributorScorecard:
        return DistributorScorecard(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> DistributorScorecard:
        rel = await resolve_relation(scope, SCORECARD_DATASET, required=_SCORECARD_REQUIRED)
        if rel.missing_required:
            return DistributorScorecard(status=STATUS_UNAVAILABLE, error=invalid_schema_error(rel),
                                        missing_columns=list(rel.missing_required), **base)
        period = await _closed_period(scope, rel, as_of, "distributor_scorecard")
        if period is None:
            return _no_period(DistributorScorecard, base, rel)
        columns = ", ".join(sorted(_SCORECARD_REQUIRED - {"doc_month"}))
        sql = f"""
            -- omega-aggregate: sap_b1.distributor_scorecard.distributors
            SELECT {columns}
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date = $3::date
             ORDER BY distributor
             LIMIT $4
        """
        rows = await scope.conn.fetch(sql, *scope.scope_args, period, clamp_top_n(50))
        label = _period_label(period)
        distributors, breaches = [], []
        counts = {"rojo": 0, "amarillo": 0, "verde": 0, "sin_umbral": 0}
        for row in rows:
            item = {
                "distributor": row["distributor"],
                "sell_out_revenue": as_float(row["sell_out_revenue_local"]),
                "sell_out_qty": as_float(row["sell_out_qty"]),
                "sell_in_qty": as_float(row["sell_in_qty"]),
                "growth_mom_pct": as_float(row["growth_mom_pct"]),
                "growth_yoy_pct": as_float(row["growth_yoy_pct"]),
                "sell_through_3m_pct": as_float(row["sell_through_3m_pct"]),
                "channel_days": as_float(row["channel_days"]),
                "margin_pct": as_float(row["margin_pct"]),
                "expiry_exposed_pct": as_float(row["expiry_exposed_pct"]),
                "overall_color": row["overall_color"],
            }
            distributors.append(item)
            counts[str(row["overall_color"])] = counts.get(str(row["overall_color"]), 0) + 1
            reds = [f"{name} {as_float(row[value])}" for color, name, value in _COLOR_METRICS if row[color] == "rojo"]
            if reds:
                breaches.append(f"La distribuidora {row['distributor']} esta en rojo en {label}: " + ", ".join(reds) + ".")
        return DistributorScorecard(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label})],
            period=label,
            breaches=breaches,
            distributors=distributors,
            red=counts["rojo"],
            yellow=counts["amarillo"],
            green=counts["verde"],
            without_thresholds=counts["sin_umbral"],
            sell_in_qty=sum(item["sell_in_qty"] or 0 for item in distributors),
            sell_out_qty=sum(item["sell_out_qty"] or 0 for item in distributors),
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


@dataclass
class BatchExpiry(B1Result):
    as_of: str | None = None
    expired_qty: float | None = None
    expired_value: float | None = None
    horizon_value: float | None = None
    at_risk_value: float | None = None
    transfer_candidates: int | None = None
    by_company: list[dict[str, Any]] = field(default_factory=list)
    top_items: list[dict[str, Any]] = field(default_factory=list)


async def query_batch_expiry(user: dict | None, *, top_n: int = 10) -> BatchExpiry:
    limit = clamp_top_n(top_n, default=10)
    base = {"proxy_note": EXPIRY_PROXY_NOTE}

    def _unavailable(error: str) -> BatchExpiry:
        return BatchExpiry(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> BatchExpiry:
        rel = await resolve_relation(scope, EXPIRY_DATASET, required=_EXPIRY_REQUIRED)
        if rel.missing_required:
            return BatchExpiry(status=STATUS_UNAVAILABLE, error=invalid_schema_error(rel),
                               missing_columns=list(rel.missing_required), **base)
        company_sql = f"""
            -- omega-aggregate: sap_b1.batch_expiry.companies
            SELECT company,
                   MAX(as_of_date)::date AS as_of,
                   COALESCE(SUM(qty) FILTER (WHERE bucket = 'vencido'), 0)::float8 AS expired_qty,
                   COALESCE(SUM(value_local) FILTER (WHERE bucket = 'vencido'), 0)::float8 AS expired_value,
                   COALESCE(SUM(value_local) FILTER (WHERE within_horizon AND bucket <> 'vencido'), 0)::float8 AS horizon_value,
                   COALESCE(SUM(at_risk_value_local) FILTER (WHERE bucket <> 'vencido'), 0)::float8 AS at_risk_value,
                   COUNT(*) FILTER (WHERE transfer_candidate IS NOT NULL AND at_risk_qty > 0)::bigint AS transfers
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
             GROUP BY company
             ORDER BY company
             LIMIT $3
        """
        companies = await scope.conn.fetch(company_sql, *scope.scope_args, clamp_top_n(50))
        items_sql = f"""
            -- omega-aggregate: sap_b1.batch_expiry.top_items
            SELECT company, item_code,
                   SUM(at_risk_qty)::float8 AS at_risk_qty,
                   SUM(at_risk_value_local)::float8 AS at_risk_value,
                   MIN(transfer_candidate) AS transfer_candidate
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND within_horizon AND bucket <> 'vencido' AND at_risk_qty > 0
             GROUP BY company, item_code
             ORDER BY SUM(at_risk_value_local) DESC, company, item_code
             LIMIT $3
        """
        items = await scope.conn.fetch(items_sql, *scope.scope_args, limit)
        if not companies:
            return BatchExpiry(status=STATUS_DEGRADED, notes=["sin lotes con existencia"],
                               evidence_refs=[gold_evidence(rel, filters={})], **base)
        as_of_value = max(row["as_of"] for row in companies if row["as_of"]) if any(row["as_of"] for row in companies) else None
        breaches = []
        by_company = []
        for row in companies:
            by_company.append({
                "company": row["company"],
                "expired_qty": as_float(row["expired_qty"]),
                "expired_value": as_float(row["expired_value"]),
                "horizon_value": as_float(row["horizon_value"]),
                "at_risk_value": as_float(row["at_risk_value"]),
                "transfer_candidates": as_int(row["transfers"]),
            })
            if as_float(row["expired_value"]):
                breaches.append(
                    f"{row['company']} tiene {round(as_float(row['expired_value']), 2)} en lotes ya vencidos con existencia."
                )
        top_items = []
        for row in items:
            action = f"traspasar a {row['transfer_candidate']}" if row["transfer_candidate"] else "promocion o devolucion"
            top_items.append({
                "company": row["company"],
                "item": row["item_code"],
                "at_risk_qty": as_float(row["at_risk_qty"]),
                "at_risk_value": as_float(row["at_risk_value"]),
                "action": action,
            })
            breaches.append(
                f"{row['company']}: el articulo {row['item_code']} tiene {round(as_float(row['at_risk_value']), 2)} "
                f"en riesgo de caducar sin venderse; accion sugerida: {action}."
            )
        return BatchExpiry(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"as_of": as_of_value, "top_n": limit})],
            breaches=breaches,
            as_of=as_of_value.isoformat() if as_of_value else None,
            expired_qty=sum(item["expired_qty"] or 0 for item in by_company),
            expired_value=sum(item["expired_value"] or 0 for item in by_company),
            horizon_value=sum(item["horizon_value"] or 0 for item in by_company),
            at_risk_value=sum(item["at_risk_value"] or 0 for item in by_company),
            transfer_candidates=sum(item["transfer_candidates"] or 0 for item in by_company),
            by_company=by_company,
            top_items=top_items,
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


COVERAGE_PROXY_NOTE = (
    "Cobertura por articulo al corte del inventario: dias que alcanza lo disponible "
    "al ritmo de consumo de los ultimos 90 dias, con y sin las ordenes de compra y "
    "de produccion abiertas, frente al tiempo de entrega; sugerencia de pedido "
    "hasta el nivel objetivo. Solo recomienda: NO escribe en Business One."
)


@dataclass
class ItemCoverage(B1Result):
    as_of: str | None = None
    items: int | None = None
    red: int | None = None
    yellow: int | None = None
    green: int | None = None
    without_consumption: int | None = None
    suggestions: int | None = None
    suggested_value: float | None = None
    min_stock_outdated: int | None = None
    by_company: list[dict[str, Any]] = field(default_factory=list)
    top_risks: list[dict[str, Any]] = field(default_factory=list)


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


async def query_item_coverage(user: dict | None, *, top_n: int = 10) -> ItemCoverage:
    limit = clamp_top_n(top_n, default=10)
    base = {"proxy_note": COVERAGE_PROXY_NOTE}

    def _unavailable(error: str) -> ItemCoverage:
        return ItemCoverage(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> ItemCoverage:
        rel = await resolve_relation(scope, COVERAGE_DATASET, required=_COVERAGE_REQUIRED)
        if rel.missing_required:
            return ItemCoverage(status=STATUS_UNAVAILABLE, error=invalid_schema_error(rel),
                                missing_columns=list(rel.missing_required), **base)
        company_sql = f"""
            -- omega-aggregate: sap_b1.item_coverage.companies
            SELECT company,
                   MAX(as_of_date)::date AS as_of,
                   COUNT(*)::bigint AS items,
                   COUNT(*) FILTER (WHERE coverage_color = 'rojo')::bigint AS red,
                   COUNT(*) FILTER (WHERE coverage_color = 'amarillo')::bigint AS yellow,
                   COUNT(*) FILTER (WHERE coverage_color = 'verde')::bigint AS green,
                   COUNT(*) FILTER (WHERE coverage_color = 'sin_consumo')::bigint AS without_consumption,
                   COUNT(*) FILTER (WHERE suggested_qty > 0)::bigint AS suggestions,
                   COALESCE(SUM(suggested_value_local) FILTER (WHERE suggested_qty > 0), 0)::float8 AS suggested_value,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY coverage_days)
                       FILTER (WHERE coverage_days IS NOT NULL)::float8 AS median_coverage_days,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY coverage_with_orders_days)
                       FILTER (WHERE coverage_with_orders_days IS NOT NULL)::float8 AS median_coverage_with_orders_days,
                   COUNT(*) FILTER (WHERE reorder_point > 0
                                     AND abs(reorder_point - COALESCE(b1_min_stock, 0)) > $3 * reorder_point)::bigint AS min_stock_outdated
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
             GROUP BY company
             ORDER BY company
             LIMIT $4
        """
        companies = await scope.conn.fetch(company_sql, *scope.scope_args, MIN_STOCK_DRIFT, clamp_top_n(50))
        risks_sql = f"""
            -- omega-aggregate: sap_b1.item_coverage.top_risks
            SELECT company, item_code, coverage_color,
                   coverage_days::float8 AS coverage_days,
                   coverage_with_orders_days::float8 AS coverage_with_orders_days,
                   lead_time_days::bigint AS lead_time_days,
                   stockout_date::date AS stockout_date,
                   suggested_qty::float8 AS suggested_qty,
                   suggested_action,
                   order_by_date::date AS order_by_date,
                   suggested_value_local::float8 AS suggested_value
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND coverage_color IN ('rojo', 'amarillo')
             ORDER BY (coverage_color = 'rojo') DESC, coverage_with_orders_days ASC NULLS LAST, company, item_code
             LIMIT $3
        """
        risks = await scope.conn.fetch(risks_sql, *scope.scope_args, limit)
        if not companies:
            return ItemCoverage(status=STATUS_DEGRADED, notes=["sin existencias publicadas"],
                                evidence_refs=[gold_evidence(rel, filters={})], **base)
        as_of_value = max((row["as_of"] for row in companies if row["as_of"]), default=None)
        breaches: list[str] = []
        by_company = []
        for row in companies:
            item = {
                "company": row["company"],
                "items": as_int(row["items"]),
                "red": as_int(row["red"]),
                "yellow": as_int(row["yellow"]),
                "green": as_int(row["green"]),
                "without_consumption": as_int(row["without_consumption"]),
                "suggestions": as_int(row["suggestions"]),
                "suggested_value": as_float(row["suggested_value"]),
                "median_coverage_days": as_float(row["median_coverage_days"]),
                "median_coverage_with_orders_days": as_float(row["median_coverage_with_orders_days"]),
                "min_stock_outdated": as_int(row["min_stock_outdated"]),
            }
            by_company.append(item)
            if item["red"]:
                breaches.append(
                    f"{row['company']}: {item['red']} articulos se agotan antes de que pueda llegar "
                    "un pedido nuevo, aun contando las ordenes abiertas."
                )
        top_risks = []
        for row in risks:
            risk = {
                "company": row["company"],
                "item": row["item_code"],
                "color": row["coverage_color"],
                "coverage_days": as_float(row["coverage_days"]),
                "coverage_with_orders_days": as_float(row["coverage_with_orders_days"]),
                "lead_time_days": as_int(row["lead_time_days"]),
                "stockout_date": _iso(row["stockout_date"]),
                "suggested_qty": as_float(row["suggested_qty"]),
                "action": row["suggested_action"],
                "order_by": _iso(row["order_by_date"]),
                "suggested_value": as_float(row["suggested_value"]),
            }
            top_risks.append(risk)
            if row["coverage_color"] == "rojo":
                breaches.append(
                    f"{row['company']}: el articulo {row['item_code']} se agota el {risk['stockout_date']} "
                    f"y el tiempo de entrega es de {risk['lead_time_days']} dias; "
                    f"{risk['action']} {risk['suggested_qty']:g} hoy."
                )
        return ItemCoverage(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"as_of": as_of_value, "top_n": limit})],
            breaches=breaches,
            as_of=_iso(as_of_value),
            items=sum(item["items"] or 0 for item in by_company),
            red=sum(item["red"] or 0 for item in by_company),
            yellow=sum(item["yellow"] or 0 for item in by_company),
            green=sum(item["green"] or 0 for item in by_company),
            without_consumption=sum(item["without_consumption"] or 0 for item in by_company),
            suggestions=sum(item["suggestions"] or 0 for item in by_company),
            suggested_value=sum(item["suggested_value"] or 0 for item in by_company),
            min_stock_outdated=sum(item["min_stock_outdated"] or 0 for item in by_company),
            by_company=by_company,
            top_risks=top_risks,
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


__all__ = [
    "COMPANY_DATASET",
    "CONSOLIDATED_DATASET",
    "COVERAGE_DATASET",
    "CUSTOMER_DATASET",
    "DATA_QUALITY_DATASET",
    "EXPIRY_DATASET",
    "ITEM_DATASET",
    "RECONCILIATION_DATASET",
    "SCORECARD_DATASET",
    "query_batch_expiry",
    "query_below_min_sales",
    "query_company_margin",
    "query_customer_margin",
    "query_data_quality",
    "query_distributor_scorecard",
    "query_group_margin",
    "query_item_coverage",
    "query_item_family_margin",
    "query_reconciliation",
]
