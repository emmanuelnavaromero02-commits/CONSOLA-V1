from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_observation_codec import (
    ENVELOPE_KEY,
    METADATA_SEMANTIC_SURFACE_PATHS,
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


def _clean_surface_path(
    values: Mapping[str, Any], path: tuple[str, ...]
) -> dict[str, Any]:
    if not path:
        return _without_policy_fields(values)
    clean = dict(values)
    child = values.get(path[0])
    if isinstance(child, Mapping):
        clean[path[0]] = _clean_surface_path(child, path[1:])
    return clean


def _clean_metadata_surfaces(values: Mapping[str, Any]) -> dict[str, Any]:
    clean = dict(values)
    for path in METADATA_SEMANTIC_SURFACE_PATHS:
        clean = _clean_surface_path(clean, path)
    return clean


def _jsonb_path(path: tuple[str, ...]) -> str:
    return "'{" + ",".join(path) + "}'"


def _clean_jsonb_surface(
    column: str, path: tuple[str, ...], keys_parameter: str
) -> str:
    source = column if not path else f"({column} #> {_jsonb_path(path)})"
    clean = f"({source} - {keys_parameter}::text[])"
    children = sorted(
        {
            candidate[len(path)]
            for candidate in METADATA_SEMANTIC_SURFACE_PATHS
            if len(candidate) > len(path) and candidate[: len(path)] == path
        }
    )
    for child in children:
        child_path = (*path, child)
        original_child = f"({column} #> {_jsonb_path(child_path)})"
        cleaned_child = _clean_jsonb_surface(column, child_path, keys_parameter)
        replacement = (
            f"CASE WHEN jsonb_typeof({original_child}) = 'object' "
            f"THEN {cleaned_child} ELSE COALESCE({original_child}, 'null'::jsonb) END"
        )
        clean = f"jsonb_set({clean}, {_jsonb_path((child,))}, {replacement}, false)"
    return clean


def diagnostic_policy_sql(column: str) -> str:
    surfaces = tuple(
        column if not path else f"({column} #> {_jsonb_path(path)})"
        for path in METADATA_SEMANTIC_SURFACE_PATHS
    )
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
    return _clean_jsonb_surface(column, (), keys_parameter)


def business_policy_metadata(
    metadata: Mapping[str, Any] | None,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    original = dict(metadata or {})
    clean = _clean_metadata_surfaces(original)
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
