from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence

from app.services.control_room.business_copy_detection import (
    MAX_VISIBLE_COPY_SCAN_LENGTH,
    canonicalize_detection_separators,
)
from app.services.control_room.business_copy_sensitivity import contains_sensitive_copy
from app.services.control_room.business_copy_unicode import security_detection_forms
from app.services.control_room.business_copy_hazards import (
    contains_structured_copy,
    contains_unsafe_unicode,
    is_diagnostic_copy,
)
from app.services.intelligence.business_labels import business_label
from app.services.public_identifier_sensitivity import (
    contains_public_identifier_copy,
    is_public_technical_structure_key,
)
from app.services.public_path_sensitivity import (
    decoded_form_contains_public_path_or_resource,
    public_encoding_scan,
)
from app.services.public_sql_sensitivity import contains_public_sql


_MAX_NESTING_DEPTH = 8
_MAX_CONTAINER_ITEMS = 256
_PUBLIC_MISSING_LABELS = frozenset(
    {
        "(sin nombre)",
        "desconocida",
        "desconocido",
        "missing",
        "n.a.",
        "n.a",
        "n/a",
        "na",
        "nan",
        "none",
        "not available",
        "not applicable",
        "not provided",
        "not set",
        "no data",
        "inf",
        "infinity",
        "-inf",
        "-infinity",
        "null",
        "nil",
        "ok",
        "partial",
        "ready",
        "sin datos",
        "sin información",
        "sin nombre",
        "undefined",
        "unknown",
        "void",
    }
)
_NUMBER_ONLY_LABEL = re.compile(
    r"[+-]?(?:(?:\d[\d_]*)(?:[.,]\d[\d_]*)?(?:e[+-]?\d+)?|"
    r"[.,]\d+|0[bxo][0-9a-f_]+)",
    re.IGNORECASE,
)


def _technical_form(value: str) -> bool:
    return bool(
        decoded_form_contains_public_path_or_resource(value)
        or contains_public_sql(value)
        or contains_public_identifier_copy(value)
        or contains_structured_copy(value)
        or contains_unsafe_unicode(value)
    )


def contains_public_technical_copy(value: str) -> bool:
    """Return whether a scalar string is unsuitable for a public response."""

    if not isinstance(value, str) or len(value) > MAX_VISIBLE_COPY_SCAN_LENGTH:
        return True
    normalized = unicodedata.normalize("NFKC", value)
    if len(normalized) > MAX_VISIBLE_COPY_SCAN_LENGTH:
        return True
    for security_form in security_detection_forms(normalized):
        if len(security_form) > MAX_VISIBLE_COPY_SCAN_LENGTH:
            return True
        candidates, overflowed = public_encoding_scan(security_form)
        if overflowed:
            return True
        for candidate in candidates:
            if len(candidate) > MAX_VISIBLE_COPY_SCAN_LENGTH:
                return True
            if contains_sensitive_copy(candidate) or _technical_form(
                canonicalize_detection_separators(candidate)
            ):
                return True
    return False


def contains_public_technical_data(
    value: object,
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
) -> bool:
    """Inspect bounded nested keys and values without stringifying objects."""

    if isinstance(value, str):
        return contains_public_technical_copy(value)
    if isinstance(value, bytes):
        return True
    if value is None or type(value) in {bool, int, float}:
        return False
    if _depth >= _MAX_NESTING_DEPTH:
        return True
    seen = _seen if _seen is not None else set()
    marker = id(value)
    if marker in seen:
        return True
    seen.add(marker)
    try:
        if isinstance(value, Mapping):
            if len(value) > _MAX_CONTAINER_ITEMS:
                return True
            return any(
                not isinstance(key, str)
                or is_public_technical_structure_key(key)
                or contains_public_technical_copy(key)
                or contains_public_technical_data(
                    nested,
                    _depth=_depth + 1,
                    _seen=seen,
                )
                for key, nested in value.items()
            )
        if isinstance(value, Sequence):
            if len(value) > _MAX_CONTAINER_ITEMS:
                return True
            return any(
                contains_public_technical_data(
                    nested,
                    _depth=_depth + 1,
                    _seen=seen,
                )
                for nested in value
            )
        return True
    finally:
        seen.remove(marker)


def public_business_label(value: object) -> str | None:
    """Normalize a label, then reject technical rather than business copy."""

    normalized = business_label(value)
    missing_form = (
        normalized.casefold().strip(" \t\r\n()[]{}<>.,;:!?\"'“”‘’")
        if normalized
        else ""
    )
    numeric_form = re.sub(r"[\s'_]", "", missing_form)
    numeric_without_letters = any(
        character.isnumeric() for character in missing_form
    ) and not any(character.isalpha() for character in missing_form)
    if (
        normalized is None
        or missing_form in _PUBLIC_MISSING_LABELS
        or _NUMBER_ONLY_LABEL.fullmatch(numeric_form)
        or numeric_without_letters
        or not any(character.isalnum() for character in normalized)
        or is_diagnostic_copy(normalized)
        or contains_public_technical_copy(normalized)
    ):
        return None
    return normalized


__all__ = (
    "contains_public_technical_copy",
    "contains_public_technical_data",
    "public_business_label",
)
