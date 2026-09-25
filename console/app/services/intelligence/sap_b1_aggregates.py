from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Awaitable, Callable

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

KPIS_DATASET = "sap_b1_margin_kpis_month"
DETAIL_DATASET = "sap_b1_margin_detail_month"
CONSOLIDATED_DATASET = "sap_b1_margin_consolidated_month"
KPI_RECONCILIATION_DATASET = "sap_b1_kpi_reconciliation"
LEDGER_DATASET = "sap_b1_margin_reconciliation_month"
DATA_QUALITY_DATASET = "sap_b1_data_quality"
ENTITY_MODEL_DATASET = "sap_b1_entity_model"
SCORECARD_DATASET = "sap_b1_distributor_scorecard_month"
SELLOUT_CLINIC_DATASET = "sap_b1_sellout_by_customer_month"
EXPIRY_DATASET = "sap_b1_batch_expiry"
COVERAGE_DATASET = "sap_b1_item_coverage"
COST_VARIANCE_DATASET = "sap_b1_material_cost_variance"
LEAD_TIME_DATASET = "sap_b1_supplier_lead_time"
LOAD_DATASET = "sap_b1_load_reconciliation"

GROUP = "grupo"
GROUP_MARGIN_DROP_PP = 3.0
RECONCILIATION_MONTHS = 12
LEAD_TIME_MONTHS = 3
PLAN_CHANGE_DAYS = 1

_KPI_REQUIRED = frozenset(
    {"company", "doc_month", "period", "local_currency", "indicator", "dimension", "dim_key", "dim_label",
     "value_local", "value_pct", "rank_in_period", "min_margin_pct", "revenue_net_local", "gross_margin_local",
     "commission_local", "contribution_margin_local"}
)
_KPI_RECON_REQUIRED = frozenset(
    {"company", "period", "indicator", "dimension", "dim_key", "dim_label", "unit", "finance_value", "platform_value",
     "delta_monto", "delta_pct", "tolerance_pct", "status", "venta_bruta", "devoluciones_nc",
     "descuentos_pie_factura", "costo_aplicado", "comision", "uploaded_at"}
)
_LEDGER_REQUIRED = frozenset({"company", "doc_month", "status", "revenue_residual_local", "cogs_residual_local"})
_DATA_QUALITY_REQUIRED = frozenset(
    {"company", "check_code", "check_group", "total", "failing", "pct_ok", "min_pct", "status"}
)
_ENTITY_REQUIRED = frozenset(
    {"entity", "company", "records", "identities", "shared_identities", "complete_records", "completeness_pct",
     "orphans", "relation_rule"}
)
_SCORECARD_REQUIRED = frozenset(
    {"distributor", "doc_month", "sell_out_revenue_local", "sell_out_qty", "sell_in_qty",
     "growth_mom_pct", "growth_yoy_pct", "sell_through_3m_pct", "channel_days", "margin_pct",
     "expiry_exposed_pct", "growth_color", "sell_through_color", "channel_days_color",
     "margin_color", "expiry_color", "overall_color"}
)
_CLINIC_REQUIRED = frozenset(
    {"distributor", "doc_month", "card_code", "card_name", "sell_out_revenue_local", "units", "share_of_sell_out_pct",
     "rank_in_month", "local_currency"}
)
_EXPIRY_REQUIRED = frozenset(
    {"company", "item_code", "as_of_date", "alert_level", "within_horizon", "value_local", "at_risk_qty",
     "at_risk_value_local", "priority_rank", "action_option", "transfer_branch_name", "transfer_candidate",
     "branch_name", "exp_date", "days_to_expiry", "batch"}
)
_COVERAGE_REQUIRED = frozenset(
    {"company", "item_code", "item_name", "as_of_date", "daily_consumption", "coverage_days",
     "coverage_with_orders_days", "lead_time_days", "stockout_date", "coverage_color", "stockout_risk",
     "suggested_qty", "suggested_action", "suggested_supplier", "alternate_supplier", "suggested_value_local",
     "order_by_date", "open_po_qty", "net_need_qty", "open_po_vs_need_pct", "plan_need_qty", "plan_updated_at",
     "consumption_basis", "is_raw_material", "criticality_rank", "is_critical"}
)
_COST_REQUIRED = frozenset(
    {"company", "doc_month", "item_code", "item_name", "is_raw_material", "purchased_qty", "real_unit_price_local",
     "standard_cost_local", "variance_pct", "variance_value_local", "max_variance_pct", "above_threshold"}
)
_LEAD_REQUIRED = frozenset(
    {"company", "doc_month", "card_code", "supplier_name", "is_intercompany", "receipts", "late_receipts",
     "avg_lead_days", "avg_promised_days", "max_delay_days"}
)

