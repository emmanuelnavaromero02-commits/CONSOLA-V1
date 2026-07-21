from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.control_room.business_semantic_slots import semantic_maps


EVIDENCE_FIELDS = (
    "analysis_evidence",
    "evidence",
    "evidence_pack",
    "evidence_refs",
)
EVIDENCE_ID_FIELDS = ("evidence_pack_id", "evidence_id")
_EVIDENCE_ID_FIELDS = frozenset(
    {
        *EVIDENCE_ID_FIELDS,
        "artifact_id",
        "document_id",
        "record_id",
        "reference_id",
        "source_record_id",
    }
)
_ADMINISTRATIVE_ID_FIELDS = frozenset(
    {
        "account_id",
        "owner_user_id",
        "tenant_id",
        "user_id",
        "workspace_id",
    }
)
_SOURCE_FIELDS = frozenset(
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
_GENERIC_SOURCE_FIELDS = frozenset({"source", "source_ref"})
_LOCATOR_FIELDS = frozenset(
    {
        "artifact",
        "artifact_ref",
        "document",
        "document_ref",
        "href",
        "path",
        "query",
        "ref",
        "reference",
        "source",
        "source_ref",
        "sql",
        "uri",
        "url",
    }
)
_REFERENCE_CONTAINERS = frozenset(
    {"artifacts", "documents", "items", "references", "refs", "sources"}
)
_PLACEHOLDER_REFERENCES = frozenset(
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
_NAMESPACED_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._-]*:[^\s]+$", re.IGNORECASE)
_PATH_REFERENCE = re.compile(r"^[^\s/]+/[^\s]+$")
_ID_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._:@/-]*$", re.IGNORECASE)


def _reference_token(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _stable_text(value: Any) -> bool:
    return isinstance(value, str) and bool(
        (normalized := _reference_token(value))
        and normalized not in _PLACEHOLDER_REFERENCES
    )


def _structured_locator(value: Any) -> bool:
    if not _stable_text(value):
        return False
    normalized = value.strip()
    return bool(
        _NAMESPACED_REFERENCE.fullmatch(normalized)
        or _PATH_REFERENCE.fullmatch(normalized)
    )


def _structured_id(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return value >= 0
    return bool(_stable_text(value) and _ID_TOKEN.fullmatch(value.strip()))


def _structured_source(key: Any, value: Any) -> bool:
    normalized = str(key).strip().lower()
    if normalized in _GENERIC_SOURCE_FIELDS:
        return _structured_locator(value)
    return bool(
        normalized in _SOURCE_FIELDS
        and isinstance(value, str)
        and _stable_text(value)
        and _ID_TOKEN.fullmatch(value.strip())
    )


def _is_id_field(key: Any, *, allow_generic_id: bool) -> bool:
    normalized = str(key).strip().lower()
    return normalized in _EVIDENCE_ID_FIELDS or (
        allow_generic_id and normalized == "id"
    )


def _source_and_id_pair(
    values: Mapping[str, Any],
    *,
    allow_generic_id: bool,
    excluded_references: frozenset[str],
) -> bool:
    has_source = any(
        str(key).strip().lower() in _SOURCE_FIELDS
        and _structured_source(key, value)
        and _reference_token(value) not in excluded_references
        for key, value in values.items()
    )
    has_id = any(
        _is_id_field(key, allow_generic_id=allow_generic_id)
        and _structured_id(value)
        and _reference_token(value) not in excluded_references
        for key, value in values.items()
    )
    return has_source and has_id


def _has_substantive_reference(
    value: Any,
    *,
    scalar_is_locator: bool,
    allow_generic_id: bool = False,
    excluded_references: frozenset[str] = frozenset(),
    seen: set[int] | None = None,
) -> bool:
    if isinstance(value, Mapping):
        if not value:
            return False
        seen = seen if seen is not None else set()
        identity = id(value)
        if identity in seen:
            return False
        seen.add(identity)
        try:
            if _source_and_id_pair(
                value,
                allow_generic_id=allow_generic_id,
                excluded_references=excluded_references,
            ):
                return True
            for key, nested in value.items():
                normalized = str(key).strip().lower()
                if normalized in _ADMINISTRATIVE_ID_FIELDS:
                    continue
                if (
                    normalized in _LOCATOR_FIELDS
                    and _structured_locator(nested)
                    and _reference_token(nested) not in excluded_references
                ):
                    return True
                if normalized in _REFERENCE_CONTAINERS and (
                    _has_substantive_reference(
                        nested,
                        scalar_is_locator=True,
                        allow_generic_id=True,
                        excluded_references=excluded_references,
                        seen=seen,
                    )
                ):
                    return True
                if isinstance(nested, (Mapping, list, tuple)) and (
                    _has_substantive_reference(
                        nested,
                        scalar_is_locator=False,
                        excluded_references=excluded_references,
                        seen=seen,
                    )
                ):
                    return True
            return False
        finally:
            seen.remove(identity)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            return False
        seen = seen if seen is not None else set()
        identity = id(value)
        if identity in seen:
            return False
        seen.add(identity)
        try:
            return any(
                _has_substantive_reference(
                    entry,
                    scalar_is_locator=scalar_is_locator,
                    allow_generic_id=allow_generic_id,
                    excluded_references=excluded_references,
                    seen=seen,
                )
                for entry in value
            )
        finally:
            seen.remove(identity)
    return bool(
        scalar_is_locator
        and _structured_locator(value)
        and _reference_token(value) not in excluded_references
    )


def has_evidence(item: Mapping[str, Any]) -> bool:
    excluded_references = frozenset(
        token
        for values in semantic_maps(item)
        for key in _ADMINISTRATIVE_ID_FIELDS
        if key in values and (token := _reference_token(values.get(key)))
    )
    for values in semantic_maps(item):
        if any(key in values for key in EVIDENCE_ID_FIELDS) and _source_and_id_pair(
            values,
            allow_generic_id=False,
            excluded_references=excluded_references,
        ):
            return True
        for key in EVIDENCE_FIELDS:
            if key in values and _has_substantive_reference(
                values.get(key),
                scalar_is_locator=True,
                allow_generic_id=True,
                excluded_references=excluded_references,
            ):
                return True
    return False


__all__ = ("EVIDENCE_FIELDS", "EVIDENCE_ID_FIELDS", "has_evidence")
