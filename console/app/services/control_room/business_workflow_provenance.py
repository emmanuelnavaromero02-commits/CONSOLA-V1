from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
)


ELIGIBILITY_POLICY_VERSION = "control-room-business-v2"
DECISION_PROVENANCE_KEY = "decision_eligibility_provenance"
WORKFLOW_QUARANTINE_KEY = "workflow_quarantine"
CURRENT_ELIGIBILITY_FINGERPRINT_KEY = "business_eligibility_fingerprint"
ELIGIBILITY_POLICY_VERSION_KEY = "eligibility_policy_version"


class WorkflowStage(StrEnum):
    OPTION_SELECTED = "option_selected"
    DECISION_CREATED = "decision_created"
    APPROVED = "approved"
    EXECUTED = "executed"


_DECISION_STAGES = frozenset(
    {WorkflowStage.DECISION_CREATED, WorkflowStage.APPROVED, WorkflowStage.EXECUTED}
)
_STAGE_REASONS = {
    WorkflowStage.OPTION_SELECTED: "explicit_option_selection",
    WorkflowStage.DECISION_CREATED: "explicit_decision_creation",
    WorkflowStage.APPROVED: "explicit_approval",
    WorkflowStage.EXECUTED: "explicit_execution",
}


def _metadata_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _identity_value(item: Mapping[str, Any], *keys: str) -> str:
    metadata = item.get("metadata")
    for source in (item, metadata if isinstance(metadata, Mapping) else {}):
        for key in keys:
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def workflow_eligibility_provenance(
    item: Mapping[str, Any],
    *,
    stage: WorkflowStage | str,
    workspace_id: str,
    decision_id: int | None = None,
    option_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    parent_context = getattr(item, "_eligible_parent_ids", None)
    eligible_parent_ids = (
        {str(value) for value in parent_context} if parent_context is not None else None
    )
    eligibility = classify_business_item(
        item,
        eligible_parent_ids=eligible_parent_ids,
    )
    if not eligibility.eligible:
        raise ValueError("cannot link decision to non-business control room item")
    parsed_stage = WorkflowStage(str(stage))
    workspace = str(workspace_id or "").strip()
    if not workspace:
        raise ValueError("workflow provenance requires workspace")
    if parsed_stage in _DECISION_STAGES and decision_id is None:
        raise ValueError(f"{parsed_stage.value} requires decision_id")
    if parsed_stage is WorkflowStage.OPTION_SELECTED and decision_id is not None:
        raise ValueError("option_selected must not include decision_id")
    selected_option = str(
        option_id if option_id is not None else item.get("selected_option_id") or ""
    ).strip()
    if parsed_stage is WorkflowStage.OPTION_SELECTED and not selected_option:
        raise ValueError("option_selected requires option_id")
    payload = {
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "stage": parsed_stage.value,
        "workspace_id": workspace,
        "item_id": _identity_value(item, "id", "item_id"),
        "kind": _identity_value(item, "kind", "item_kind").lower(),
        "fingerprint": business_observation_fingerprint(item),
        "eligible_at_link": True,
        "linked_at": datetime.now(UTC).isoformat(),
        "reason": reason or _STAGE_REASONS[parsed_stage],
    }
    if decision_id is not None:
        payload["decision_id"] = int(decision_id)
    if selected_option:
        payload["option_id"] = selected_option
    return payload


def decision_eligibility_provenance(
    item: Mapping[str, Any],
    *,
    decision_id: int | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper; server writes always provide workspace_id."""
    workspace = str(workspace_id or item.get("workspace_id") or "").strip()
    return workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id=workspace or "legacy-unscoped",
        decision_id=decision_id,
    )


def workflow_has_eligible_provenance(
    metadata: Mapping[str, Any] | None,
    item: Mapping[str, Any],
    *,
    decision_id: Any = None,
    use_stored_fingerprint: bool = False,
    allowed_stages: Collection[WorkflowStage | str] | None = None,
) -> bool:
    current_metadata = _metadata_mapping(metadata)
    value = current_metadata.get(DECISION_PROVENANCE_KEY)
    if not isinstance(value, Mapping):
        return False
    try:
        stage = WorkflowStage(str(value.get("stage") or "").strip())
    except ValueError:
        return False
    if allowed_stages is not None and stage not in {
        WorkflowStage(str(allowed)) for allowed in allowed_stages
    }:
        return False
    current_fingerprint = (
        str(current_metadata.get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY) or "").strip()
        if use_stored_fingerprint
        else ""
    )
    if not current_fingerprint:
        current_fingerprint = business_observation_fingerprint(item)
    expected_decision_id = (
        decision_id if decision_id is not None else item.get("decision_id")
    )
    expected_workspace = _identity_value(item, "workspace_id")
    stored_workspace = str(value.get("workspace_id") or "").strip()
    option_value = (
        item.get("selected_option_id")
        if "selected_option_id" in item
        else current_metadata.get("selected_option_id")
    )
    expected_option = str(option_value or "").strip()
    common = (
        value.get("eligible_at_link") is True
        and value.get("policy_version") == ELIGIBILITY_POLICY_VERSION
        and bool(stored_workspace)
        and bool(expected_workspace)
        and stored_workspace == expected_workspace
        and str(value.get("item_id") or "").strip()
        == _identity_value(item, "id", "item_id")
        and str(value.get("kind") or "").strip().lower()
        == _identity_value(item, "kind", "item_kind").lower()
        and str(value.get("fingerprint") or "").strip() == current_fingerprint
    )
    if not common:
        return False
    stored_decision = str(value.get("decision_id") or "").strip()
    if expected_decision_id is not None:
        if stage not in _DECISION_STAGES:
            return False
        if stored_decision != str(expected_decision_id):
            return False
    elif stage in _DECISION_STAGES or stored_decision:
        return False
    stored_option = str(value.get("option_id") or "").strip()
    if stage is WorkflowStage.OPTION_SELECTED:
        return bool(expected_option) and stored_option == expected_option
    return stored_option == expected_option


def persistence_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    value = item.get("metadata")
    metadata = dict(value) if isinstance(value, Mapping) else {}
    for key in (
        DECISION_PROVENANCE_KEY,
        WORKFLOW_QUARANTINE_KEY,
        "decision_provenance",
    ):
        metadata.pop(key, None)
    metadata[CURRENT_ELIGIBILITY_FINGERPRINT_KEY] = business_observation_fingerprint(
        item
    )
    metadata[ELIGIBILITY_POLICY_VERSION_KEY] = ELIGIBILITY_POLICY_VERSION
    return metadata


def workflow_is_quarantined(item: Mapping[str, Any] | None) -> bool:
    from app.services.control_room.business_workflow_quarantine import (
        workflow_is_quarantined as evaluate,
    )

    return evaluate(item)


def quarantine_workflow_metadata(
    metadata: Mapping[str, Any] | None,
    **generation: Any,
) -> dict[str, Any]:
    from app.services.control_room.business_workflow_quarantine import (
        quarantine_workflow_metadata as build,
    )

    return build(metadata, **generation)


__all__ = (
    "DECISION_PROVENANCE_KEY",
    "CURRENT_ELIGIBILITY_FINGERPRINT_KEY",
    "ELIGIBILITY_POLICY_VERSION",
    "ELIGIBILITY_POLICY_VERSION_KEY",
    "WORKFLOW_QUARANTINE_KEY",
    "WorkflowStage",
    "business_observation_fingerprint",
    "decision_eligibility_provenance",
    "persistence_metadata",
    "quarantine_workflow_metadata",
    "workflow_has_eligible_provenance",
    "workflow_is_quarantined",
    "workflow_eligibility_provenance",
)