MARGEN_BRUTO_NOTE = (
    "Venta neta (facturas después del descuento de pie de factura menos notas de crédito) menos el costo que "
    "Business One registra en cada línea, por empresa sobre sus propios libros y para el grupo con la utilidad "
    "entre empresas eliminada. NO convierte monedas."
)
MARGEN_CONTRIBUCION_NOTE = (
    "Margen bruto menos la comisión de venta de cada línea. Las ventas entre empresas del grupo no llevan comisión. "
    "NO incluye otros costos variables que no estén en los documentos."
)
DESTRUCTORES_NOTE = (
    "Clientes externos del último mes cerrado con margen bruto por debajo del mínimo configurado (o negativo si no "
    "hay mínimo), ordenados por el margen perdido contra ese mínimo. Los nombres solo aparecen a petición explícita."
)
CONCENTRACION_NOTE = (
    "Parte del margen bruto externo del último mes cerrado que aporta el 20 % de clientes con más margen."
)
MARGEN_VENDEDOR_NOTE = "Margen bruto y de contribución del último mes cerrado por vendedor de cada empresa."
RECONCILIACION_NOTE = (
    "Corrida manual de Finanzas contra la plataforma, fila por fila de los cinco indicadores de margen, con la "
    "diferencia y sus componentes (venta bruta, notas de crédito, descuentos de pie, costo, comisión); además, el "
    "cuadre de los documentos contra la contabilidad de los últimos doce meses. NO sustituye el cierre contable."
)
CALIDAD_NOTE = (
    "Revisiones de calidad por empresa que quedaron bajo su mínimo: RFC de clientes, relaciones entre documentos y "
    "maestros, costo en líneas, lotes e intercompañía. NO corrige datos en Business One."
)
MODELO_NOTE = (
    "Las ocho entidades del modelo unificado de las tres empresas: registros, identidades, identidades compartidas, "
    "relaciones completas y huérfanos."
)
SEMAFORO_DIST_NOTE = (
    "Semáforo del último mes cerrado por distribuidora: sell-out y crecimiento, sell-through de tres meses, días de "
    "inventario en canal, margen y stock expuesto a caducidad contra sus umbrales."
)
RATIO_NOTE = "Unidades vendidas por la distribuidora a clientes externos entre unidades compradas a la fábrica, tres meses."
DIAS_INVENTARIO_NOTE = "Existencia de la distribuidora al cierre del mes entre su venta diaria de tres meses."
CADUCIDAD_NOTE = (
    "Lotes con existencia al corte: vencidos y los que caducan en 30 (rojo), 60 (amarillo) o 90 días (verde), las "
    "unidades que se vencerían sin venderse al ritmo de 90 días y la acción sugerida (traslado a otra filial, a otra "
    "empresa del grupo o promoción), priorizados por valor. NO considera traslados ya en camino."
)
SELLOUT_CLINICA_NOTE = (
    "Venta de cada distribuidora a cada clínica del último mes cerrado, neta de descuentos y notas de crédito, con "
    "su participación. Los nombres solo aparecen a petición explícita."
)
COBERTURA_NOTE = (
    "Días que alcanza lo disponible con las órdenes abiertas al ritmo mayor entre el consumo de 90 días y la "
    "necesidad del plan de producción; semáforo rojo abajo de 30 días y amarillo abajo de 60; riesgo de quiebre "
    "cuando se agota antes de que pueda llegar un pedido. Solo recomienda: NO escribe en Business One."
)
OC_NECESIDAD_NOTE = "Órdenes de compra abiertas contra la necesidad neta del horizonte de planeación, por artículo."
COSTO_NOTE = (
    "Precio real de compra del último mes cerrado contra el costo del artículo en Business One (estándar si se valúa "
    "a estándar), por artículo, y los que exceden la desviación máxima."
)
LEAD_TIME_NOTE = "Entregas de proveedores de los últimos tres meses contra la fecha prometida en la orden de compra."
APRENDIZAJE_NOTE = (
    "Lo que se decidió sobre las alertas de SAP Business One y lo que resultó: alertas, decisiones, resultados "
    "medidos, falsos positivos y lecciones; propone revisar umbrales cuando la evidencia lo sugiere. Nunca cambia un "
    "umbral por sí mismo."
)


@dataclass
class B1Result(AggregateResult):
    period: str | None = None
    breaches: list[str] = field(default_factory=list)


def _label(value: Any) -> str | None:
    return value.strftime("%Y-%m") if hasattr(value, "strftime") else (str(value) if value else None)


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return round(100.0 * numerator / denominator, 2)


def _round(value: Any, digits: int = 2) -> float | None:
    number = as_float(value)
    return None if number is None else round(number, digits)


def _money(value: Any) -> str:
    number = as_float(value) or 0.0
    return f"{number:,.2f}"


async def _gold(
    user: dict | None,
    result_type: type,
    note: str,
    dataset: str,
    required: frozenset[str],
    compute: Callable[[GoldScope, Any, dict[str, Any]], Awaitable[Any]],
) -> Any:
    base = {"proxy_note": note}

    def _unavailable(error: str) -> Any:
        return result_type(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _run(scope: GoldScope) -> Any:
        rel = await resolve_relation(scope, dataset, required=required)
        if rel.missing_required:
            return result_type(status=STATUS_UNAVAILABLE, error=invalid_schema_error(rel),
                               missing_columns=list(rel.missing_required), **base)
        return await compute(scope, rel, base)

    return await run_gold_aggregate(user, _run, _unavailable)


async def _closed_month(scope: GoldScope, rel: Any, as_of: date | None, marker: str, extra: str = "") -> date | None:
    sql = f"""
        -- omega-aggregate: sap_b1.{marker}.closed_period
        SELECT MAX(doc_month)::date AS period
          FROM {rel.sql}
         WHERE {GOLD_SCOPE_PREDICATE}
           AND doc_month::date < $3::date {extra}
    """
    row = await scope.conn.fetchrow(sql, *scope.scope_args, month_start(as_of_date(as_of)))
    return row["period"] if row else None


def _no_period(result_type: type, base: dict[str, Any], rel: Any) -> Any:
    return result_type(status=STATUS_DEGRADED, notes=["sin meses cerrados con datos"],
                       evidence_refs=[gold_evidence(rel, filters={})], **base)


# ---------------------------------------------------------------- finanzas


@dataclass
class MarginTotals(B1Result):
    currency: str | None = None
    group: dict[str, Any] | None = None
    companies: list[dict[str, Any]] = field(default_factory=list)
    trailing_group_pct: float | None = None


async def _margin_totals(user: dict | None, indicator: str, note: str, as_of: date | None) -> MarginTotals:
    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> MarginTotals:
        period = await _closed_month(scope, rel, as_of, indicator, f"AND indicator = '{indicator}'")
        if period is None:
            return _no_period(MarginTotals, base, rel)
        sql = f"""
            -- omega-aggregate: sap_b1.{indicator}.totals
            SELECT company, doc_month::date AS doc_month, local_currency,
                   value_local::float8 AS value, value_pct::float8 AS pct,
                   revenue_net_local::float8 AS revenue, commission_local::float8 AS commission
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND indicator = $3 AND dimension = 'total'
               AND doc_month::date <= $4::date AND doc_month::date >= $5::date
             ORDER BY doc_month DESC, company
             LIMIT $6
        """
        rows = await scope.conn.fetch(sql, *scope.scope_args, indicator, period, add_months(period, -3), 200)
        current = [row for row in rows if row["doc_month"] == period]
        label = _label(period)
        group = next((row for row in current if row["company"] == GROUP), None)
        trailing = [as_float(row["pct"]) for row in rows if row["company"] == GROUP and row["doc_month"] < period]
        trailing = [value for value in trailing if value is not None]
        trailing_pct = round(sum(trailing) / len(trailing), 2) if trailing else None
        breaches: list[str] = []
        group_pct = as_float(group["pct"]) if group else None
        if group_pct is not None and trailing_pct is not None and group_pct < trailing_pct - GROUP_MARGIN_DROP_PP:
            breaches.append(
                f"El {'margen bruto' if indicator == 'margen_bruto' else 'margen de contribución'} del grupo de "
                f"{label} fue {round(group_pct, 2)} % y cayó {round(trailing_pct - group_pct, 2)} puntos contra el "
                f"promedio de los tres meses previos ({trailing_pct} %)."
            )
        return MarginTotals(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label, "indicator": indicator})],
            period=label,
            breaches=breaches,
            currency=(group or (current[0] if current else {})).get("local_currency") if (group or current) else None,
            group={"value": as_float(group["value"]), "pct": _round(group["pct"]), "revenue": as_float(group["revenue"]),
                   "commission": as_float(group["commission"])} if group else None,
            companies=[
                {"company": row["company"], "value": as_float(row["value"]), "pct": _round(row["pct"]),
                 "revenue": as_float(row["revenue"]), "commission": as_float(row["commission"])}
                for row in current if row["company"] != GROUP
            ],
            trailing_group_pct=trailing_pct,
            **base,
        )

    return await _gold(user, MarginTotals, note, KPIS_DATASET, _KPI_REQUIRED, compute)


