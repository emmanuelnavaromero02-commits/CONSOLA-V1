from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_eligibility import (
    TECHNICAL_STATES,
    classify_business_item,
)
from app.services.control_room.business_lineage import parent_references
from app.services.control_room.business_lineage import is_source_state
from app.services.control_room.business_observation import semantic_states
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    ELIGIBILITY_POLICY_VERSION_KEY,
    business_observation_fingerprint,
    workflow_has_eligible_provenance,
    workflow_is_quarantined,
)


_SCOPE_FIELDS = ("tenant_id", "workspace_id", "owner_user_id")
_PERSISTED_POLICY_FIELDS = (
    "data_status",
    "item_kind",
    "kind",
    "readiness_status",
    "source_dataset",
    "source_status",
    "tenant_id",
    "workspace_id",
)


def scoped_overlay_item(
    current_item: Mapping[str, Any],
    persisted_state: Mapping[str, Any],
) -> dict[str, Any] | None:
    scoped = dict(current_item)
    for field in _SCOPE_FIELDS:
        current = scoped.get(field)
        persisted = persisted_state.get(field)
        if (
            current is not None
            and persisted is not None
            and str(current) != str(persisted)
        ):
            return None
        if persisted is not None:
            scoped[field] = persisted
    return scoped


def _persisted_policy_item(
    persisted_state: Mapping[str, Any],
    persisted_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    item = dict(persisted_metadata)
    item_id = persisted_state.get("item_id")
    if item_id is not None:
        item["id"] = item_id
        item["item_id"] = item_id
    if persisted_state.get("entity_id") is not None:
        item["entity_id"] = persisted_state["entity_id"]
    if persisted_state.get("cartridge_id") is not None:
        item["cartridge"] = persisted_state["cartridge_id"]
    for key in _PERSISTED_POLICY_FIELDS:
        if key in persisted_state:
            item[key] = persisted_state[key]
    return item


def persisted_state_is_diagnostic(
    persisted_state: Mapping[str, Any],
    persisted_metadata: Mapping[str, Any],
) -> bool:
    item = _persisted_policy_item(persisted_state, persisted_metadata)
    return is_source_state(item) or bool(semantic_states(item) & TECHNICAL_STATES)


def artifact_overlay_allowed(
    current_item: Mapping[str, Any],
    persisted_state: Mapping[str, Any],
    persisted_metadata: Mapping[str, Any],
) -> bool:
    scoped_item = scoped_overlay_item(current_item, persisted_state)
    if scoped_item is None or workflow_is_quarantined(persisted_state):
        return False
    fingerprint = str(
        persisted_metadata.get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY) or ""
    ).strip()
    if not fingerprint or fingerprint != business_observation_fingerprint(scoped_item):
        return False
    version = str(persisted_metadata.get(ELIGIBILITY_POLICY_VERSION_KEY) or "").strip()
    if version != ELIGIBILITY_POLICY_VERSION:
        return False
    provenance = persisted_metadata.get(DECISION_PROVENANCE_KEY)
    if isinstance(provenance, Mapping):
        provenance_item = {
            **scoped_item,
            "selected_option_id": persisted_state.get("selected_option_id"),
        }
        if not workflow_has_eligible_provenance(
            persisted_metadata,
            provenance_item,
            decision_id=persisted_state.get("decision_id"),
        ):
            return False
    persisted_item = _persisted_policy_item(persisted_state, persisted_metadata)
    refs = parent_references(persisted_item)
    parent_context = set(refs.ids) if refs.ids and not refs.malformed else None
    return classify_business_item(
        persisted_item,
        eligible_parent_ids=parent_context,
    ).eligible


__all__ = (
    "artifact_overlay_allowed",
    "persisted_state_is_diagnostic",
    "scoped_overlay_item",
)
