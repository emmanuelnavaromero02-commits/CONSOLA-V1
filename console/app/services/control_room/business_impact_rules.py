from __future__ import annotations

from collections.abc import Callable
from typing import Any


ImpactPayload = Callable[..., dict[str, Any]]
NumberParser = Callable[[Any], float | None]


def calculate_item_impact(
    item: dict[str, Any],
    *,
    number: NumberParser,
    payload: ImpactPayload,
) -> dict[str, Any]:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    anomaly_type = str(item.get("anomaly_type") or "")
    cartridge = str(item.get("cartridge") or "")
    replicon_financial = cartridge == "replicon" and anomaly_type in {
        "low_margin",
        "wip_variance",
        "non_billable_ratio",
    }
    if replicon_financial and details.get("financial_status") != "ready":
        return payload(
            item=item,
            estimate=None,
            status="unavailable",
            confidence=0.25,
            drivers=[],
            formula="FX/base currency provenance required.",
            explanation="La fuente Replicon no acredita moneda base y FX publicables.",
        )

    stored = number(item.get("impact_estimate"))
    if stored is not None and stored > 0:
        return payload(
            item=item,
            estimate=stored,
            status="ok",
            confidence=number(item.get("confidence")) or 0.6,
            drivers=[
                {
                    "label": "Impacto persistido",
                    "value": stored,
                    "currency": item.get("impact_currency") or "USD",
                }
            ],
            formula="Impacto persistido en control_room_items.",
            explanation="Estimacion recuperada del estado operativo persistido.",
            currency=str(item.get("impact_currency") or "USD"),
        )

    if cartridge == "replicon" and anomaly_type in {"low_margin", "wip_variance"}:
        revenue = number(details.get("revenue_usd"))
        margin_usd = number(details.get("margen_bruto_usd"))
        wip = number(details.get("wip_usd")) or 0
        margin_gap = 0.0
        if revenue is not None and margin_usd is not None:
            margin_gap = max(0.0, revenue * 0.20 - margin_usd)
        exposure = margin_gap + (abs(wip) if abs(wip) >= 5000 else 0)
        if exposure > 0:
            return payload(
                item=item,
                estimate=exposure,
                status="ok",
                confidence=0.78,
                drivers=[
                    {
                        "label": "Brecha margen objetivo 20%",
                        "value": round(margin_gap, 2),
                        "currency": "USD",
                    },
                    {
                        "label": "WIP bajo revision",
                        "value": round(abs(wip), 2),
                        "currency": "USD",
                    },
                ],
                formula="max(0, revenue_usd * 20% - margen_bruto_usd) + abs(wip_usd si >= 5000)",
                explanation="Usa P&L Replicon materializado; no escribe en Replicon.",
            )

    if cartridge == "replicon" and anomaly_type == "non_billable_ratio":
        hours = number(details.get("horas_no_facturables"))
        rate = (
            number(details.get("billing_rate_usd"))
            or number(details.get("billing_rate"))
            or number(details.get("rate_usd"))
            or number(details.get("currenthourlybillingamount"))
        )
        if hours is not None and rate is not None:
            return payload(
                item=item,
                estimate=hours * rate,
                status="ok",
                confidence=0.7,
                drivers=[
                    {"label": "Horas no facturables", "value": round(hours, 2)},
                    {
                        "label": "Tarifa Replicon",
                        "value": round(rate, 2),
                        "currency": "USD",
                    },
                ],
                formula="horas_no_facturables * tarifa_replicon",
                explanation="Calcula exposicion de horas no facturables con tarifa real disponible.",
            )

    if cartridge == "sap_s4hana" and anomaly_type == "negative_revenue":
        revenue = number(details.get("revenue"))
        if revenue is not None:
            return payload(
                item=item,
                estimate=abs(revenue),
                status="ok",
                confidence=0.74,
                drivers=[
                    {"label": "Revenue negativo", "value": revenue, "currency": "USD"}
                ],
                formula="abs(revenue)",
                explanation="Usa revenue materializado por cliente/periodo.",
            )

    if cartridge == "sap_s4hana" and anomaly_type == "aged_sales_backlog":
        open_value = number(details.get("open_value"))
        if open_value is not None:
            return payload(
                item=item,
                estimate=open_value,
                status="ok",
                confidence=0.62,
                drivers=[
                    {
                        "label": "Backlog abierto",
                        "value": round(open_value, 2),
                        "currency": "USD",
                    },
                    {
                        "label": "Antiguedad maxima",
                        "value": number(details.get("oldest_age_days")) or 0,
                        "unit": "dias",
                    },
                ],
                formula="open_value",
                explanation="Exposicion comercial, no perdida confirmada.",
            )

    if cartridge == "sap_s4hana" and anomaly_type == "supplier_spend_concentration":
        spend = number(details.get("total_spend"))
        if spend is not None:
            return payload(
                item=item,
                estimate=spend,
                status="ok",
                confidence=0.45,
                drivers=[
                    {
                        "label": "Gasto concentrado",
                        "value": round(spend, 2),
                        "currency": "USD",
                    }
                ],
                formula="total_spend",
                explanation="Exposicion de compras bajo revision, no ahorro garantizado.",
            )

    if cartridge == "sap_s4hana" and anomaly_type in {
        "missing_address",
        "missing_tax_id",
        "duplicate_business_partner",
    }:
        exposure = (
            number(details.get("open_value"))
            or number(details.get("balance_usd"))
            or number(details.get("exposure_usd"))
            or number(details.get("total_spend"))
        )
        if exposure is not None and exposure > 0:
            return payload(
                item=item,
                estimate=exposure,
                status="ok",
                confidence=0.52,
                drivers=[
                    {
                        "label": "Exposicion BP",
                        "value": round(exposure, 2),
                        "currency": "USD",
                    },
                    {"label": "Tipo maestro", "value": anomaly_type},
                ],
                formula="open_value | balance_usd | exposure_usd | total_spend",
                explanation="Usa exposicion comercial/proveedor disponible para el maestro BP.",
            )

    if cartridge == "sap_hcm" and anomaly_type == "terminated_but_active":
        monthly_cost = (
            number(details.get("monthly_cost_usd"))
            or number(details.get("salary_monthly_usd"))
            or number(details.get("costo_mensual_usd"))
        )
        if monthly_cost is not None and monthly_cost > 0:
            return payload(
                item=item,
                estimate=monthly_cost * 3,
                status="ok",
                confidence=0.66,
                drivers=[
                    {
                        "label": "Costo mensual empleado",
                        "value": round(monthly_cost, 2),
                        "currency": "USD",
                    },
                    {"label": "Ventana control", "value": 3, "unit": "meses"},
                ],
                formula="monthly_cost_usd * 3 meses de exposicion",
                explanation="Estima cola de costo/acceso para empleado terminado pero activo.",
            )

    if cartridge == "sap_successfactors" and anomaly_type in {
        "missing_manager",
        "missing_department",
        "missing_job_code",
    }:
        affected = (
            number(details.get("affected_employees"))
            or number(details.get("direct_reports"))
            or number(details.get("headcount"))
        )
        monthly_cost = number(details.get("avg_monthly_cost_usd")) or number(
            details.get("salary_monthly_usd")
        )
        if (
            affected is not None
            and monthly_cost is not None
            and affected > 0
            and monthly_cost > 0
        ):
            return payload(
                item=item,
                estimate=affected * monthly_cost * 0.15,
                status="ok",
                confidence=0.48,
                drivers=[
                    {"label": "Personas afectadas", "value": round(affected, 2)},
                    {
                        "label": "Costo mensual promedio",
                        "value": round(monthly_cost, 2),
                        "currency": "USD",
                    },
                ],
                formula="affected_employees * avg_monthly_cost_usd * 15%",
                explanation="Proxy de riesgo operativo SF cuando hay base de costo y poblacion afectada.",
            )

    monthly_cost = (
        number(details.get("monthly_cost_usd"))
        or number(details.get("salary_monthly_usd"))
        or number(details.get("costo_mensual_usd"))
    )
    if monthly_cost is not None and monthly_cost > 0:
        return payload(
            item=item,
            estimate=monthly_cost,
            status="ok",
            confidence=0.55,
            drivers=[
                {
                    "label": "Costo mensual",
                    "value": round(monthly_cost, 2),
                    "currency": "USD",
                }
            ],
            formula="monthly_cost_usd",
            explanation="Usa costo directo disponible en la fuente.",
        )

    return payload(
        item=item,
        estimate=None,
        status="unavailable",
        confidence=0.25,
        drivers=[],
        formula="Sin base monetaria disponible en el dataset.",
        explanation="La senal es operativa; falta cost basis para convertirla a dinero sin inventar cifras.",
    )


__all__ = ("calculate_item_impact",)