async def query_margen_bruto(user: dict | None, *, as_of: date | None = None) -> MarginTotals:
    return await _margin_totals(user, "margen_bruto", MARGEN_BRUTO_NOTE, as_of)


async def query_margen_contribucion(user: dict | None, *, as_of: date | None = None) -> MarginTotals:
    return await _margin_totals(user, "margen_contribucion", MARGEN_CONTRIBUCION_NOTE, as_of)


@dataclass
class Destroyers(B1Result):
    customers: int | None = None
    margin_lost: float | None = None
    by_company: list[dict[str, Any]] = field(default_factory=list)
    top: list[dict[str, Any]] = field(default_factory=list)


async def query_destructores(user: dict | None, *, as_of: date | None = None, top_n: int = 0) -> Destroyers:
    named = clamp_named_rows(top_n)

    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> Destroyers:
        period = await _closed_month(scope, rel, as_of, "destructores", "AND indicator IN ('margen_bruto', 'destructores')")
        if period is None:
            return _no_period(Destroyers, base, rel)
        sql = f"""
            -- omega-aggregate: sap_b1.destructores.companies
            SELECT company, COUNT(*)::bigint AS customers, SUM(value_local)::float8 AS lost,
                   MAX(min_margin_pct)::float8 AS min_margin_pct
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND indicator = 'destructores' AND doc_month::date = $3::date
             GROUP BY company
             ORDER BY SUM(value_local) DESC, company
             LIMIT $4
        """
        rows = await scope.conn.fetch(sql, *scope.scope_args, period, clamp_top_n(50))
        top: list[Any] = []
        if named:
            top = await scope.conn.fetch(f"""
                -- omega-aggregate: sap_b1.destructores.top
                SELECT company, dim_label, value_local::float8 AS lost, value_pct::float8 AS pct,
                       min_margin_pct::float8 AS min_margin_pct, revenue_net_local::float8 AS revenue
                  FROM {rel.sql}
                 WHERE {GOLD_SCOPE_PREDICATE}
                   AND indicator = 'destructores' AND doc_month::date = $3::date
                 ORDER BY value_local DESC, company, dim_key
                 LIMIT $4
            """, *scope.scope_args, period, named)
        label = _label(period)
        breaches = [
            f"{row['company']}: {as_int(row['customers'])} clientes destruyen margen en {label}; margen perdido "
            f"{_money(row['lost'])} contra el mínimo"
            + (f" de {_round(row['min_margin_pct'])} %." if row["min_margin_pct"] is not None else " (sin mínimo: margen negativo).")
            for row in rows
        ]
        return Destroyers(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label, "indicator": "destructores", "top_n": named})],
            period=label,
            breaches=breaches,
            customers=sum(as_int(row["customers"]) or 0 for row in rows),
            margin_lost=sum(as_float(row["lost"]) or 0.0 for row in rows),
            by_company=[{"company": row["company"], "customers": as_int(row["customers"]), "margin_lost": as_float(row["lost"]),
                         "min_margin_pct": as_float(row["min_margin_pct"])} for row in rows],
            top=[{"company": row["company"], "customer": row["dim_label"], "margin_lost": as_float(row["lost"]),
                  "margin_pct": _round(row["pct"]), "min_margin_pct": as_float(row["min_margin_pct"]),
                  "revenue": as_float(row["revenue"])} for row in top],
            **base,
        )

    return await _gold(user, Destroyers, DESTRUCTORES_NOTE, KPIS_DATASET, _KPI_REQUIRED, compute)


@dataclass
class Concentration(B1Result):
    by_company: list[dict[str, Any]] = field(default_factory=list)


async def query_concentracion_top20(user: dict | None, *, as_of: date | None = None) -> Concentration:
    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> Concentration:
        period = await _closed_month(scope, rel, as_of, "concentracion_top20", "AND indicator = 'concentracion_top20'")
        if period is None:
            return _no_period(Concentration, base, rel)
        rows = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.concentracion_top20.companies
            SELECT company, value_pct::float8 AS pct, value_local::float8 AS top_margin,
                   rank_in_period::bigint AS top_n
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND indicator = 'concentracion_top20' AND dimension = 'total' AND doc_month::date = $3::date
             ORDER BY company
             LIMIT $4
        """, *scope.scope_args, period, clamp_top_n(50))
        label = _label(period)
        return Concentration(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label, "indicator": "concentracion_top20"})],
            period=label,
            by_company=[{"company": row["company"], "share_pct": _round(row["pct"]), "top_margin": as_float(row["top_margin"]),
                         "top_customers": as_int(row["top_n"])} for row in rows],
            **base,
        )

    return await _gold(user, Concentration, CONCENTRACION_NOTE, KPIS_DATASET, _KPI_REQUIRED, compute)


@dataclass
class SellerMargin(B1Result):
    by_company: list[dict[str, Any]] = field(default_factory=list)


async def query_margen_vendedor(user: dict | None, *, as_of: date | None = None, top_n: int = 0) -> SellerMargin:
    named = clamp_named_rows(top_n)

    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> SellerMargin:
        period = await _closed_month(scope, rel, as_of, "margen_vendedor", "AND indicator = 'margen_vendedor'")
        if period is None:
            return _no_period(SellerMargin, base, rel)
        rows = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.margen_vendedor.sellers
            SELECT company, dim_label, value_local::float8 AS margin, value_pct::float8 AS pct,
                   contribution_margin_local::float8 AS contribution, revenue_net_local::float8 AS revenue,
                   rank_in_period::bigint AS rank
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND indicator = 'margen_vendedor' AND doc_month::date = $3::date
             ORDER BY company, rank_in_period
             LIMIT $4
        """, *scope.scope_args, period, clamp_top_n(200, upper=500))
        label = _label(period)
        by_company: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = by_company.setdefault(row["company"], {"company": row["company"], "sellers": 0, "margin": 0.0,
                                                           "best_pct": None, "worst_pct": None, "sellers_detail": []})
            pct = _round(row["pct"])
            entry["sellers"] += 1
            entry["margin"] += as_float(row["margin"]) or 0.0
            if pct is not None:
                entry["best_pct"] = pct if entry["best_pct"] is None else max(entry["best_pct"], pct)
                entry["worst_pct"] = pct if entry["worst_pct"] is None else min(entry["worst_pct"], pct)
            if named and len(entry["sellers_detail"]) < named:
                entry["sellers_detail"].append({"seller": row["dim_label"], "margin": as_float(row["margin"]), "margin_pct": pct,
                                                "contribution": as_float(row["contribution"]), "revenue": as_float(row["revenue"])})
        return SellerMargin(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label, "indicator": "margen_vendedor", "top_n": named})],
            period=label,
            by_company=list(by_company.values()),
            **base,
        )

    return await _gold(user, SellerMargin, MARGEN_VENDEDOR_NOTE, KPIS_DATASET, _KPI_REQUIRED, compute)


