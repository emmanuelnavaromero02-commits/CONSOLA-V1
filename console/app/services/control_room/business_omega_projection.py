from __future__ import annotations

from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass
from typing import Any

from app.services.control_room.business_builder_policy import (
    business_builder_allowed,
    diagnostic_projection,
    projection_context,
)
from app.services.control_room.business_omega_sections import (
    omega_execution,
    omega_options,
    omega_priority,
)
from app.services.control_room.business_projection import project_business_item


@dataclass(frozen=True)
class OmegaProjectionRuntime:
    impact_builder: Callable[..., dict[str, Any]]
    action_templates_builder: Callable[..., list[dict[str, Any]]]
    decision_intelligence_builder: Callable[[dict[str, Any]], dict[str, Any]]
    lessons_builder: Callable[[dict[str, Any]], list[dict[str, Any]]]
    priority_builder: Callable[..., dict[str, Any]]
    alert_state_builder: Callable[[dict[str, Any]], dict[str, Any]]
    control_state_builder: Callable[[dict[str, Any]], dict[str, Any]]
    control_items_builder: Callable[..., list[dict[str, Any]]]
    external_writeback_enabled: Callable[[], bool]
    execution_statuses: Set[str]
    terminal_statuses: Set[str]
    supported_writeback_templates: Set[str]


def build_omega_projection(
    item: dict[str, Any],
    *,
    eligible_parent_ids: Set[str] | None,
    runtime: OmegaProjectionRuntime,
) -> dict[str, Any]:
    context = projection_context(item, eligible_parent_ids)
    if not business_builder_allowed(item, eligible_parent_ids=context):
        return diagnostic_projection(item)
    decision_id = item.get("decision_id")
    status = str(item.get("status") or "open")
    approved = status == "approved"
    impact = runtime.impact_builder(item, eligible_parent_ids=context)
    templates = runtime.action_templates_builder(item, eligible_parent_ids=context)
    execution_status = str(item.get("execution_status") or "not_started")
    if execution_status not in runtime.execution_statuses:
        execution_status = "not_started"
    decision_intelligence = runtime.decision_intelligence_builder(item)
    options, selected_option_id, intelligence = omega_options(
        item,
        impact,
        decision_intelligence=decision_intelligence,
    )
    lessons = runtime.lessons_builder(item)
    lesson_count = int(item.get("lesson_count") or 0)
    priority = omega_priority(
        item,
        impact,
        lesson_count=lesson_count,
        eligible_parent_ids=context,
        priority_builder=runtime.priority_builder,
    )
    alert_state = runtime.alert_state_builder(item)
    controls = runtime.control_items_builder(
        item,
        status=status,
        decision_id=decision_id,
        approved=approved,
    )
    control_closed = all(str(control.get("status")) == "closed" for control in controls)
    execution = omega_execution(
        execution_status=execution_status,
        primary_system=str(item.get("cartridge") or "platform"),
        approved=approved,
        decision_id=decision_id,
        action_templates=templates,
        external_writeback_enabled=runtime.external_writeback_enabled(),
        supported_templates=runtime.supported_writeback_templates,
    )
    lesson_applications = (
        item.get("lesson_applications")
        if isinstance(item.get("lesson_applications"), list)
        else []
    )
    thresholds = item.get("thresholds_applied") or []
    threshold_state = item.get("threshold_state") or "default"
    return project_business_item(
        {
            **item,
            "alert_state": alert_state,
            "control_state": runtime.control_state_builder(item),
            "impact_estimate": impact.get("estimate"),
            "impact_currency": impact.get("currency"),
            "impact_status": impact.get("status"),
            "confidence": impact.get("confidence"),
            "priority_score": priority["score"],
            "priority": priority,
            "impact_drivers": impact.get("drivers"),
            "impact_formula": impact.get("formula"),
            "impact_explanation": impact.get("explanation"),
            "thresholds_applied": thresholds,
            "threshold_state": threshold_state,
            "selected_option_id": selected_option_id,
            "execution_status": execution_status,
            "action_templates": templates,
            "related_lessons": item.get("related_lessons") or [],
            "lesson_count": lesson_count,
            "lesson_applications": lesson_applications,
            "decision_intelligence": decision_intelligence,
            "intelligence": intelligence,
            "omega": {
                "signals": {
                    "source": item.get("source_dataset"),
                    "severity": item.get("severity"),
                    "detected_at": item.get("detected_at"),
                    "status": status,
                    "priority_score": priority["score"],
                    "priority_band": priority["band"],
                    "threshold_state": threshold_state,
                },
                "investigation": {
                    "root_cause": item.get("root_cause"),
                    "impact": item.get("impact"),
                    "evidence": item.get("details") or {},
                    "money": impact,
                    "thresholds": thresholds,
                },
                "options": options,
                "decision": {
                    "decision_id": decision_id,
                    "status": status,
                    "label": (
                        f"Decision #{decision_id}" if decision_id else "Pendiente"
                    ),
                },
                "execution": execution,
                "control": {
                    "owner": item.get("module") or item.get("cartridge"),
                    "cadence": "Proximo refresh operativo",
                    "status": "cerrado" if control_closed else "abierto",
                    "items": controls,
                },
                "lessons": {
                    "rules": lessons,
                    "applied": lesson_applications,
                    "suggested_actions": (
                        item.get("suggested_actions")
                        if isinstance(item.get("suggested_actions"), list)
                        else []
                    ),
                },
                "decision_intelligence": decision_intelligence,
                "intelligence": intelligence,
            },
        },
        eligible_parent_ids=context,
    )


__all__ = ("OmegaProjectionRuntime", "build_omega_projection")
