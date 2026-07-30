from __future__ import annotations

from typing import Any

from app.services.intelligence.utils import num


def decision_options(
    signal: dict[str, Any], metric: dict[str, Any]
) -> list[dict[str, Any]]:
    templates = (
        metric.get("action_templates")
        if isinstance(metric.get("action_templates"), list)
        else []
    )
    options: list[dict[str, Any]] = []
    deviation_value = num(signal.get("deviation_value"))
    confidence = num(signal.get("confidence"))
    impact_config = (
        metric.get("impact") if isinstance(metric.get("impact"), dict) else {}
    )
    unit_value = num(impact_config.get("unit_value"))
    if deviation_value is None or confidence is None or unit_value is None:
        return []
    impact_base = abs(deviation_value)
    for template in templates:
        if not isinstance(template, dict):
            continue
        identity = (
            template.get("id"),
            template.get("label"),
            template.get("action_kind"),
        )
        if not all(isinstance(value, str) and value.strip() for value in identity):
            continue
        option_id, label, action_kind = (value.strip() for value in identity)
        impact_multiplier = num(template.get("impact_multiplier"))
        cost = num(template.get("cost"))
        risk = num(template.get("risk"))
        time_cost = num(template.get("time_cost"))
        if any(value is None for value in (impact_multiplier, cost, risk, time_cost)):
            continue
        expected_impact = round(impact_base * impact_multiplier * unit_value, 2)
        score = round(expected_impact * confidence - cost - risk - time_cost, 2)
        options.append(
            {
                "option_id": option_id,
                "label": label,
                "action_kind": action_kind,
                "impact_expected": expected_impact,
                "confidence": confidence,
                "cost": cost,
                "risk": risk,
                "time_cost": time_cost,
                "score": score,
                "selected": False,
                "score_explanation": "score = impacto_esperado * confianza - costo - riesgo - tiempo",
            }
        )
    options.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
    return options[:3]