@dataclass
class FinanceReconciliation(B1Result):
    rows: int | None = None
    within: int | None = None
    outside: int | None = None
    without_platform: int | None = None
    platform_only: int | None = None
    within_pct: float | None = None
    periods: list[str] = field(default_factory=list)
    outliers: list[dict[str, Any]] = field(default_factory=list)
    ledger_months: int | None = None
    ledger_differences: int | None = None


async def query_reconciliacion_finanzas(user: dict | None, *, as_of: date | None = None) -> FinanceReconciliation:
    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> FinanceReconciliation:
        totals = await scope.conn.fetchrow(f"""
            -- omega-aggregate: sap_b1.reconciliacion_finanzas.totals
            SELECT COUNT(*) FILTER (WHERE status <> 'solo_plataforma')::bigint AS rows,
                   COUNT(*) FILTER (WHERE status = 'ok')::bigint AS within,
                   COUNT(*) FILTER (WHERE status = 'fuera_tolerancia')::bigint AS outside,
                   COUNT(*) FILTER (WHERE status = 'sin_dato_plataforma')::bigint AS missing,
                   COUNT(*) FILTER (WHERE status = 'solo_plataforma')::bigint AS platform_only,
                   array_agg(DISTINCT period ORDER BY period) FILTER (WHERE status <> 'solo_plataforma') AS periods
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
        """, *scope.scope_args)
        outliers = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.reconciliacion_finanzas.outliers
            SELECT company, period, indicator, dimension, dim_key, dim_label, unit,
                   finance_value::float8 AS finance_value, platform_value::float8 AS platform_value,
                   delta_pct::float8 AS delta_pct, venta_bruta::float8 AS venta_bruta,
                   devoluciones_nc::float8 AS devoluciones_nc, descuentos_pie_factura::float8 AS descuentos,
                   costo_aplicado::float8 AS costo, comision::float8 AS comision
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND status IN ('fuera_tolerancia', 'sin_dato_plataforma')
             ORDER BY abs(COALESCE(delta_pct, 0)) DESC, company, period, indicator
             LIMIT $3
        """, *scope.scope_args, clamp_top_n(10))
        rows_total = as_int(totals["rows"]) or 0
        within = as_int(totals["within"]) or 0
        notes: list[str] = []
        if not rows_total:
            notes.append("Finanzas todavía no carga su corrida manual")
        breaches = []
        for row in outliers:
            what = f"{row['indicator'].replace('_', ' ')} {row['dimension']}" + (f" {row['dim_key']}" if row["dim_key"] else "")
            if row["platform_value"] is None:
                breaches.append(f"{row['company']} {row['period']}: {what} de Finanzas no existe en la plataforma.")
            else:
                breaches.append(
                    f"{row['company']} {row['period']}: {what} difiere {_round(row['delta_pct'])} "
                    f"{'puntos' if row['unit'] == 'pct' else '%'} contra Finanzas."
                )
        window_end = month_start(as_of_date(as_of))
        ledger = await _ledger_summary(scope, add_months(window_end, -RECONCILIATION_MONTHS), window_end)
        if ledger["differences"]:
            breaches.append(
                f"En {ledger['differences']} empresa-mes de los últimos doce los documentos no cuadran con la contabilidad."
            )
        periods = list(totals["periods"] or [])
        return FinanceReconciliation(
            status=status_for(rel) if rows_total else STATUS_DEGRADED,
            evidence_refs=[gold_evidence(rel, filters={})],
            notes=notes,
            period=periods[-1] if periods else None,
            breaches=breaches,
            rows=rows_total,
            within=within,
            outside=as_int(totals["outside"]),
            without_platform=as_int(totals["missing"]),
            platform_only=as_int(totals["platform_only"]),
            within_pct=_pct(within, rows_total),
            periods=periods,
            outliers=[
                {"company": row["company"], "period": row["period"], "indicator": row["indicator"],
                 "dimension": row["dimension"], "key": row["dim_key"], "label": row["dim_label"], "unit": row["unit"],
                 "finance_value": as_float(row["finance_value"]), "platform_value": as_float(row["platform_value"]),
                 "delta_pct": _round(row["delta_pct"], 4), "venta_bruta": as_float(row["venta_bruta"]),
                 "devoluciones_nc": as_float(row["devoluciones_nc"]), "descuentos_pie_factura": as_float(row["descuentos"]),
                 "costo_aplicado": as_float(row["costo"]), "comision": as_float(row["comision"])}
                for row in outliers
            ],
            ledger_months=ledger["months"],
            ledger_differences=ledger["differences"],
            **base,
        )

    return await _gold(user, FinanceReconciliation, RECONCILIACION_NOTE, KPI_RECONCILIATION_DATASET, _KPI_RECON_REQUIRED, compute)


async def _ledger_summary(scope: GoldScope, start: date, end: date) -> dict[str, int]:
    try:
        rel = await resolve_relation(scope, LEDGER_DATASET, required=_LEDGER_REQUIRED)
    except Exception:  # noqa: BLE001
        return {"months": 0, "differences": 0}
    if rel.missing_required:
        return {"months": 0, "differences": 0}
    row = await scope.conn.fetchrow(f"""
        -- omega-aggregate: sap_b1.reconciliacion_finanzas.ledger
        SELECT COUNT(*)::bigint AS months, COUNT(*) FILTER (WHERE status = 'diferencia')::bigint AS differences
          FROM {rel.sql}
         WHERE {GOLD_SCOPE_PREDICATE}
           AND doc_month::date >= $3::date AND doc_month::date < $4::date
    """, *scope.scope_args, start, end)
    return {"months": as_int(row["months"]) or 0, "differences": as_int(row["differences"]) or 0}


@dataclass
class DataQuality(B1Result):
    checks: int | None = None
    checks_below_min: int | None = None
    failing: list[dict[str, Any]] = field(default_factory=list)


async def query_calidad_datos(user: dict | None) -> DataQuality:
    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> DataQuality:
        totals = await scope.conn.fetchrow(f"""
            -- omega-aggregate: sap_b1.calidad_datos.totals
            SELECT COUNT(*)::bigint AS checks, COUNT(*) FILTER (WHERE status = 'bajo_umbral')::bigint AS below
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
        """, *scope.scope_args)
        rows = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.calidad_datos.failing
            SELECT company, check_code, check_group, total::bigint AS total, failing::bigint AS failing,
                   pct_ok::float8 AS pct_ok, min_pct::float8 AS min_pct
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND status = 'bajo_umbral'
             ORDER BY pct_ok, company, check_code
             LIMIT $3
        """, *scope.scope_args, clamp_top_n(10))
        return DataQuality(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={})],
            breaches=[
                f"Calidad de datos en {row['company']}: {row['check_code'].replace('_', ' ')} al "
                f"{as_float(row['pct_ok'])} % (mínimo {as_float(row['min_pct'])} %)."
                for row in rows
            ],
            checks=as_int(totals["checks"]),
            checks_below_min=as_int(totals["below"]),
            failing=[{"company": row["company"], "check": row["check_code"].replace("_", " "), "group": row["check_group"],
                      "total": as_int(row["total"]), "failing": as_int(row["failing"]), "pct_ok": as_float(row["pct_ok"]),
                      "min_pct": as_float(row["min_pct"])} for row in rows],
            **base,
        )

    return await _gold(user, DataQuality, CALIDAD_NOTE, DATA_QUALITY_DATASET, _DATA_QUALITY_REQUIRED, compute)


