from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_observation_codec import (
    ENVELOPE_KEY,
    POLICY_FIELDS,
    with_observation_envelope,
)


POLICY_METADATA_FIELDS = POLICY_FIELDS | frozenset({ENVELOPE_KEY})

REPLACED_POLICY_KEYS = tuple(sorted(POLICY_METADATA_FIELDS))


def business_policy_metadata(
    metadata: Mapping[str, Any] | None,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    clean = {
        str(key): value
        for key, value in dict(metadata or {}).items()
        if str(key) not in POLICY_METADATA_FIELDS
    }
    for key in POLICY_METADATA_FIELDS:
        if key not in item:
            continue
        value = item.get(key)
        if value is None or value == "":
            continue
        clean[key] = value
    details = item.get("details")
    if isinstance(details, Mapping):
        clean["details"] = {
            str(key): value
            for key, value in details.items()
            if str(key) not in POLICY_METADATA_FIELDS
        }
    return with_observation_envelope(clean, item)


__all__ = ("POLICY_METADATA_FIELDS", "REPLACED_POLICY_KEYS", "business_policy_metadata")
