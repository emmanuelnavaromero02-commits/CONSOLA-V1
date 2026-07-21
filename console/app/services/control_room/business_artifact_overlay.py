from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_lineage import parent_references
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
        if not workflow_has_eligible_provenance(
            persisted_metadata,
            scoped_item,
            decision_id=persisted_state.get("decision_id"),
        ):
            return False
    persisted_item = dict(persisted_metadata)
    if persisted_state.get("cartridge_id") is not None:
        persisted_item["cartridge"] = persisted_state["cartridge_id"]
    for key in (
        "data_status",
        "item_kind",
        "kind",
        "readiness_status",
        "source_dataset",
        "source_status",
    ):
        if key in persisted_state:
            persisted_item[key] = persisted_state[key]
    refs = parent_references(persisted_item)
    parent_context = set(refs.ids) if refs.ids and not refs.malformed else None
    return classify_business_item(
        persisted_item,
        eligible_parent_ids=parent_context,
    ).eligible


__all__ = ("artifact_overlay_allowed", "scoped_overlay_item")