@dataclass
class EntityModel(B1Result):
    entities: list[dict[str, Any]] = field(default_factory=list)


async def query_modelo_entidades(user: dict | None) -> EntityModel:
    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> EntityModel:
        rows = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.modelo_entidades.rows
            SELECT entity, company, records::bigint AS records, identities::bigint AS identities,
                   shared_identities::bigint AS shared, complete_records::bigint AS complete,
                   completeness_pct::float8 AS completeness_pct, orphans::bigint AS orphans, relation_rule
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
             ORDER BY entity, company = 'grupo' DESC, company
             LIMIT $3
        """, *scope.scope_args, clamp_top_n(100, upper=200))
        breaches = [
            f"{row['entity']}: {as_int(row['orphans'])} referencias huérfanas en el grupo."
            for row in rows if row["company"] == GROUP and (as_int(row["orphans"]) or 0) > 0
        ]
        return EntityModel(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={})],
            breaches=breaches,
            entities=[{"entity": row["entity"], "company": row["company"], "records": as_int(row["records"]),
                       "identities": as_int(row["identities"]), "shared_identities": as_int(row["shared"]),
                       "complete_records": as_int(row["complete"]), "completeness_pct": as_float(row["completeness_pct"]),
                       "orphans": as_int(row["orphans"]), "relation_rule": row["relation_rule"]} for row in rows],
            **base,
        )

    return await _gold(user, EntityModel, MODELO_NOTE, ENTITY_MODEL_DATASET, _ENTITY_REQUIRED, compute)


# ---------------------------------------------------------------- ventas

_COLOR_METRICS = (
    ("growth_color", "crecimiento de sell-out", "growth_yoy_pct"),
    ("sell_through_color", "ratio sell-out / sell-in", "sell_through_3m_pct"),
    ("channel_days_color", "días de inventario en canal", "channel_days"),
    ("margin_color", "margen de la distribuidora", "margin_pct"),
    ("expiry_color", "stock expuesto a caducidad", "expiry_exposed_pct"),
)


@dataclass
class DistributorScorecard(B1Result):
    distributors: list[dict[str, Any]] = field(default_factory=list)
    red: int | None = None
    yellow: int | None = None
    green: int | None = None
    without_thresholds: int | None = None


async def query_semaforo_distribuidoras(user: dict | None, *, as_of: date | None = None) -> DistributorScorecard:
    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> DistributorScorecard:
        period = await _closed_month(scope, rel, as_of, "semaforo_distribuidoras")
        if period is None:
            return _no_period(DistributorScorecard, base, rel)
        columns = ", ".join(sorted(_SCORECARD_REQUIRED - {"doc_month"}))
        rows = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.semaforo_distribuidoras.rows
            SELECT {columns}
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date = $3::date
             ORDER BY distributor
             LIMIT $4
        """, *scope.scope_args, period, clamp_top_n(50))
        label = _label(period)
        counts = {"rojo": 0, "amarillo": 0, "verde": 0, "sin_umbral": 0}
        distributors, breaches = [], []
        for row in rows:
            distributors.append({
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
                "colors": {color: row[color] for color, _name, _value in _COLOR_METRICS},
                "overall_color": row["overall_color"],
            })
            counts[str(row["overall_color"])] = counts.get(str(row["overall_color"]), 0) + 1
            reds = [f"{name} {_round(row[value])}" for color, name, value in _COLOR_METRICS if row[color] == "rojo"]
            if reds:
                breaches.append(f"La distribuidora {row['distributor']} está en rojo en {label}: " + ", ".join(reds) + ".")
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
            **base,
        )

    return await _gold(user, DistributorScorecard, SEMAFORO_DIST_NOTE, SCORECARD_DATASET, _SCORECARD_REQUIRED, compute)


@dataclass
class DistributorMetric(B1Result):
    distributors: list[dict[str, Any]] = field(default_factory=list)


