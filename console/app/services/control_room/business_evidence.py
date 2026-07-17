from __future__ import annotations

import math
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
_REFERENCE_FIELDS = frozenset(
    {
        "artifact",
        "artifact_ref",
        "dataset",
        "document",
        "document_ref",
        "gold_table",
        "href",
        "path",
        "query",
        "ref",
        "reference",
        "source",
        "source_dataset",
        "source_ref",
        "sql",
        "table",
        "uri",
        "url",
    }
)
_REFERENCE_CONTAINERS = frozenset(
    {"artifacts", "documents", "items", "references", "refs", "sources"}
)


def _stable_reference(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return value > 0
    if isinstance(value, float):
        return math.isfinite(value) and value > 0
    return False


def _is_id_field(key: Any) -> bool:
    normalized = str(key).strip().lower()
    return normalized == "id" or normalized.endswith("_id")


def _is_reference_field(key: Any) -> bool:
    normalized = str(key).strip().lower()
    return normalized in _REFERENCE_FIELDS or normalized.endswith(
        ("_ref", "_uri", "_url")
    )


def _has_substantive_reference(
    value: Any,
    *,
    scalar_is_reference: bool,
    seen: set[int] | None = None,
) -> bool:
    if isinstance(value, Mapping):
        if not value:
            return False
        seen = seen or set()
        identity = id(value)
        if identity in seen:
            return False
        seen.add(identity)
        try:
            for key, nested in value.items():
                normalized = str(key).strip().lower()
                if (_is_id_field(key) or _is_reference_field(key)) and (
                    _stable_reference(nested)
                    or _has_substantive_reference(
                        nested,
                        scalar_is_reference=True,
                        seen=seen,
                    )
                ):
                    return True
                if normalized in _REFERENCE_CONTAINERS and _has_substantive_reference(
                    nested,
                    scalar_is_reference=True,
                    seen=seen,
                ):
                    return True
                if isinstance(nested, (Mapping, list, tuple)) and (
                    _has_substantive_reference(
                        nested,
                        scalar_is_reference=False,
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
        seen = seen or set()
        identity = id(value)
        if identity in seen:
            return False
        seen.add(identity)
        try:
            return any(
                _has_substantive_reference(
                    entry,
                    scalar_is_reference=scalar_is_reference,
                    seen=seen,
                )
                for entry in value
            )
        finally:
            seen.remove(identity)
    return scalar_is_reference and _stable_reference(value)


def _evidence_pack_is_substantive(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return _has_substantive_reference(value, scalar_is_reference=True)
    for key, nested in value.items():
        if _is_id_field(key) and _stable_reference(nested):
            return True
    if "items" in value:
        return _has_substantive_reference(value.get("items"), scalar_is_reference=True)
    return _has_substantive_reference(value, scalar_is_reference=False)


def has_evidence(item: Mapping[str, Any]) -> bool:
    for values in semantic_maps(item):
        if any(
            key in values and _stable_reference(values.get(key))
            for key in EVIDENCE_ID_FIELDS
        ):
            return True
        for key in EVIDENCE_FIELDS:
            if key not in values:
                continue
            evidence = values.get(key)
            if key == "evidence_pack":
                if _evidence_pack_is_substantive(evidence):
                    return True
            elif _has_substantive_reference(evidence, scalar_is_reference=True):
                return True
    return False


__all__ = ("EVIDENCE_FIELDS", "EVIDENCE_ID_FIELDS", "has_evidence")
