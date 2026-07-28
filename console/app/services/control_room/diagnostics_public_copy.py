from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from types import MappingProxyType

from app.services.control_room.business_copy_detection import (
    MAX_VISIBLE_COPY_SCAN_LENGTH,
    canonicalize_detection_separators,
)
from app.services.control_room.business_copy_hazards import (
    contains_structured_copy,
    contains_unsafe_unicode,
)
from app.services.control_room.business_copy_sensitivity import contains_sensitive_copy
from app.services.control_room.business_copy_unicode import security_detection_forms
from app.services.control_room.business_diagnostic_grammar import (
    is_diagnostic_state_copy,
)
from app.services.public_identifier_sensitivity import contains_public_identifier_copy
from app.services.public_path_sensitivity import (
    decoded_form_contains_public_path_or_resource,
    public_encoding_scan,
)


DIAGNOSTICS_ENDPOINT = "diagnostics"
DIAGNOSTIC_ITEM_TITLE_FIELD = "diagnostic_items[].title"
SOURCE_ERROR_FIELD = "sources[].error"
DIAGNOSTIC_ITEM_ERROR_FIELD = "diagnostic_items[].error"
INSTALLATION_ERROR_FIELD = "installations[].error"

_TECHNICAL_DIAGNOSTIC_COPY_ID = object()
_SOURCE_QUERY_FAILED_COPY_ID = object()
_DIAGNOSTIC_ERROR_COPY_ID = object()
_INSTALLATION_ERROR_COPY_ID = object()

_PRODUCT_COPY_REGISTRY: Mapping[tuple[object, str, str], str] = MappingProxyType(
    {
        (
            _TECHNICAL_DIAGNOSTIC_COPY_ID,
            DIAGNOSTICS_ENDPOINT,
            DIAGNOSTIC_ITEM_TITLE_FIELD,
        ): "Technical diagnostic",
        (
            _SOURCE_QUERY_FAILED_COPY_ID,
            DIAGNOSTICS_ENDPOINT,
            SOURCE_ERROR_FIELD,
        ): "Source query failed",
        (
            _DIAGNOSTIC_ERROR_COPY_ID,
            DIAGNOSTICS_ENDPOINT,
            DIAGNOSTIC_ITEM_ERROR_FIELD,
        ): "Diagnostic error reported",
        (
            _INSTALLATION_ERROR_COPY_ID,
            DIAGNOSTICS_ENDPOINT,
            INSTALLATION_ERROR_FIELD,
        ): "Installation error reported",
    }
)


def _non_sql_hazard(value: str) -> bool:
    if len(value) > MAX_VISIBLE_COPY_SCAN_LENGTH or contains_unsafe_unicode(value):
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
            canonical = canonicalize_detection_separators(candidate)
            if (
                contains_sensitive_copy(candidate)
                or decoded_form_contains_public_path_or_resource(candidate)
                or contains_public_identifier_copy(canonical)
                or contains_structured_copy(candidate)
                or contains_unsafe_unicode(candidate)
                or is_diagnostic_state_copy(candidate)
            ):
                return True
    return False


def _resolve_server_copy(
    copy_id: object,
    endpoint: str,
    field: str,
    *,
    _registry: Mapping[tuple[object, str, str], object] | None = None,
) -> str | None:
    """Resolve an opaque server capability at one exact public position."""

    if isinstance(copy_id, (str, bytes, int, float, bool)) or copy_id is None:
        return None
    registry = _PRODUCT_COPY_REGISTRY if _registry is None else _registry
    try:
        literal = registry.get((copy_id, endpoint, field))
    except (AttributeError, TypeError):
        return None
    if type(literal) is not str or not literal or _non_sql_hazard(literal):
        return None
    return literal


__all__ = (
    "DIAGNOSTICS_ENDPOINT",
    "DIAGNOSTIC_ITEM_TITLE_FIELD",
)
