from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.services.control_room.business_runtime_evidence import (
    verified_runtime_row_reference,
)


ADMINISTRATIVE_ID_FIELDS = frozenset(
    {"account_id", "owner_user_id", "tenant_id", "user_id", "workspace_id"}
)
SOURCE_FIELDS = frozenset(
    {
        "dataset",
        "gold_table",
        "source",
        "source_dataset",
        "source_ref",
        "source_system",
        "table",
    }
)
CANONICAL_SOURCE_FIELDS = SOURCE_FIELDS - {"source", "source_ref"}
SOURCE_FIELD_ROLES = {
    "dataset": "dataset",
    "gold_table": "dataset",
    "source_dataset": "dataset",
    "table": "dataset",
    "source_system": "system",
}
_EVIDENCE_ID_FIELDS = frozenset(
    {
        "artifact_id",
        "document_id",
        "evidence_id",
        "evidence_pack_id",
        "record_id",
        "reference_id",
        "source_record_id",
    }
)
_SERVER_VERIFIED_ID_FIELDS = frozenset({"record_id", "source_record_id"})
_PLACEHOLDERS = frozenset(
    {
        "-",
        "--",
        "missing",
        "n/a",
        "n.a.",
        "na",
        "nil",
        "none",
        "not applicable",
        "not available",
        "null",
        "undefined",
        "unknown",
    }
)
_ID_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._:@/-]*$", re.IGNORECASE)
_TYPED_EVIDENCE_IDS = {
    "artifact": frozenset({"artifact_id"}),
    "dataset_row": frozenset({"record_id", "source_record_id"}),
    "document": frozenset({"document_id"}),
    "record": frozenset({"record_id", "source_record_id"}),
}


