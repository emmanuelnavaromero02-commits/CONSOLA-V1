from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from app.schemas.control_room_council import NO_ESTIMATE_LABEL, CouncilImpact
from app.schemas.control_room_surfaces import ExperienceMetric
from app.services.control_room.business_impact_rules import calculate_item_impact


PERSISTED_FORMULA = "Impacto persistido en control_room_items."
RULE_LABELS = {
    PERSISTED_FORMULA: "Estimación registrada con el hallazgo",
    "max(0, revenue_usd * 20% - margen_bruto_usd) + abs(wip_usd si >= 5000)": (
        "Regla: brecha vs margen objetivo 20 % más WIP en revisión"
    ),
    "horas_no_facturables * tarifa_replicon": (
        "Regla: horas no facturables × tarifa registrada"
    ),
    "abs(revenue)": "Regla: valor absoluto del ingreso negativo",
    "open_value": "Regla: valor abierto pendiente",
    "total_spend": "Regla: gasto total concentrado",
    "open_value | balance_usd | exposure_usd | total_spend": (
        "Regla: exposición comercial registrada del socio"
    ),
    "monthly_cost_usd * 3 meses de exposicion": "Regla: costo mensual × 3 meses",
    "affected_employees * avg_monthly_cost_usd * 15%": (
        "Regla: personas afectadas × costo mensual × 15 %"
    ),
    "monthly_cost_usd": "Regla: costo mensual directo",
}
HOUR_UNITS = frozenset({"h", "hr", "hrs", "hora", "horas", "hour", "hours"})
TIME_FORMULA = "Valor observado del indicador en horas"
TIME_LABEL = "Tiempo observado en el indicador"
_CURRENCY_LENGTH = 3


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _payload(**values: Any) -> dict[str, Any]:
    return {
        "estimate": values.get("estimate"),
        "status": values.get("status"),
        "formula": values.get("formula"),
        "currency": values.get("currency", "USD"),
    }


def no_estimate() -> CouncilImpact:
    return CouncilImpact(kind="none", label=NO_ESTIMATE_LABEL)


def _money(item: Mapping[str, Any]) -> CouncilImpact | None:
    try:
        result = calculate_item_impact(dict(item), number=_number, payload=_payload)
    except (TypeError, ValueError, KeyError):
        return None
    estimate = _number(result.get("estimate"))
    formula = str(result.get("formula") or "").strip()
    currency = str(result.get("currency") or "").strip().upper()
    if (
        result.get("status") != "ok"
        or estimate is None
        or estimate <= 0
        or not formula
        or len(currency) != _CURRENCY_LENGTH
        or not currency.isalpha()
    ):
        return None
    persisted = formula == PERSISTED_FORMULA
    return CouncilImpact(
        kind="money",
        value=round(estimate, 2),
        currency=currency,
        basis="persisted" if persisted else "rule",
        formula=None if persisted else formula[:240],
        label=RULE_LABELS.get(formula, f"Regla: {formula}")[:240],
    )


def _time(metric: ExperienceMetric | None) -> CouncilImpact | None:
    if metric is None or metric.unit is None:
        return None
    if metric.unit.strip().lower().rstrip(".") not in HOUR_UNITS:
        return None
    value = _number(metric.value)
    if value is None or value <= 0:
        return None
    return CouncilImpact(
        kind="time",
        value=round(value, 2),
        unit="horas",
        basis="observed",
        formula=TIME_FORMULA,
        label=TIME_LABEL,
    )


def council_impact(
    item: Mapping[str, Any], metric: ExperienceMetric | None
) -> CouncilImpact:
    return _money(item) or _time(metric) or no_estimate()


__all__ = ("HOUR_UNITS", "PERSISTED_FORMULA", "RULE_LABELS", "council_impact", "no_estimate")