async def _distributor_metric(user: dict | None, as_of: date | None, value: str, color: str, name: str, note: str,
                              unit: str) -> DistributorMetric:
    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> DistributorMetric:
        period = await _closed_month(scope, rel, as_of, value)
        if period is None:
            return _no_period(DistributorMetric, base, rel)
        rows = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.{value}.rows
            SELECT distributor, {value}::float8 AS value, {color} AS color
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date = $3::date
             ORDER BY distributor
             LIMIT $4
        """, *scope.scope_args, period, clamp_top_n(50))
        label = _label(period)
        return DistributorMetric(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label, "metric": value})],
            period=label,
            breaches=[f"{row['distributor']}: {name} de {_round(row['value'])} {unit} en {label} (rojo)."
                      for row in rows if row["color"] == "rojo"],
            distributors=[{"distributor": row["distributor"], "value": _round(row["value"]), "color": row["color"]} for row in rows],
            **base,
        )

    return await _gold(user, DistributorMetric, note, SCORECARD_DATASET, _SCORECARD_REQUIRED, compute)


async def query_ratio_sellout_sellin(user: dict | None, *, as_of: date | None = None) -> DistributorMetric:
    return await _distributor_metric(user, as_of, "sell_through_3m_pct", "sell_through_color",
                                     "ratio sell-out / sell-in", RATIO_NOTE, "%")


async def query_dias_inventario(user: dict | None, *, as_of: date | None = None) -> DistributorMetric:
    return await _distributor_metric(user, as_of, "channel_days", "channel_days_color",
                                     "días de inventario en canal", DIAS_INVENTARIO_NOTE, "días")


@dataclass
class ClinicSellOut(B1Result):
    by_distributor: list[dict[str, Any]] = field(default_factory=list)


async def query_sellout_clinica(user: dict | None, *, as_of: date | None = None, top_n: int = 0) -> ClinicSellOut:
    named = clamp_named_rows(top_n)

    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> ClinicSellOut:
        period = await _closed_month(scope, rel, as_of, "sellout_clinica")
        if period is None:
            return _no_period(ClinicSellOut, base, rel)
        rows = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.sellout_clinica.rows
            SELECT distributor, card_name, sell_out_revenue_local::float8 AS revenue, units::float8 AS units,
                   share_of_sell_out_pct::float8 AS share, rank_in_month::bigint AS rank, local_currency
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date = $3::date
             ORDER BY distributor, rank_in_month
             LIMIT $4
        """, *scope.scope_args, period, clamp_top_n(500, upper=2000))
        label = _label(period)
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = grouped.setdefault(row["distributor"], {"distributor": row["distributor"], "clinics": 0, "revenue": 0.0,
                                                            "units": 0.0, "top_share_pct": None, "currency": row["local_currency"],
                                                            "top": []})
            entry["clinics"] += 1
            entry["revenue"] += as_float(row["revenue"]) or 0.0
            entry["units"] += as_float(row["units"]) or 0.0
            if as_int(row["rank"]) == 1:
                entry["top_share_pct"] = _round(row["share"])
            if named and len(entry["top"]) < named:
                entry["top"].append({"clinic": row["card_name"], "revenue": as_float(row["revenue"]),
                                     "units": as_float(row["units"]), "share_pct": _round(row["share"])})
        return ClinicSellOut(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label, "top_n": named})],
            period=label,
            by_distributor=list(grouped.values()),
            **base,
        )

    return await _gold(user, ClinicSellOut, SELLOUT_CLINICA_NOTE, SELLOUT_CLINIC_DATASET, _CLINIC_REQUIRED, compute)


@dataclass
class BatchExpiry(B1Result):
    as_of: str | None = None
    levels: dict[str, dict[str, float | int | None]] = field(default_factory=dict)
    at_risk_value: float | None = None
    options: dict[str, int] = field(default_factory=dict)
    priorities: list[dict[str, Any]] = field(default_factory=list)


