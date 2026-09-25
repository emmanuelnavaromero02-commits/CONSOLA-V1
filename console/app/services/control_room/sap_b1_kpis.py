"""SAP Business One KPI views for the Control Room bridge (finance case)."""

from __future__ import annotations

from typing import Any

from app.services.control_room.domain_kpis import (
    SAP_B1_EXPIRY_METRICS,
    SAP_B1_MARGIN_METRICS,
    SAP_B1_SALES_METRICS,
    domain_payload,
)
from app.services.intelligence import sap_b1_aggregates
from app.services.intelligence.domain_aggregate_support import AggregateResult, clamp_named_rows

SAP_B1_MARGIN_DOMAIN = "sap_b1_margin"
SAP_B1_SALES_DOMAIN = "sap_b1_sales"
SAP_B1_EXPIRY_DOMAIN = "sap_b1_expiry"


async def sap_b1_margin_kpis(user: dict | None, *, top_n: int = 0) -> dict[str, Any]:
    named_rows = clamp_named_rows(top_n)
    results: dict[str, AggregateResult] = {
        "group_margin": await sap_b1_aggregates.query_group_margin(user),
        "company_margin": await sap_b1_aggregates.query_company_margin(user),
        "customer_margin": await sap_b1_aggregates.query_customer_margin(user, top_n=named_rows),
        "item_family_margin": await sap_b1_aggregates.query_item_family_margin(user),
        "below_min_sales": await sap_b1_aggregates.query_below_min_sales(user),
        "reconciliation": await sap_b1_aggregates.query_reconciliation(user),
        "data_quality": await sap_b1_aggregates.query_data_quality(user),
    }
    return domain_payload(SAP_B1_MARGIN_DOMAIN, results, named_rows=named_rows)


async def sap_b1_sales_kpis(user: dict | None) -> dict[str, Any]:
    results: dict[str, AggregateResult] = {
        "distributor_scorecard": await sap_b1_aggregates.query_distributor_scorecard(user),
    }
    return domain_payload(SAP_B1_SALES_DOMAIN, results)


async def sap_b1_expiry_kpis(user: dict | None) -> dict[str, Any]:
    results: dict[str, AggregateResult] = {
        "batch_expiry": await sap_b1_aggregates.query_batch_expiry(user),
    }
    return domain_payload(SAP_B1_EXPIRY_DOMAIN, results)


__all__ = [
    "SAP_B1_EXPIRY_DOMAIN",
    "SAP_B1_EXPIRY_METRICS",
    "SAP_B1_MARGIN_DOMAIN",
    "SAP_B1_MARGIN_METRICS",
    "SAP_B1_SALES_DOMAIN",
    "SAP_B1_SALES_METRICS",
    "sap_b1_expiry_kpis",
    "sap_b1_margin_kpis",
    "sap_b1_sales_kpis",
]
