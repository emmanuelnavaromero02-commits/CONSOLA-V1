from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_observation import (
    EVIDENCE_FIELDS,
    OBSERVATION_DATE_FIELDS,
)
from app.services.control_room.business_observation_codec import (
    INVALID_ENVELOPE_FIELD,
    with_observation_envelope,
)
from app.services.control_room.business_semantic_slots import (
    METRIC_KIND_FIELDS,
    OBSERVATION_FLAG_FIELDS,
    SLOT_ALIASES,
)


NUMERIC_FIELDS = frozenset(
    alias for aliases in SLOT_ALIASES.values() for alias in aliases
)

POLICY_METADATA_FIELDS = frozenset(
    {
        *METRIC_KIND_FIELDS,
        *NUMERIC_FIELDS,
        *OBSERVATION_FLAG_FIELDS,
        *EVIDENCE_FIELDS,
        *OBSERVATION_DATE_FIELDS,
        INVALID_ENVELOPE_FIELD,
        "business_observation",
        "data_readiness",
        "data_status",
        "dataset",
        "details",
        "domain",
        "evidence_id",
        "evidence_pack_id",
        "gold_table",
        "intelligence",
        "item_kind",
        "kind",
        "lineage",
        "module",
        "root_source",
        "source_dataset",
        "source_status",
        "source_system",
    }
)

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
