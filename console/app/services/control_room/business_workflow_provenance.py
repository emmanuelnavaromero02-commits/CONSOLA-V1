from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_lineage import parent_references


ELIGIBILITY_POLICY_VERSION = "control-room-business-v1"
DECISION_PROVENANCE_KEY = "decision_eligibility_provenance"
WORKFLOW_QUARANTINE_KEY = "workflow_quarantine"


def _canonical(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)


def business_observation_fingerprint(item: Mapping[str, Any]) -> str:
    refs = parent_references(item)
    payload = {
        "item_id": item.get("id") or item.get("item_id"),
        "kind": item.get("kind") or item.get("item_kind"),
        "observation": item.get("business_observation")
        or item.get("observation")
        or item.get("details"),
        "lineage": sorted(refs.ids),
        "root": item.get("source_dataset")
        or item.get("dataset")
        or item.get("gold_table")
        or item.get("source_system"),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def decision_eligibility_provenance(
    item: Mapping[str, Any],
    *,
    decision_id: int | None = None,
) -> dict[str, Any]:
    eligibility = classify_business_item(item)
    if not eligibility.eligible:
        raise ValueError("cannot link decision to non-business control room item")
    payload = {
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "item_id": str(item.get("id") or item.get("item_id") or ""),
        "kind": str(item.get("kind") or item.get("item_kind") or ""),
        "fingerprint": business_observation_fingerprint(item),
        "eligible_at_link": True,
        "linked_at": datetime.now(UTC).isoformat(),
    }
    if decision_id is not None:
        payload["decision_id"] = int(decision_id)
    return payload


def workflow_has_eligible_provenance(metadata: Mapping[str, Any] | None) -> bool:
    value = dict(metadata or {}).get(DECISION_PROVENANCE_KEY)
    if not isinstance(value, Mapping):
        return False
    return (
        value.get("eligible_at_link") is True
        and value.get("policy_version") == ELIGIBILITY_POLICY_VERSION
        and bool(str(value.get("item_id") or "").strip())
        and bool(str(value.get("fingerprint") or "").strip())
    )


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
    "ELIGIBILITY_POLICY_VERSION",
    "WORKFLOW_QUARANTINE_KEY",
    "business_observation_fingerprint",
    "decision_eligibility_provenance",
    "quarantine_workflow_metadata",
    "workflow_has_eligible_provenance",
)