async def query_caducidad_lotes(user: dict | None, *, top_n: int = 10) -> BatchExpiry:
    limit = clamp_top_n(top_n, default=10)

    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> BatchExpiry:
        levels = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.caducidad_lotes.levels
            SELECT alert_level, COUNT(*)::bigint AS batches, COALESCE(SUM(value_local), 0)::float8 AS value,
                   COALESCE(SUM(at_risk_value_local), 0)::float8 AS at_risk, MAX(as_of_date)::date AS as_of
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND alert_level IS NOT NULL
             GROUP BY alert_level
        """, *scope.scope_args)
        options = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.caducidad_lotes.options
            SELECT action_option, COUNT(*)::bigint AS batches
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND action_option IS NOT NULL
             GROUP BY action_option
        """, *scope.scope_args)
        top = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.caducidad_lotes.priorities
            SELECT company, item_code, batch, branch_name, alert_level, days_to_expiry::bigint AS days,
                   at_risk_qty::float8 AS at_risk_qty, at_risk_value_local::float8 AS at_risk_value,
                   action_option, transfer_branch_name, transfer_candidate, priority_rank::bigint AS rank
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND priority_rank IS NOT NULL
             ORDER BY at_risk_value_local DESC, company, priority_rank
             LIMIT $3
        """, *scope.scope_args, limit)
        if not levels and not top:
            return BatchExpiry(status=STATUS_DEGRADED, notes=["sin lotes por caducar dentro del horizonte"],
                               evidence_refs=[gold_evidence(rel, filters={})], **base)
        as_of_value = max((row["as_of"] for row in levels if row["as_of"]), default=None)
        breaches = []
        by_level = {row["alert_level"]: {"batches": as_int(row["batches"]), "value": as_float(row["value"]),
                                         "at_risk_value": as_float(row["at_risk"])} for row in levels}
        if by_level.get("vencido", {}).get("value"):
            breaches.append(f"Hay {by_level['vencido']['batches']} lotes ya vencidos con existencia por "
                            f"{_money(by_level['vencido']['value'])}.")
        priorities = []
        for row in top:
            option = row["action_option"]
            if option == "traslado_filial":
                action = f"trasladar a la filial {row['transfer_branch_name']}"
            elif option == "traslado_empresa":
                action = f"trasladar a {row['transfer_candidate']}"
            else:
                action = "promoción"
            priorities.append({"company": row["company"], "item": row["item_code"], "batch": row["batch"],
                               "branch": row["branch_name"], "level": row["alert_level"], "days_to_expiry": as_int(row["days"]),
                               "at_risk_qty": as_float(row["at_risk_qty"]), "at_risk_value": as_float(row["at_risk_value"]),
                               "option": option, "action": action})
            if row["alert_level"] in ("rojo", "vencido"):
                breaches.append(
                    f"{row['company']}: lote {row['batch']} de {row['item_code']} ({row['branch_name']}) caduca en "
                    f"{as_int(row['days'])} días con {_money(row['at_risk_value'])} en riesgo; opción: {action}."
                )
        return BatchExpiry(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"as_of": as_of_value, "top_n": limit})],
            breaches=breaches,
            as_of=_iso(as_of_value),
            levels=by_level,
            at_risk_value=sum(item.get("at_risk_value") or 0.0 for level, item in by_level.items() if level != "vencido"),
            options={row["action_option"]: as_int(row["batches"]) or 0 for row in options},
            priorities=priorities,
            **base,
        )

    return await _gold(user, BatchExpiry, CADUCIDAD_NOTE, EXPIRY_DATASET, _EXPIRY_REQUIRED, compute)


# ---------------------------------------------------------------- compras


@dataclass
class Coverage(B1Result):
    as_of: str | None = None
    colors: dict[str, int] = field(default_factory=dict)
    stockout_risk: int | None = None
    critical_at_risk: int | None = None
    plan_changes: int | None = None
    suggestions: int | None = None
    suggested_value: float | None = None
    risks: list[dict[str, Any]] = field(default_factory=list)


async def query_dias_cobertura(user: dict | None, *, top_n: int = 10) -> Coverage:
    limit = clamp_top_n(top_n, default=10)

    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> Coverage:
        totals = await scope.conn.fetchrow(f"""
            -- omega-aggregate: sap_b1.dias_cobertura.totals
            SELECT MAX(as_of_date)::date AS as_of,
                   COUNT(*) FILTER (WHERE coverage_color = 'rojo')::bigint AS red,
                   COUNT(*) FILTER (WHERE coverage_color = 'amarillo')::bigint AS yellow,
                   COUNT(*) FILTER (WHERE coverage_color = 'verde')::bigint AS green,
                   COUNT(*) FILTER (WHERE coverage_color = 'sin_consumo')::bigint AS idle,
                   COUNT(*) FILTER (WHERE stockout_risk)::bigint AS stockout,
                   COUNT(*) FILTER (WHERE is_critical AND coverage_color IN ('rojo', 'amarillo'))::bigint AS critical,
                   COUNT(*) FILTER (WHERE suggested_qty > 0)::bigint AS suggestions,
                   COALESCE(SUM(suggested_value_local) FILTER (WHERE suggested_qty > 0), 0)::float8 AS suggested_value
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
        """, *scope.scope_args)
        as_of_value = totals["as_of"] if totals else None
        if as_of_value is None:
            return Coverage(status=STATUS_DEGRADED, notes=["sin existencias publicadas"],
                            evidence_refs=[gold_evidence(rel, filters={})], **base)
        changed_since = as_of_value - timedelta(days=PLAN_CHANGE_DAYS)
        risks = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.dias_cobertura.risks
            SELECT company, item_code, item_name, coverage_color, stockout_risk,
                   coverage_with_orders_days::float8 AS coverage_days, lead_time_days::bigint AS lead_time_days,
                   stockout_date::date AS stockout_date, suggested_qty::float8 AS suggested_qty, suggested_action,
                   suggested_supplier, alternate_supplier, order_by_date::date AS order_by_date,
                   consumption_basis, is_critical, criticality_rank::bigint AS criticality_rank,
                   plan_updated_at IS NOT NULL AND plan_updated_at::date >= $4::date AS plan_changed
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND (coverage_color = 'rojo' OR stockout_risk)
             ORDER BY stockout_risk DESC, coverage_with_orders_days ASC NULLS LAST, company, item_code
             LIMIT $3
        """, *scope.scope_args, limit, changed_since)
        changes = await scope.conn.fetchval(f"""
            -- omega-aggregate: sap_b1.dias_cobertura.plan_changes
            SELECT COUNT(*)::bigint FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND plan_updated_at IS NOT NULL AND plan_updated_at::date >= $3::date
        """, *scope.scope_args, changed_since)
        breaches, items = [], []
        for row in risks:
            options = []
            if row["suggested_action"] == "comprar":
                options.append("adelantar la orden de compra")
                if row["alternate_supplier"]:
                    options.append(f"proveedor alterno {row['alternate_supplier']}")
            elif row["suggested_action"] == "producir":
                options.append("programar producción")
            item = {"company": row["company"], "item": row["item_code"], "name": row["item_name"], "color": row["coverage_color"],
                    "stockout_risk": bool(row["stockout_risk"]), "coverage_days": _round(row["coverage_days"], 1),
                    "lead_time_days": as_int(row["lead_time_days"]), "stockout_date": _iso(row["stockout_date"]),
                    "suggested_qty": as_float(row["suggested_qty"]), "action": row["suggested_action"],
                    "supplier": row["suggested_supplier"], "alternate_supplier": row["alternate_supplier"],
                    "order_by": _iso(row["order_by_date"]), "basis": row["consumption_basis"],
                    "critical": bool(row["is_critical"]), "plan_changed": bool(row["plan_changed"]), "options": options}
            items.append(item)
            prefix = "El plan de producción cambió: " if row["plan_changed"] else ""
            breaches.append(
                f"{prefix}{row['company']}: {row['item_code']} alcanza {item['coverage_days']} días con órdenes "
                f"(entrega {item['lead_time_days']} días)" + (f"; opciones: {', '.join(options)}." if options else ".")
            )
        return Coverage(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"as_of": as_of_value, "top_n": limit})],
            breaches=breaches,
            as_of=_iso(as_of_value),
            colors={"rojo": as_int(totals["red"]) or 0, "amarillo": as_int(totals["yellow"]) or 0,
                    "verde": as_int(totals["green"]) or 0, "sin_consumo": as_int(totals["idle"]) or 0},
            stockout_risk=as_int(totals["stockout"]),
            critical_at_risk=as_int(totals["critical"]),
            plan_changes=as_int(changes),
            suggestions=as_int(totals["suggestions"]),
            suggested_value=as_float(totals["suggested_value"]),
            risks=items,
            **base,
        )

    return await _gold(user, Coverage, COBERTURA_NOTE, COVERAGE_DATASET, _COVERAGE_REQUIRED, compute)


@dataclass
class PurchaseNeed(B1Result):
    items_with_need: int | None = None
    items_short: int | None = None
    shortfalls: list[dict[str, Any]] = field(default_factory=list)


