from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence, Set
from typing import Any

from app.services.control_room.business_access import owner_projection
from app.services.control_room.business_artifact_overlay import (
    artifact_overlay_allowed,
    persisted_state_is_diagnostic,
    scoped_overlay_item,
)
from app.services.control_room.business_projection import eligible_item_ids
from app.services.control_room.business_surface_provenance import (
    set_surface_workflow_provenance,
)
from app.services.control_room.business_workflow_provenance import (
    workflow_has_eligible_provenance,
    workflow_is_quarantined,
)
from app.services.control_room.business_workflow_state import (
    expected_workflow_stage,
)


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    for key, value in tuple(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


async def load_overlay_state(
    conn: Any,
    *,
    workspace_id: str,
    item_ids: Sequence[str],
    tenant_id: str | None,
    owner_id: int | None,
) -> dict[str, dict[str, Any]]:
    params: list[Any] = [workspace_id, list(item_ids)]
    clauses = ["workspace_id = $1", "item_id = ANY($2::text[])"]
    if tenant_id:
        params.append(tenant_id)
        clauses.append(f"tenant_id::text = ${len(params)}")
    if owner_id is not None:
        params.append(owner_id)
        clauses.append(f"owner_user_id = ${len(params)}")
    rows = await conn.fetch(
        f"""
        SELECT item_id, tenant_id, workspace_id, owner_user_id, cartridge_id,
               status, decision_id, metadata,
               source_dataset, item_kind,
               first_seen_at, last_seen_at, resolved_at, dismissed_at,
               impact_estimate, impact_currency, confidence, priority_score,
               selected_option_id, execution_status
          FROM control_room_items
         WHERE {' AND '.join(clauses)}
        """,
        *params,
    )
    return {str(row["item_id"]): _public(row) for row in rows}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _has_linked_workflow(state: Mapping[str, Any]) -> bool:
    execution = str(state.get("execution_status") or "not_started")
    return (
        state.get("decision_id") is not None
        or state.get("selected_option_id") is not None
        or execution != "not_started"
        or str(state.get("status") or "") in {"decision_created", "approved"}
    )


def _workflow_state(
    item: Mapping[str, Any],
    state: Mapping[str, Any],
    metadata: Mapping[str, Any],
    scoped_item: Mapping[str, Any] | None,
    *,
    overlay_artifacts: bool,
    persisted_diagnostic: bool,
) -> tuple[dict[str, Any], bool]:
    linked_workflow = _has_linked_workflow(state)
    provenance_item = (
        {
            **scoped_item,
            "selected_option_id": state.get("selected_option_id"),
        }
        if scoped_item is not None
        else None
    )
    eligible_provenance = (
        provenance_item is not None
        and not persisted_diagnostic
        and not workflow_is_quarantined(state)
        and workflow_has_eligible_provenance(
            metadata,
            provenance_item,
            decision_id=state.get("decision_id"),
        )
    )
    expected_stage = expected_workflow_stage(state)
    stage_provenance_verified = (
        eligible_provenance
        and expected_stage is not None
        and workflow_has_eligible_provenance(
            metadata,
            provenance_item,
            decision_id=state.get("decision_id"),
            allowed_stages={expected_stage},
        )
    )
    decision_provenance_verified = (
        state.get("decision_id") is not None and stage_provenance_verified
    )
    trusted = (
        scoped_item is not None
        and not persisted_diagnostic
        and not workflow_is_quarantined(state)
        and (overlay_artifacts if not linked_workflow else stage_provenance_verified)
    )
    if trusted:
        workflow = {
            "status": state.get("status") or item.get("status") or "open",
            "decision_id": state.get("decision_id") or item.get("decision_id"),
            "selected_option_id": state.get("selected_option_id")
            or metadata.get("selected_option_id")
            or item.get("selected_option_id"),
            "execution_status": state.get("execution_status")
            or metadata.get("execution_status")
            or item.get("execution_status"),
            "lessons": metadata.get("lessons"),
            "learned_rules": metadata.get("learned_rules"),
            "lesson_applications": metadata.get("lesson_applications")
            if isinstance(metadata.get("lesson_applications"), list)
            else [],
        }
    else:
        workflow = {
            "status": item.get("status") or "open",
            "decision_id": item.get("decision_id"),
            "selected_option_id": item.get("selected_option_id"),
            "execution_status": item.get("execution_status") or "not_started",
            "lessons": item.get("lessons"),
            "learned_rules": item.get("learned_rules"),
            "lesson_applications": item.get("lesson_applications")
            if isinstance(item.get("lesson_applications"), list)
            else [],
        }
    return workflow, decision_provenance_verified


def overlay_business_state(
    items: Sequence[Mapping[str, Any]],
    state_by_id: Mapping[str, Mapping[str, Any]],
    *,
    item_statuses: Set[str],
    projector: Callable[..., dict[str, Any]],
    sort_key: Callable[[Mapping[str, Any]], Any],
) -> list[dict[str, Any]]:
    business_ids = eligible_item_ids(items)
    merged: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        state = dict(state_by_id.get(str(item.get("id") or ""), {}))
        metadata = _mapping(state.get("metadata"))
        scoped_item = scoped_overlay_item(item, state)
        overlay_artifacts = artifact_overlay_allowed(item, state, metadata)
        persisted_diagnostic = persisted_state_is_diagnostic(state, metadata)
        workflow, workflow_provenance_verified = _workflow_state(
            item,
            state,
            metadata,
            scoped_item,
            overlay_artifacts=overlay_artifacts,
            persisted_diagnostic=persisted_diagnostic,
        )
        if str(workflow["status"]) not in item_statuses:
            workflow["status"] = "open"
        intelligence = _mapping(
            (metadata.get("intelligence") if overlay_artifacts else None)
            or item.get("intelligence")
        )
        decision_intelligence = _mapping(
            (metadata.get("decision_intelligence") if overlay_artifacts else None)
            or intelligence.get("decision_intelligence")
            or item.get("decision_intelligence")
        )
        if decision_intelligence:
            intelligence = {
                **intelligence,
                "decision_intelligence": decision_intelligence,
            }
        projected = projector(
            {
                **item,
                **owner_projection(item, state),
                **workflow,
                "impact_estimate": (
                    state.get("impact_estimate")
                    if overlay_artifacts
                    else item.get("impact_estimate")
                ),
                "impact_currency": (
                    state.get("impact_currency")
                    if overlay_artifacts
                    else item.get("impact_currency")
                ),
                "confidence": (
                    state.get("confidence")
                    if overlay_artifacts
                    else item.get("confidence")
                ),
                "priority_score": (
                    state.get("priority_score")
                    if overlay_artifacts
                    else item.get("priority_score")
                ),
                "thresholds_applied": (
                    metadata.get("thresholds_applied")
                    or item.get("thresholds_applied")
                    or []
                )
                if overlay_artifacts
                else (item.get("thresholds_applied") or []),
                "threshold_state": (
                    metadata.get("threshold_state")
                    or item.get("threshold_state")
                    or "default"
                )
                if overlay_artifacts
                else (item.get("threshold_state") or "default"),
                "alert_state": (
                    metadata.get("alert_state")
                    if isinstance(metadata.get("alert_state"), Mapping)
                    else item.get("alert_state")
                )
                if overlay_artifacts
                else item.get("alert_state"),
                "control_state": (
                    metadata.get("control_state")
                    if isinstance(metadata.get("control_state"), Mapping)
                    else item.get("control_state")
                )
                if overlay_artifacts
                else item.get("control_state"),
                "decision_intelligence": decision_intelligence,
                "intelligence": intelligence,
                "first_seen_at": state.get("first_seen_at"),
                "last_seen_at": state.get("last_seen_at"),
                "resolved_at": state.get("resolved_at"),
                "dismissed_at": state.get("dismissed_at"),
            },
            eligible_parent_ids=business_ids,
        )
        set_surface_workflow_provenance(
            projected,
            verified=workflow_provenance_verified,
        )
        merged.append(projected)
    merged.sort(key=sort_key)
    return merged


__all__ = ("load_overlay_state", "overlay_business_state")
