from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def build_impact_payload(
    *,
    item: Mapping[str, Any],
    estimate: float | None,
    status: str,
    confidence: float | None,
    drivers: list[dict[str, Any]],
    formula: str,
    explanation: str,
    severity_weights: Mapping[str, int],
    currency: str = "USD",
) -> dict[str, Any]:
    estimate_value = round(float(estimate or 0), 2) if estimate is not None else None
    severity_weight = severity_weights.get(str(item.get("severity") or "medium"), 2)
    # F11: None means no real confidence exists — it contributes nothing to
    # priority and surfaces as null ("sin dato"), never as an invented score.
    confidence_points = 0 if confidence is None else int(confidence * 20)
    impact_points = (
        0 if estimate_value is None else min(42, int(abs(estimate_value) / 10_000))
    )
    threshold_state = str(item.get("threshold_state") or "")
    threshold_points = {"critical": 12, "warning": 6}.get(threshold_state, 0)
    priority_score = min(
        100,
        max(
            0,
            severity_weight * 14
            + impact_points
            + confidence_points
            + threshold_points,
        ),
    )
    return {
        "item_id": item.get("id"),
        "status": status,
        "estimate": estimate_value,
        "currency": currency,
        "confidence": (
            None if confidence is None else round(max(0.0, min(1.0, confidence)), 2)
        ),
        "priority_score": priority_score,
        "drivers": drivers,
        "formula": formula,
        "explanation": explanation,
    }


def build_priority_payload(
    item: Mapping[str, Any],
    impact: Mapping[str, Any],
    *,
    severity_weights: Mapping[str, int],
    terminal_statuses: set[str],
) -> dict[str, Any]:
    score = int(impact.get("priority_score") or 0)
    drivers: list[dict[str, Any]] = [
        {
            "label": "Severidad",
            "value": item.get("severity") or "medium",
            "points": severity_weights.get(str(item.get("severity") or "medium"), 2)
            * 14,
        }
    ]
    if impact.get("status") == "ok" and impact.get("estimate") is not None:
        drivers.append(
            {
                "label": "Impacto economico",
                "value": impact.get("estimate"),
                "currency": impact.get("currency") or "USD",
                "points": min(
                    42, int(abs(float(impact.get("estimate") or 0)) / 10_000)
                ),
            }
        )
    else:
        drivers.append(
            {"label": "Impacto economico", "value": "no disponible", "points": 0}
        )

    threshold_state = str(item.get("threshold_state") or "default")
    threshold_points = {"critical": 16, "warning": 8}.get(threshold_state, 0)
    if threshold_points:
        drivers.append(
            {"label": "Umbral", "value": threshold_state, "points": threshold_points}
        )
        score += threshold_points

    lesson_count = int(item.get("lesson_count") or 0)
    lesson_points = min(12, lesson_count * 4)
    if lesson_points:
        drivers.append(
            {
                "label": "Patron aprendido",
                "value": lesson_count,
                "points": lesson_points,
            }
        )
        score += lesson_points

    if str(item.get("status") or "open") in terminal_statuses:
        drivers.append(
            {"label": "Estado cerrado", "value": item.get("status"), "points": -35}
        )
        score -= 35

    score = max(0, min(100, score))
    band = (
        "critical"
        if score >= 90
        else "high"
        if score >= 75
        else "medium"
        if score >= 55
        else "low"
    )
    return {
        "score": score,
        "band": band,
        "drivers": drivers,
        "formula": "severity + impact + confidence + thresholds + learned_patterns",
    }


__all__ = ("build_impact_payload", "build_priority_payload")
