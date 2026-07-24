from __future__ import annotations

from collections.abc import Callable, Mapping, Set
from datetime import UTC, datetime
from typing import Any

from app.services.control_room.business_builder_policy import business_builder_allowed


def build_business_alert(
    item: dict[str, Any],
    *,
    eligible_parent_ids: Set[str] | None,
    terminal_statuses: Set[str],
    severity_weights: Mapping[str, int],
    alert_state_builder: Callable[[dict[str, Any]], dict[str, Any]],
    priority_builder: Callable[..., dict[str, Any]],
    alert_type_builder: Callable[[dict[str, Any]], str],
    alert_message_builder: Callable[[dict[str, Any], str], str],
    external_delivery_enabled: Callable[[], bool],
) -> dict[str, Any] | None:
    if not business_builder_allowed(item, eligible_parent_ids=eligible_parent_ids):
        return None
    if str(item.get("status") or "open") in terminal_statuses:
        return None
    alert_state = alert_state_builder(item)
    alert_status = str(alert_state.get("state") or "open")
    if alert_status == "false_positive":
        return None
    priority = (
        item["priority"]
        if isinstance(item.get("priority"), Mapping)
        else priority_builder(item, eligible_parent_ids=eligible_parent_ids)
    )
    score = int(priority.get("score") or item.get("priority_score") or 0)
    alert_type = alert_type_builder(item)
    is_agent_alert = item.get("kind") == "agent_alert" or item.get("source") == "agent"
    if not (
        is_agent_alert
        or alert_type in {"source_health", "threshold_breach", "learned_pattern"}
        or item.get("severity") in {"critical", "high"}
        or score >= 55
    ):
        return None
    severity = str(priority.get("band") or item.get("severity") or "medium")
    if severity not in severity_weights:
        severity = "medium"
    delivery_status = {
        "open": "not_configured",
        "acknowledged": "acknowledged",
        "assigned": "assigned",
        "snoozed": "snoozed",
    }.get(alert_status, "not_configured")
    delivery_enabled = external_delivery_enabled()
    push_ready = delivery_enabled and alert_status == "open"
    delivery_reason = (
        "Push externo habilitado para conectores de delivery."
        if push_ready
        else "Push externo deshabilitado en V1 hasta configurar conectores de delivery."
    )
    return {
        "id": f"alert:{item.get('id')}",
        "item_id": item.get("id"),
        "alert_type": alert_type,
        "source": item.get("source") or "system",
        "advisory": bool(item.get("advisory")),
        "agent_id": item.get("agent_id"),
        "agent_run_id": item.get("agent_run_id"),
        "analysis_type": item.get("analysis_type"),
        "engine": item.get("engine"),
        "engine_run_id": item.get("engine_run_id"),
        "analysis_evidence": (
            item.get("analysis_evidence")
            if isinstance(item.get("analysis_evidence"), Mapping)
            else {}
        ),
        "deduped": bool(item.get("deduped")),
        "occurrence_count": int(item.get("occurrence_count") or 1),
        "hypothesis": item.get("hypothesis"),
        "expected_outcome": item.get("expected_outcome"),
        "severity": severity,
        "priority_score": score,
        "domain": item.get("domain"),
        "module": item.get("module"),
        "module_id": item.get("module_id") or item.get("cartridge"),
        "cartridge": item.get("cartridge"),
        "connector_id": item.get("connector_id") or item.get("cartridge"),
        "source_dataset": item.get("source_dataset"),
        "title": item.get("title"),
        "message": alert_message_builder(item, alert_type),
        "status": alert_status,
        "owner": alert_state.get("owner"),
        "note": alert_state.get("note"),
        "reason": alert_state.get("reason"),
        "acknowledged_at": alert_state.get("acknowledged_at"),
        "assigned_at": alert_state.get("assigned_at"),
        "snoozed_until": alert_state.get("snoozed_until"),
        "threshold_state": str(item.get("threshold_state") or "default"),
        "lesson_count": int(item.get("lesson_count") or 0),
        "impact_estimate": item.get("impact_estimate"),
        "impact_currency": item.get("impact_currency") or "USD",
        "recommended_action": item.get("recommendation"),
        "drivers": priority.get("drivers") or [],
        "route_key": (
            f"{item.get('cartridge')}:{item.get('anomaly_type')}:"
            f"{item.get('module_id') or item.get('cartridge')}"
        ),
        "push_ready": push_ready,
        "delivery": {
            "status": delivery_status,
            "channels": ["email", "slack", "teams"],
            "enabled": delivery_enabled,
            "reason": (
                delivery_reason
                if alert_status == "open"
                else f"Alerta en estado {alert_status}; {delivery_reason}"
            ),
        },
        "created_at": item.get("first_seen_at")
        or item.get("detected_at")
        or datetime.now(UTC).isoformat(),
        "updated_at": alert_state.get("updated_at")
        or item.get("last_seen_at")
        or datetime.now(UTC).isoformat(),
    }


__all__ = ("build_business_alert",)
