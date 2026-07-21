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
)


def artifact_overlay_allowed(
    current_item: Mapping[str, Any],
    persisted_state: Mapping[str, Any],
    persisted_metadata: Mapping[str, Any],
) -> bool:
    fingerprint = str(
        persisted_metadata.get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY) or ""
    ).strip()
    if not fingerprint or fingerprint != business_observation_fingerprint(current_item):
        return False
    version = str(persisted_metadata.get(ELIGIBILITY_POLICY_VERSION_KEY) or "").strip()
    if version != ELIGIBILITY_POLICY_VERSION:
        return False
    provenance = persisted_metadata.get(DECISION_PROVENANCE_KEY)
    if isinstance(provenance, Mapping):
        provenance_version = str(provenance.get("policy_version") or "").strip()
        if provenance_version and provenance_version != ELIGIBILITY_POLICY_VERSION:
            return False
    persisted_item = dict(persisted_metadata)
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


__all__ = ("artifact_overlay_allowed",)
