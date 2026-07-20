from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_lineage import parent_references
from app.services.control_room.business_observation_codec import (
    POLICY_FIELDS,
    semantic_surfaces,
)


ELIGIBILITY_POLICY_VERSION = "control-room-business-v1"
DECISION_PROVENANCE_KEY = "decision_eligibility_provenance"
WORKFLOW_QUARANTINE_KEY = "workflow_quarantine"
CURRENT_ELIGIBILITY_FINGERPRINT_KEY = "business_eligibility_fingerprint"

_IDENTITY_FIELDS = frozenset({"kind", "item_kind"})
_ROOT_FIELDS = frozenset(
    {"dataset", "gold_table", "root_source", "source_dataset", "source_system"}
)
_STATUS_FIELDS = frozenset(
    {
        "data_readiness",
        "data_status",
        "evaluation_status",
        "readiness_status",
        "source_status",
    }
)


def _canonical(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)


def _identity_value(item: Mapping[str, Any], *keys: str) -> str:
    metadata = item.get("metadata")
    for source in (item, metadata if isinstance(metadata, Mapping) else {}):
        for key in keys:
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _semantic_fingerprint_values(item: Mapping[str, Any]) -> dict[str, list[str]]:
    values: dict[str, set[str]] = {}
    excluded = _IDENTITY_FIELDS | _ROOT_FIELDS | _STATUS_FIELDS
    for surface in semantic_surfaces(item):
        for key in POLICY_FIELDS - excluded:
            if key not in surface or surface[key] in (None, ""):
                continue
            values.setdefault(key, set()).add(_canonical(surface[key]))
    return {key: sorted(entries) for key, entries in sorted(values.items())}


def _root_values(item: Mapping[str, Any]) -> list[str]:
    values = {
        str(surface[key]).strip()
        for surface in semantic_surfaces(item)
        for key in _ROOT_FIELDS
        if key in surface and str(surface[key] or "").strip()
    }
    return sorted(values)


def business_observation_fingerprint(item: Mapping[str, Any]) -> str:
    refs = parent_references(item)
    payload = {
        "item_id": _identity_value(item, "id", "item_id"),
        "kind": _identity_value(item, "kind", "item_kind").lower(),
        "observation": _semantic_fingerprint_values(item),
        "lineage": sorted(refs.ids),
        "root": _root_values(item),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def decision_eligibility_provenance(
    item: Mapping[str, Any],
    *,
    decision_id: int | None = None,
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
    payload = {
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "item_id": _identity_value(item, "id", "item_id"),
        "kind": _identity_value(item, "kind", "item_kind").lower(),
        "fingerprint": business_observation_fingerprint(item),
        "eligible_at_link": True,
        "linked_at": datetime.now(UTC).isoformat(),
    }
    if decision_id is not None:
        payload["decision_id"] = int(decision_id)
    return payload


def workflow_has_eligible_provenance(
    metadata: Mapping[str, Any] | None,
    item: Mapping[str, Any],
) -> bool:
    current_metadata = dict(metadata or {})
    value = current_metadata.get(DECISION_PROVENANCE_KEY)
    if not isinstance(value, Mapping):
        return False
    current_fingerprint = str(
        current_metadata.get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY) or ""
    ).strip() or business_observation_fingerprint(item)
    return (
        value.get("eligible_at_link") is True
        and value.get("policy_version") == ELIGIBILITY_POLICY_VERSION
        and str(value.get("item_id") or "").strip()
        == _identity_value(item, "id", "item_id")
        and str(value.get("kind") or "").strip().lower()
        == _identity_value(item, "kind", "item_kind").lower()
        and str(value.get("fingerprint") or "").strip() == current_fingerprint
    )


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
    return metadata


def quarantine_workflow_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    clean = dict(metadata or {})
    clean[WORKFLOW_QUARANTINE_KEY] = {
        "reason": "legacy_or_diagnostic_workflow",
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "quarantined_at": datetime.now(UTC).isoformat(),
    }
    return clean


__all__ = (
    "DECISION_PROVENANCE_KEY",
    "CURRENT_ELIGIBILITY_FINGERPRINT_KEY",
    "ELIGIBILITY_POLICY_VERSION",
    "WORKFLOW_QUARANTINE_KEY",
    "business_observation_fingerprint",
    "decision_eligibility_provenance",
    "persistence_metadata",
    "quarantine_workflow_metadata",
    "workflow_has_eligible_provenance",
)
