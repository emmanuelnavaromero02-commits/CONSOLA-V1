from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.services.control_room.business_policy_metadata import business_policy_metadata


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def business_item_metadata(
    item: Mapping[str, Any],
    impact: Mapping[str, Any],
    *,
    decision_intelligence: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    existing = _mapping(item.get("metadata"))
    metadata = {
        **existing,
        "module": item.get("module"),
        "description": item.get("description"),
        "recommendation": item.get("recommendation"),
        "root_cause": item.get("root_cause"),
        "impact": item.get("impact"),
        "details": {
            **_mapping(existing.get("details")),
            **_mapping(item.get("details")),
        },
        "sql": item.get("sql"),
        "impact_payload": dict(impact),
        "thresholds_applied": item.get("thresholds_applied") or [],
        "threshold_state": item.get("threshold_state") or "default",
    }
    for key in (
        "control_origin",
        "advisory",
        "hypothesis",
        "expected_outcome",
        "capabilities",
        "priority",
        "math_provenance",
        "monte_carlo",
        "bayesian_calibration",
    ):
        value = item.get(key)
        if value is not None and value != "":
            metadata[key] = value
    for key, limit in (
        ("learned_rules", 10),
        ("lessons", 10),
        ("lesson_applications", 20),
    ):
        value = item.get(key)
        if isinstance(value, list) and value:
            metadata[key] = value[:limit]
    for key in ("alert_state", "control_state"):
        value = item.get(key)
        if isinstance(value, Mapping) and value:
            metadata[key] = dict(value)

    intelligence = _mapping(item.get("intelligence"))
    decision = dict(decision_intelligence(item))
    if decision:
        metadata["decision_intelligence"] = decision
        intelligence = {**intelligence, "decision_intelligence": decision}
    if intelligence:
        metadata["intelligence"] = intelligence
    return business_policy_metadata(metadata, item)


__all__ = ("business_item_metadata",)
