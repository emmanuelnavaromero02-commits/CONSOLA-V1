from __future__ import annotations

from typing import Any

from app.services.intelligence.utils import num


def decision_options(signal: dict[str, Any], metric: dict[str, Any]) -> list[dict[str, Any]]:
    templates = metric.get("action_templates") if isinstance(metric.get("action_templates"), list) else []
    options: list[dict[str, Any]] = []
    impact_base = abs(float(signal.get("deviation_value") or 0))
    confidence = float(signal.get("confidence") or 0.50)
    impact_config = metric.get("impact") if isinstance(metric.get("impact"), dict) else {}
    unit_value = num(impact_config.get("unit_value")) or 1.0
    for template in templates:
        if not isinstance(template, dict):
            continue
        option_id = str(template.get("id") or template.get("action_kind") or "option").strip()
        if not option_id:
            continue
        impact_multiplier = num(template.get("impact_multiplier")) or 0
        cost = num(template.get("cost")) or 0
        risk = num(template.get("risk")) or 0
        time_cost = num(template.get("time_cost")) or 0
        expected_impact = round(impact_base * impact_multiplier * unit_value, 2)
        score = round(expected_impact * confidence - cost - risk - time_cost, 2)
        options.append(
            {
                "option_id": option_id,
                "label": str(template.get("label") or option_id.replace("_", " ").title()),
                "action_kind": str(template.get("action_kind") or option_id),
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
