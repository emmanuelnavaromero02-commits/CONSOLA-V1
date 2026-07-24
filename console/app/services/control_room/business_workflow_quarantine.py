from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    WORKFLOW_QUARANTINE_KEY,
    business_observation_fingerprint,
)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def quarantine_generations(value: Any) -> list[dict[str, Any]]:
    quarantine = _mapping(value)
    generations = quarantine.get("generations")
    if not isinstance(generations, list):
        return []
    return [dict(row) for row in generations if isinstance(row, Mapping)]


def workflow_is_quarantined(item: Mapping[str, Any] | None) -> bool:
    current = item if isinstance(item, Mapping) else {}
    metadata = _mapping(current.get("metadata"))
    quarantine = current.get(WORKFLOW_QUARANTINE_KEY) or metadata.get(
        WORKFLOW_QUARANTINE_KEY
    )
    if not quarantine:
        return False
    generations = quarantine_generations(quarantine)
    if not generations:
        return True
    fingerprint = business_observation_fingerprint(current)
    return any(
        str(generation.get("fingerprint") or "").strip() == fingerprint
        for generation in generations
    )


def quarantine_workflow_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    item: Mapping[str, Any],
    decision_id: Any,
    stage: str,
    reason: str,
) -> dict[str, Any]:
    clean = dict(metadata or {})
    existing = _mapping(clean.get(WORKFLOW_QUARANTINE_KEY))
    generations = quarantine_generations(existing)
    provenance = _mapping(clean.get(DECISION_PROVENANCE_KEY))
    fingerprint = str(provenance.get("fingerprint") or "").strip()
    if not fingerprint:
        fingerprint = business_observation_fingerprint(item)
    normalized_decision = decision_id
    if normalized_decision is None:
        normalized_decision = provenance.get("decision_id")
    normalized_stage = str(provenance.get("stage") or stage or "unknown").strip()
    identity = (fingerprint, str(normalized_decision or ""), normalized_stage)
    if not any(
        (
            str(row.get("fingerprint") or "").strip(),
            str(row.get("decision_id") or ""),
            str(row.get("stage") or "").strip(),
        )
        == identity
        for row in generations
    ):
        generations.append(
            {
                "fingerprint": fingerprint,
                "decision_id": normalized_decision,
                "stage": normalized_stage,
                "reason": str(reason or "workflow_invalidated"),
                "quarantined_at": datetime.now(UTC).isoformat(),
            }
        )
    clean[WORKFLOW_QUARANTINE_KEY] = {
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "generations": generations,
    }
    return clean


def workflow_columns_unlinked(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("decision_id") is None
        and not row.get("selected_option_id")
        and str(row.get("execution_status") or "not_started") == "not_started"
        and str(row.get("status") or "open")
        not in {"decision_created", "approved", "resolved"}
    )


def workflow_reopen_allowed(row: Mapping[str, Any]) -> bool:
    raw_metadata = row.get("metadata")
    if raw_metadata is None:
        metadata: Mapping[str, Any] = {}
    elif isinstance(raw_metadata, Mapping):
        metadata = raw_metadata
    elif isinstance(raw_metadata, str):
        try:
            parsed = json.loads(raw_metadata)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(parsed, Mapping):
            return False
        metadata = parsed
    else:
        return False
    return bool(
        str(row.get("status") or "").strip().lower() == "dismissed"
        and workflow_columns_unlinked(row)
        and DECISION_PROVENANCE_KEY not in metadata
    )


__all__ = (
    "quarantine_generations",
    "quarantine_workflow_metadata",
    "workflow_columns_unlinked",
    "workflow_is_quarantined",
    "workflow_reopen_allowed",
)
