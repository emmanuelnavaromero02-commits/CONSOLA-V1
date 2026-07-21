from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


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
) -> frozenset[str]:
    return frozenset(
        reference_token(value)
        for values in surfaces
        for key, value in values.items()
        if str(key).strip().lower() in CANONICAL_SOURCE_FIELDS and stable_text(value)
    )


def administrative_references(
    surfaces: Iterable[Mapping[str, Any]],
) -> frozenset[str]:
    return frozenset(
        token
        for values in surfaces
        for key in ADMINISTRATIVE_ID_FIELDS
        if key in values and (token := reference_token(values.get(key)))
    )


def contains_excluded(value: Any, excluded: frozenset[str]) -> bool:
    normalized = reference_token(value)
    components = frozenset(
        component for component in re.split(r"[^a-z0-9._-]+", normalized) if component
    )
    return any(
        reference
        and (
            reference in components or (len(reference) >= 8 and reference in normalized)
        )
        for reference in excluded
    )


def structured_id(value: Any, excluded: frozenset[str]) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return value >= 0
    return bool(
        stable_text(value)
        and _ID_TOKEN.fullmatch(value.strip())
        and not contains_excluded(value, excluded)
    )


def source_matches(value: Any, sources: frozenset[str]) -> bool:
    if not stable_text(value):
        return False
    normalized = reference_token(value)
    if normalized in sources:
        return True
    return re.split(r"[:/]", normalized, maxsplit=1)[0] in sources


def legacy_scalar_reference(
    value: Any,
    *,
    canonical_source_values: frozenset[str],
    excluded_references: frozenset[str],
) -> bool:
    if not stable_text(value) or contains_excluded(value, excluded_references):
        return False
    parts = re.split(r"[:/]", value.strip(), maxsplit=1)
    return bool(
        len(parts) == 2
        and reference_token(parts[0]) in canonical_source_values
        and structured_id(parts[1], excluded_references)
    )


def _is_id_field(key: Any, *, allow_generic_id: bool) -> bool:
    normalized = str(key).strip().lower()
    return normalized in _EVIDENCE_ID_FIELDS or (
        allow_generic_id and normalized == "id"
    )


def source_and_id_pair(
    values: Mapping[str, Any],
    *,
    allow_generic_id: bool,
    canonical_source_values: frozenset[str],
    excluded_references: frozenset[str],
) -> bool:
    return any(
        str(key).strip().lower() in SOURCE_FIELDS
        and source_matches(value, canonical_source_values)
        and not contains_excluded(value, excluded_references)
        for key, value in values.items()
    ) and any(
        _is_id_field(key, allow_generic_id=allow_generic_id)
        and structured_id(value, excluded_references)
        for key, value in values.items()
    )


def typed_reference(
    values: Mapping[str, Any],
    *,
    canonical_source_values: frozenset[str],
    excluded_references: frozenset[str],
) -> bool | None:
    evidence_type = reference_token(values.get("type"))
    if not evidence_type:
        return None
    id_fields = _TYPED_EVIDENCE_IDS.get(evidence_type)
    if id_fields is None:
        return False
    has_source = any(
        str(key).strip().lower() in SOURCE_FIELDS
        and reference_token(value) in canonical_source_values
        for key, value in values.items()
    )
    has_id = any(
        str(key).strip().lower() in id_fields
        and structured_id(value, excluded_references)
        for key, value in values.items()
    )
    return has_source and has_id


__all__ = (
    "ADMINISTRATIVE_ID_FIELDS",
    "CANONICAL_SOURCE_FIELDS",
    "SOURCE_FIELDS",
    "administrative_references",
    "canonical_sources",
    "contains_excluded",
    "legacy_scalar_reference",
    "source_and_id_pair",
    "typed_reference",
)