async def query_oc_vs_necesidad(user: dict | None, *, top_n: int = 10) -> PurchaseNeed:
    limit = clamp_top_n(top_n, default=10)

    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> PurchaseNeed:
        totals = await scope.conn.fetchrow(f"""
            -- omega-aggregate: sap_b1.oc_vs_necesidad.totals
            SELECT COUNT(*) FILTER (WHERE net_need_qty > 0)::bigint AS with_need,
                   COUNT(*) FILTER (WHERE net_need_qty > 0 AND open_po_qty < net_need_qty)::bigint AS short
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND suggested_action IS DISTINCT FROM 'producir'
        """, *scope.scope_args)
        rows = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.oc_vs_necesidad.shortfalls
            SELECT company, item_code, item_name, net_need_qty::float8 AS need, open_po_qty::float8 AS open_po,
                   open_po_vs_need_pct::float8 AS pct, is_critical
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND net_need_qty > 0 AND open_po_qty < net_need_qty
               AND suggested_action IS DISTINCT FROM 'producir'
             ORDER BY is_critical DESC, open_po_vs_need_pct ASC NULLS FIRST, company, item_code
             LIMIT $3
        """, *scope.scope_args, limit)
        return PurchaseNeed(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"top_n": limit})],
            breaches=[f"{row['company']}: {row['item_code']} tiene órdenes abiertas por {_round(row['pct'])} % de su necesidad."
                      for row in rows if row["is_critical"]],
            items_with_need=as_int(totals["with_need"]),
            items_short=as_int(totals["short"]),
            shortfalls=[{"company": row["company"], "item": row["item_code"], "name": row["item_name"],
                         "net_need_qty": as_float(row["need"]), "open_po_qty": as_float(row["open_po"]),
                         "coverage_pct": _round(row["pct"]), "critical": bool(row["is_critical"])} for row in rows],
            **base,
        )

    return await _gold(user, PurchaseNeed, OC_NECESIDAD_NOTE, COVERAGE_DATASET, _COVERAGE_REQUIRED, compute)


@dataclass
class CostVariance(B1Result):
    items: int | None = None
    above_threshold: int | None = None
    variance_value: float | None = None
    worst: list[dict[str, Any]] = field(default_factory=list)


async def query_costo_real_vs_estandar(user: dict | None, *, as_of: date | None = None, top_n: int = 10) -> CostVariance:
    limit = clamp_top_n(top_n, default=10)

    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> CostVariance:
        period = await _closed_month(scope, rel, as_of, "costo_real_vs_estandar")
        if period is None:
            return _no_period(CostVariance, base, rel)
        totals = await scope.conn.fetchrow(f"""
            -- omega-aggregate: sap_b1.costo_real_vs_estandar.totals
            SELECT COUNT(*)::bigint AS items, COUNT(*) FILTER (WHERE above_threshold)::bigint AS above,
                   COALESCE(SUM(variance_value_local), 0)::float8 AS variance
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date = $3::date
        """, *scope.scope_args, period)
        rows = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.costo_real_vs_estandar.worst
            SELECT company, item_code, item_name, variance_pct::float8 AS pct, variance_value_local::float8 AS value,
                   max_variance_pct::float8 AS max_pct, is_raw_material
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date = $3::date AND above_threshold
             ORDER BY abs(variance_value_local) DESC, company, item_code
             LIMIT $4
        """, *scope.scope_args, period, limit)
        label = _label(period)
        return CostVariance(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"period": label, "top_n": limit})],
            period=label,
            breaches=[f"{row['company']}: {row['item_code']} se compró {_round(row['pct'])} % contra su costo en "
                      f"{label} (máximo {_round(row['max_pct'])} %), {_money(row['value'])} de diferencia." for row in rows],
            items=as_int(totals["items"]),
            above_threshold=as_int(totals["above"]),
            variance_value=as_float(totals["variance"]),
            worst=[{"company": row["company"], "item": row["item_code"], "name": row["item_name"], "variance_pct": _round(row["pct"]),
                    "variance_value": as_float(row["value"]), "raw_material": bool(row["is_raw_material"])} for row in rows],
            **base,
        )

    return await _gold(user, CostVariance, COSTO_NOTE, COST_VARIANCE_DATASET, _COST_REQUIRED, compute)


@dataclass
class SupplierLeadTime(B1Result):
    suppliers: int | None = None
    late_suppliers: list[dict[str, Any]] = field(default_factory=list)


async def query_lead_time_proveedores(user: dict | None, *, as_of: date | None = None, top_n: int = 10) -> SupplierLeadTime:
    limit = clamp_top_n(top_n, default=10)

    async def compute(scope: GoldScope, rel: Any, base: dict[str, Any]) -> SupplierLeadTime:
        end = month_start(as_of_date(as_of))
        start = add_months(end, -LEAD_TIME_MONTHS)
        rows = await scope.conn.fetch(f"""
            -- omega-aggregate: sap_b1.lead_time_proveedores.suppliers
            SELECT company, card_code, MAX(supplier_name) AS supplier_name, bool_or(is_intercompany) AS intercompany,
                   SUM(receipts)::bigint AS receipts, SUM(late_receipts)::bigint AS late,
                   MAX(max_delay_days)::bigint AS max_delay,
                   (SUM(avg_lead_days * receipts) / NULLIF(SUM(receipts), 0))::float8 AS lead_days,
                   (SUM(avg_promised_days * receipts) / NULLIF(SUM(receipts), 0))::float8 AS promised_days
              FROM {rel.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND doc_month::date >= $3::date AND doc_month::date < $4::date
             GROUP BY company, card_code
             ORDER BY SUM(late_receipts) DESC, company, card_code
             LIMIT $5
        """, *scope.scope_args, start, end, clamp_top_n(200, upper=500))
        late = [row for row in rows if (as_int(row["late"]) or 0) > 0]
        return SupplierLeadTime(
            status=status_for(rel),
            evidence_refs=[gold_evidence(rel, filters={"window_start": start, "window_end_exclusive": end})],
            period=f"{_label(start)} a {_label(add_months(end, -1))}",
            breaches=[f"{row['company']}: el proveedor {row['supplier_name'] or row['card_code']} entregó tarde "
                      f"{as_int(row['late'])} de {as_int(row['receipts'])} recepciones (hasta {as_int(row['max_delay'])} días)."
                      for row in late[:limit]],
            suppliers=len(rows),
            late_suppliers=[{"company": row["company"], "supplier": row["supplier_name"] or row["card_code"],
                             "intercompany": bool(row["intercompany"]), "receipts": as_int(row["receipts"]),
                             "late_receipts": as_int(row["late"]), "on_time_pct": _pct((as_int(row["receipts"]) or 0) - (as_int(row["late"]) or 0), as_int(row["receipts"])),
                             "avg_lead_days": _round(row["lead_days"], 1), "avg_promised_days": _round(row["promised_days"], 1),
                             "max_delay_days": as_int(row["max_delay"])} for row in late[:limit]],
            **base,
        )

    return await _gold(user, SupplierLeadTime, LEAD_TIME_NOTE, LEAD_TIME_DATASET, _LEAD_REQUIRED, compute)


__all__ = [
    "CONSOLIDATED_DATASET",
    "COST_VARIANCE_DATASET",
    "COVERAGE_DATASET",
    "DATA_QUALITY_DATASET",
    "DETAIL_DATASET",
    "ENTITY_MODEL_DATASET",
    "EXPIRY_DATASET",
    "KPIS_DATASET",
    "KPI_RECONCILIATION_DATASET",
    "LEAD_TIME_DATASET",
    "LEDGER_DATASET",
    "LOAD_DATASET",
    "SCORECARD_DATASET",
    "SELLOUT_CLINIC_DATASET",
    "APRENDIZAJE_NOTE",
    "query_caducidad_lotes",
    "query_calidad_datos",
    "query_concentracion_top20",
    "query_costo_real_vs_estandar",
    "query_destructores",
    "query_dias_cobertura",
    "query_dias_inventario",
    "query_lead_time_proveedores",
    "query_margen_bruto",
    "query_margen_contribucion",
    "query_margen_vendedor",
    "query_modelo_entidades",
    "query_oc_vs_necesidad",
    "query_ratio_sellout_sellin",
    "query_reconciliacion_finanzas",
    "query_sellout_clinica",
    "query_semaforo_distribuidoras",
]
