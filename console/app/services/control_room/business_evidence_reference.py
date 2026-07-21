from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.control_room.business_evidence_identity import (
    ADMINISTRATIVE_ID_FIELDS,
    legacy_scalar_reference,
    nested_administrative_references,
    source_and_id_pair,
    typed_reference,
)


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


def has_substantive_reference(
    value: Any,
    *,
    scalar_is_locator: bool,
    canonical_source_values: frozenset[str],
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
            local_exclusions = excluded_references | nested_administrative_references(
                value
            )
            typed = typed_reference(
                value,
                canonical_source_values=canonical_source_values,
                excluded_references=local_exclusions,
            )
            if typed is not None:
                return typed
            if source_and_id_pair(
                value,
                allow_generic_id=allow_generic_id,
                canonical_source_values=canonical_source_values,
                excluded_references=local_exclusions,
            ):
                return True
            for key, nested in value.items():
                normalized = str(key).strip().lower()
                if normalized in ADMINISTRATIVE_ID_FIELDS:
                    continue
                if normalized in _LOCATOR_FIELDS and legacy_scalar_reference(
                    nested,
                    canonical_source_values=canonical_source_values,
                    excluded_references=local_exclusions,
                ):
                    return True
                if normalized in _REFERENCE_CONTAINERS and has_substantive_reference(
                    nested,
                    scalar_is_locator=True,
                    allow_generic_id=True,
                    canonical_source_values=canonical_source_values,
                    excluded_references=local_exclusions,
                    seen=seen,
                ):
                    return True
                if isinstance(nested, (Mapping, list, tuple)) and (
                    has_substantive_reference(
                        nested,
                        scalar_is_locator=False,
                        canonical_source_values=canonical_source_values,
                        excluded_references=local_exclusions,
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
            local_exclusions = excluded_references | nested_administrative_references(
                value
            )
            return any(
                has_substantive_reference(
                    entry,
                    scalar_is_locator=scalar_is_locator,
                    allow_generic_id=allow_generic_id,
                    canonical_source_values=canonical_source_values,
                    excluded_references=local_exclusions,
                    seen=seen,
                )
                for entry in value
            )
        finally:
            seen.remove(identity)
    return bool(
        scalar_is_locator
        and legacy_scalar_reference(
            value,
            canonical_source_values=canonical_source_values,
            excluded_references=excluded_references,
        )
    )


__all__ = ("has_substantive_reference",)
