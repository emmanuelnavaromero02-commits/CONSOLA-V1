from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.control_room.business_access import (
    actor_id as optional_actor_id,
    can_read_workspace_wide,
)
from app.services.control_room.business_action_authority_policy import actor_id
from app.services.control_room.business_action_digest import action_contract_digest
from app.services.control_room.business_persisted_row import persisted_business_item
from app.services.control_room.business_runtime_evidence import (
    canonical_runtime_row_reference,
)


ITEM_STATUSES = {
    "open",
    "in_review",
    "decision_created",
    "approved",
    "dismissed",
    "resolved",
}
SEVERITY_WEIGHTS = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def metadata(value: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = value.get("metadata")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return raw if isinstance(raw, Mapping) else {}


def evidence_digest(item: Mapping[str, Any]) -> str | None:
    references: list[dict[str, Any]] = []
    for source in (item, metadata(item)):
        raw = source.get("evidence_refs")
        values = (
            raw
            if isinstance(raw, Sequence)
            and not isinstance(raw, (str, bytes, bytearray))
            else ()
        )
        for value in values:
            if isinstance(value, Mapping):
                canonical = canonical_runtime_row_reference(value)
                if canonical is not None:
                    references.append(canonical)
    if not references:
        return None
    return action_contract_digest(
        {"runtime_references": sorted(references, key=lambda value: str(value))}
    )


def persisted_item(row: Mapping[str, Any], item_id: str) -> Mapping[str, Any] | None:
    return persisted_business_item(
        row,
        expected_item_id=item_id,
        item_statuses=ITEM_STATUSES,
        severity_weights=SEVERITY_WEIGHTS,
    )


def owner_allowed(
    row: Mapping[str, Any], live: Mapping[str, Any], user: Mapping[str, Any]
) -> bool:
    persisted_owner = optional_actor_id(row.get("owner_user_id"))
    live_owner = optional_actor_id(live.get("owner_user_id"))
    if persisted_owner != live_owner and live_owner is not None:
        return False
    return can_read_workspace_wide(user) or persisted_owner == actor_id(user)


__all__ = (
    "evidence_digest",
    "metadata",
    "owner_allowed",
    "persisted_item",
)
