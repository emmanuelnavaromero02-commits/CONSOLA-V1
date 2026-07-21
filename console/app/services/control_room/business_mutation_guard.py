from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import (
    actor_id,
    can_read_workspace_wide,
    workspace_scope,
)
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_projection import (
    normalize_persisted_business_item,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    ELIGIBILITY_POLICY_VERSION_KEY,
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    business_observation_fingerprint,
    workflow_has_eligible_provenance,
)
from app.services.control_room.business_workflow_quarantine import (
    workflow_columns_unlinked,
)


_LOCK_ITEM_SQL = """
SELECT tenant_id::text AS tenant_id,
       workspace_id::text AS workspace_id,
       owner_user_id, item_id, cartridge_id, domain, source_dataset,
       item_kind, title, severity, status, decision_id, entity_kind,
       entity_id, entity_label, anomaly_type, metadata,
       impact_estimate, impact_currency, confidence, priority_score,
       selected_option_id, execution_status, first_seen_at, last_seen_at,
       resolved_at, dismissed_at
  FROM control_room_items
 WHERE workspace_id = $1::uuid
   AND item_id = $2
 FOR UPDATE
"""


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _changed() -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "item_business_state_changed",
            "message": "control room item changed; reload before mutating",
        },
    )


def _complete_item_scope(
    item: Mapping[str, Any], *, tenant_id: str, workspace_id: str
) -> None:
    item_tenant = str(item.get("tenant_id") or "").strip()
    item_workspace = str(item.get("workspace_id") or "").strip()
    if not item_tenant or not item_workspace:
        raise _changed()
    if item_tenant != tenant_id or item_workspace != workspace_id:
        raise HTTPException(404, "control room item not found")


def _current_generation_quarantined(
    metadata: Mapping[str, Any], fingerprint: str
) -> bool:
    quarantine = metadata.get(WORKFLOW_QUARANTINE_KEY)
    if not isinstance(quarantine, Mapping):
        return bool(quarantine)
    generations = quarantine.get("generations")
    if isinstance(generations, list):
        return any(
            isinstance(value, Mapping)
            and str(value.get("fingerprint") or "").strip() == fingerprint
            for value in generations
        )
    quarantined_fingerprint = str(
        quarantine.get("fingerprint") or quarantine.get("previous_fingerprint") or ""
    ).strip()
    return not quarantined_fingerprint or quarantined_fingerprint == fingerprint


def _validate_owner(
    row: Mapping[str, Any], item: Mapping[str, Any], user: Mapping[str, Any]
) -> None:
    persisted_owner = actor_id(row.get("owner_user_id"))
    resolved_owner = actor_id(item.get("owner_user_id"))
    current_actor = actor_id(user.get("id"))
    if resolved_owner is not None and persisted_owner != resolved_owner:
        raise HTTPException(404, "control room item not found")
    if not can_read_workspace_wide(user) and persisted_owner != current_actor:
        raise HTTPException(404, "control room item not found")


def _validate_mutation_state(row: Mapping[str, Any], item: Mapping[str, Any]) -> None:
    defaults = {"status": "open", "execution_status": "not_started"}
    for field in ("decision_id", "selected_option_id", "status", "execution_status"):
        if field not in item:
            continue
        persisted = row.get(field)
        resolved = item.get(field)
        if field in defaults:
            persisted = persisted or defaults[field]
            resolved = resolved or defaults[field]
        if str(persisted or "") != str(resolved or ""):
            raise _changed()


def _validate_workflow(
    row: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    decision_id: int | None,
    allowed_stages: Collection[WorkflowStage | str] | None,
) -> None:
    persisted_decision = row.get("decision_id")
    resolved_decision = item.get("decision_id")
    expected_decision = decision_id if decision_id is not None else resolved_decision
    if expected_decision is None:
        if persisted_decision is not None:
            raise _changed()
        return
    if str(persisted_decision or "") != str(expected_decision):
        raise _changed()
    normalized = normalize_persisted_business_item(row)
    if not workflow_has_eligible_provenance(
        _metadata(row.get("metadata")),
        normalized,
        decision_id=expected_decision,
        allowed_stages=allowed_stages,
    ):
        raise _changed()


async def lock_authoritative_business_item(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    allow_missing: bool = False,
    allow_diagnostic_transition: bool = False,
    decision_id: int | None = None,
    allowed_stages: Collection[WorkflowStage | str] | None = None,
) -> dict[str, Any] | None:
    tenant_id, workspace_id = workspace_scope(user)
    if not tenant_id:
        raise _changed()
    _complete_item_scope(item, tenant_id=tenant_id, workspace_id=workspace_id)
    row = await conn.fetchrow(_LOCK_ITEM_SQL, workspace_id, str(item.get("id") or ""))
    if not row:
        if allow_missing:
            return None
        raise HTTPException(404, "control room item not found")
    locked = dict(row)
    if (
        str(locked.get("tenant_id") or "") != tenant_id
        or str(locked.get("workspace_id") or "") != workspace_id
    ):
        raise HTTPException(404, "control room item not found")
    _validate_owner(locked, item, user)
    _validate_mutation_state(locked, item)
    if not classify_business_item(item).eligible:
        raise _changed()
    normalized = normalize_persisted_business_item(locked)
    if not classify_business_item(normalized).eligible:
        if not allow_diagnostic_transition or not workflow_columns_unlinked(locked):
            raise _changed()
        return locked
    metadata = _metadata(locked.get("metadata"))
    current_fingerprint = business_observation_fingerprint(item)
    persisted_fingerprint = str(
        metadata.get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY) or ""
    ).strip()
    if (
        metadata.get(ELIGIBILITY_POLICY_VERSION_KEY) != ELIGIBILITY_POLICY_VERSION
        or persisted_fingerprint != current_fingerprint
        or business_observation_fingerprint(normalized) != current_fingerprint
    ):
        raise _changed()
    if _current_generation_quarantined(metadata, current_fingerprint):
        raise _changed()
    if metadata.get(DECISION_PROVENANCE_KEY) or decision_id is not None:
        _validate_workflow(
            locked,
            item,
            decision_id=decision_id,
            allowed_stages=allowed_stages,
        )
    return locked


__all__ = ("lock_authoritative_business_item",)
