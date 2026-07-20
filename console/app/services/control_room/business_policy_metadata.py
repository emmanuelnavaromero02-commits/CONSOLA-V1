from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_observation_codec import (
    ENVELOPE_KEY,
    POLICY_FIELDS,
    with_observation_envelope,
)
from app.services.control_room.business_eligibility import TECHNICAL_STATES


POLICY_METADATA_FIELDS = POLICY_FIELDS | frozenset({ENVELOPE_KEY})
REPLACED_POLICY_KEYS = tuple(sorted(POLICY_METADATA_FIELDS))
_DIAGNOSTIC_KIND_FIELDS = tuple(sorted(POLICY_FIELDS & {"item_kind", "kind"}))
_DIAGNOSTIC_STATE_FIELDS = tuple(
    sorted(
        POLICY_FIELDS
        & {
            "data_readiness",
            "data_status",
            "evaluation_status",
            "readiness_status",
            "source_status",
        }
    )
)


def _without_policy_fields(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in values.items()
        if str(key) not in POLICY_METADATA_FIELDS
    }


def diagnostic_policy_sql(column: str) -> str:
    surfaces = (column, f"{column}->'details'")
    states = ",".join(f"'{value}'" for value in sorted(TECHNICAL_STATES))
    checks = [
        f"lower(COALESCE({surface}->>'{field}', '')) = 'source_state'"
        for surface in surfaces
        for field in _DIAGNOSTIC_KIND_FIELDS
    ]
    checks.extend(
        f"lower(COALESCE({surface}->>'{field}', '')) IN ({states})"
        for surface in surfaces
        for field in _DIAGNOSTIC_STATE_FIELDS
    )
    return f"({' OR '.join(checks)})"


def policy_metadata_without_fields_sql(column: str, keys_parameter: str) -> str:
    top = f"({column} - {keys_parameter}::text[])"
    details = f"(({column}->'details') - {keys_parameter}::text[])"
    return (
        f"(CASE WHEN jsonb_typeof({column}->'details') = 'object' "
        f"THEN ({top} - 'details') || jsonb_build_object('details', {details}) "
        f"ELSE {top} END)"
    )


def business_policy_metadata(
    metadata: Mapping[str, Any] | None,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    original = dict(metadata or {})
    clean = _without_policy_fields(original)
    existing_details = original.get("details")
    if isinstance(existing_details, Mapping):
        clean["details"] = _without_policy_fields(existing_details)
    for key in POLICY_METADATA_FIELDS:
        if key not in item:
            continue
        value = item.get(key)
        if value is None or value == "":
            continue
        clean[key] = value
    details = item.get("details")
    if isinstance(details, Mapping):
        clean["details"] = _without_policy_fields(details)
    return with_observation_envelope(clean, item)


__all__ = (
    "POLICY_METADATA_FIELDS",
    "REPLACED_POLICY_KEYS",
    "business_policy_metadata",
    "diagnostic_policy_sql",
    "policy_metadata_without_fields_sql",
)
