from __future__ import annotations

from typing import Any

from app.services.control_room.domain_kpis import (
    SAP_B1_EXPIRY_METRICS,
    SAP_B1_LEARNING_METRICS,
    SAP_B1_MARGIN_METRICS,
    SAP_B1_SALES_METRICS,
    SAP_B1_SEMAFORO_METRICS,
    SAP_B1_SUPPLY_METRICS,
    domain_payload,
)
from app.services.control_room.sap_b1_learning import query_aprendizaje
from app.services.intelligence import sap_b1_aggregates as agg
from app.services.intelligence.domain_aggregate_support import AggregateResult, clamp_named_rows

SAP_B1_MARGIN_DOMAIN = "sap_b1_margin"
SAP_B1_SALES_DOMAIN = "sap_b1_sales"
SAP_B1_EXPIRY_DOMAIN = "sap_b1_expiry"
SAP_B1_SUPPLY_DOMAIN = "sap_b1_supply"
SAP_B1_LEARNING_DOMAIN = "sap_b1_learning"
SAP_B1_SEMAFORO_DOMAIN = "sap_b1_semaforo"


async def sap_b1_margin_kpis(user: dict | None, *, top_n: int = 0) -> dict[str, Any]:
    named_rows = clamp_named_rows(top_n)
    results: dict[str, AggregateResult] = {
        "margen_bruto": await agg.query_margen_bruto(user),
        "margen_contribucion": await agg.query_margen_contribucion(user),
        "destructores": await agg.query_destructores(user, top_n=named_rows),
        "concentracion_top20": await agg.query_concentracion_top20(user),
        "margen_vendedor": await agg.query_margen_vendedor(user, top_n=named_rows),
        "reconciliacion_finanzas": await agg.query_reconciliacion_finanzas(user),
        "calidad_datos": await agg.query_calidad_datos(user),
        "modelo_entidades": await agg.query_modelo_entidades(user),
    }
    return domain_payload(SAP_B1_MARGIN_DOMAIN, results, named_rows=named_rows)


async def sap_b1_sales_kpis(user: dict | None, *, top_n: int = 0) -> dict[str, Any]:
    named_rows = clamp_named_rows(top_n)
    results: dict[str, AggregateResult] = {
        "ratio_sellout_sellin": await agg.query_ratio_sellout_sellin(user),
        "dias_inventario": await agg.query_dias_inventario(user),
        "sellout_clinica": await agg.query_sellout_clinica(user, top_n=named_rows),
        "semaforo_distribuidoras": await agg.query_semaforo_distribuidoras(user),
    }
    return domain_payload(SAP_B1_SALES_DOMAIN, results, named_rows=named_rows)


async def sap_b1_expiry_kpis(user: dict | None) -> dict[str, Any]:
    results: dict[str, AggregateResult] = {
        "caducidad_lotes": await agg.query_caducidad_lotes(user),
    }
    return domain_payload(SAP_B1_EXPIRY_DOMAIN, results)


async def sap_b1_supply_kpis(user: dict | None) -> dict[str, Any]:
    results: dict[str, AggregateResult] = {
        "dias_cobertura": await agg.query_dias_cobertura(user),
        "oc_vs_necesidad": await agg.query_oc_vs_necesidad(user),
        "costo_real_vs_estandar": await agg.query_costo_real_vs_estandar(user),
        "lead_time_proveedores": await agg.query_lead_time_proveedores(user),
    }
    return domain_payload(SAP_B1_SUPPLY_DOMAIN, results)


async def sap_b1_learning_kpis(user: dict | None) -> dict[str, Any]:
    results: dict[str, AggregateResult] = {"aprendizaje": await query_aprendizaje(user)}
    return domain_payload(SAP_B1_LEARNING_DOMAIN, results)


async def sap_b1_semaforo_kpis(user: dict | None) -> dict[str, Any]:
    results: dict[str, AggregateResult] = {
        "margen_bruto": await agg.query_margen_bruto(user),
        "destructores": await agg.query_destructores(user),
        "reconciliacion_finanzas": await agg.query_reconciliacion_finanzas(user),
        "semaforo_distribuidoras": await agg.query_semaforo_distribuidoras(user),
        "caducidad_lotes": await agg.query_caducidad_lotes(user),
        "dias_cobertura": await agg.query_dias_cobertura(user),
        "calidad_datos": await agg.query_calidad_datos(user),
    }
    return domain_payload(SAP_B1_SEMAFORO_DOMAIN, results)


__all__ = [
    "SAP_B1_EXPIRY_DOMAIN",
    "SAP_B1_EXPIRY_METRICS",
    "SAP_B1_LEARNING_DOMAIN",
    "SAP_B1_LEARNING_METRICS",
    "SAP_B1_MARGIN_DOMAIN",
    "SAP_B1_MARGIN_METRICS",
    "SAP_B1_SALES_DOMAIN",
    "SAP_B1_SALES_METRICS",
    "SAP_B1_SEMAFORO_DOMAIN",
    "SAP_B1_SEMAFORO_METRICS",
    "SAP_B1_SUPPLY_DOMAIN",
    "SAP_B1_SUPPLY_METRICS",
    "sap_b1_expiry_kpis",
    "sap_b1_learning_kpis",
    "sap_b1_margin_kpis",
    "sap_b1_sales_kpis",
    "sap_b1_semaforo_kpis",
    "sap_b1_supply_kpis",
]