def reference_token(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def stable_text(value: Any) -> bool:
    return isinstance(value, str) and bool(
        (normalized := reference_token(value)) and normalized not in _PLACEHOLDERS
    )


def canonical_sources(
    surfaces: Iterable[Mapping[str, Any]],
) -> dict[str, frozenset[str]]:
    sources: dict[str, set[str]] = {}
    for values in surfaces:
        for key, value in values.items():
            role = SOURCE_FIELD_ROLES.get(str(key).strip().lower())
            if role and stable_text(value):
                sources.setdefault(role, set()).add(reference_token(value))
    return {role: frozenset(values) for role, values in sources.items()}


def _all_source_values(sources: Mapping[str, frozenset[str]]) -> frozenset[str]:
    return frozenset(value for values in sources.values() for value in values)


def _source_matches(
    key: Any,
    value: Any,
    sources: Mapping[str, frozenset[str]],
) -> bool:
    field = str(key).strip().lower()
    token = reference_token(value)
    role = SOURCE_FIELD_ROLES.get(field)
    if role:
        return token in sources.get(role, frozenset())
    return field in {"source", "source_ref"} and token in _all_source_values(sources)


def administrative_references(
    surfaces: Iterable[Mapping[str, Any]],
) -> frozenset[str]:
    return frozenset(
        token
        for values in surfaces
        for key in ADMINISTRATIVE_ID_FIELDS
        if key in values and (token := reference_token(values.get(key)))
    )


def nested_administrative_references(
    value: Any,
    *,
    seen: set[int] | None = None,
) -> frozenset[str]:
    if not isinstance(value, (Mapping, Sequence)) or isinstance(
        value, (str, bytes, bytearray)
    ):
        return frozenset()
    seen = seen if seen is not None else set()
    identity = id(value)
    if identity in seen:
        return frozenset()
    seen.add(identity)
    try:
        references: set[str] = set()
        if isinstance(value, Mapping):
            references.update(administrative_references((value,)))
            nested_values = value.values()
        else:
            nested_values = value
        for nested in nested_values:
            references.update(nested_administrative_references(nested, seen=seen))
        return frozenset(references)
    finally:
        seen.remove(identity)


def contains_excluded(value: Any, excluded: frozenset[str]) -> bool:
    normalized = reference_token(value)
    components = frozenset(
        component for component in re.split(r"[^a-z0-9._-]+", normalized) if component
    )
    return any(
        reference
        and (
            reference in components
            or bool(
                re.search(
                    rf"(?<![a-z0-9]){re.escape(reference)}(?![a-z0-9])",
                    normalized,
                )
            )
            or (len(reference) >= 8 and reference in normalized)
        )
        for reference in excluded
    )


def structured_id(value: Any, excluded: frozenset[str]) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return value >= 0 and not contains_excluded(value, excluded)
    return bool(
        stable_text(value)
        and _ID_TOKEN.fullmatch(value.strip())
        and not contains_excluded(value, excluded)
    )


def legacy_scalar_reference(
    value: Any,
    *,
    canonical_sources_by_role: Mapping[str, frozenset[str]],
    excluded_references: frozenset[str],
) -> bool:
    if not stable_text(value) or contains_excluded(value, excluded_references):
        return False
    parts = re.split(r"[:/]", value.strip(), maxsplit=1)
    return bool(
        len(parts) == 2
        and reference_token(parts[0]) in _all_source_values(canonical_sources_by_role)
        and structured_id(parts[1], excluded_references)
    )


def _is_id_field(key: Any, *, allow_generic_id: bool) -> bool:
    normalized = str(key).strip().lower()
    return (
        normalized in _EVIDENCE_ID_FIELDS
        and normalized not in _SERVER_VERIFIED_ID_FIELDS
    ) or (allow_generic_id and normalized == "id")


def source_and_id_pair(
    values: Mapping[str, Any],
    *,
    allow_generic_id: bool,
    canonical_sources_by_role: Mapping[str, frozenset[str]],
    excluded_references: frozenset[str],
) -> bool:
    local_exclusions = excluded_references | administrative_references((values,))
    return any(
        str(key).strip().lower() in SOURCE_FIELDS
        and _source_matches(key, value, canonical_sources_by_role)
        and not contains_excluded(value, local_exclusions)
        for key, value in values.items()
    ) and any(
        _is_id_field(key, allow_generic_id=allow_generic_id)
        and structured_id(value, local_exclusions)
        for key, value in values.items()
    )


def typed_reference(
    values: Mapping[str, Any],
    *,
    canonical_sources_by_role: Mapping[str, frozenset[str]],
    excluded_references: frozenset[str],
) -> bool | None:
    evidence_type = reference_token(values.get("type"))
    if not evidence_type:
        return None
    id_fields = _TYPED_EVIDENCE_IDS.get(evidence_type)
    if id_fields is None:
        return False
    local_exclusions = excluded_references | administrative_references((values,))
    has_source = any(
        str(key).strip().lower() in SOURCE_FIELDS
        and _source_matches(key, value, canonical_sources_by_role)
        for key, value in values.items()
    )
    has_id = any(
        str(key).strip().lower() in id_fields and structured_id(value, local_exclusions)
        for key, value in values.items()
    )
    if id_fields & _SERVER_VERIFIED_ID_FIELDS:
        has_source = _source_matches(
            "source_dataset",
            values.get("source_dataset"),
            canonical_sources_by_role,
        )
        has_id = verified_runtime_row_reference(values) and not contains_excluded(
            values.get("source_record_id") or values.get("record_id"),
            local_exclusions,
        )
    return has_source and has_id


__all__ = (
    "ADMINISTRATIVE_ID_FIELDS",
    "CANONICAL_SOURCE_FIELDS",
    "SOURCE_FIELD_ROLES",
    "SOURCE_FIELDS",
    "administrative_references",
    "canonical_sources",
    "contains_excluded",
    "legacy_scalar_reference",
    "nested_administrative_references",
    "source_and_id_pair",
    "typed_reference",
)
