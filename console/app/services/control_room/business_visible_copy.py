from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.services.control_room.business_copy_hazards import (
    contains_structured_copy,
    contains_unsafe_unicode,
    is_diagnostic_copy,
)
from app.services.control_room.business_copy_sensitivity import contains_sensitive_copy
from app.services.control_room.business_surface_identity import (
    BusinessSurfaceIdentity,
    is_technical_surface_copy,
)


MAX_VISIBLE_COPY_SCAN_LENGTH = 8192


class VisibleCopyCause(StrEnum):
    ALLOWED = "allowed"
    EMPTY = "empty"
    INVALID_TYPE = "invalid_type"
    TOO_LONG = "too_long"
    SENSITIVE = "sensitive"
    TECHNICAL_IDENTIFIER = "technical_identifier"
    DIAGNOSTIC_ONLY = "diagnostic_only"
    UNSAFE_UNICODE = "unsafe_unicode"
    STRUCTURED = "structured"


@dataclass(frozen=True)
class VisibleCopyResult:
    allowed: bool
    text: str | None
    cause: VisibleCopyCause


def _rejection_cause(
    text: str,
    *,
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
) -> VisibleCopyCause | None:
    if contains_unsafe_unicode(text):
        return VisibleCopyCause.UNSAFE_UNICODE
    if contains_structured_copy(text):
        return VisibleCopyCause.STRUCTURED
    if contains_sensitive_copy(text):
        return VisibleCopyCause.SENSITIVE
    if is_technical_surface_copy(item, identity, text):
        return VisibleCopyCause.TECHNICAL_IDENTIFIER
    if is_diagnostic_copy(text):
        return VisibleCopyCause.DIAGNOSTIC_ONLY
    return None


def classify_visible_business_copy(
    value: object,
    *,
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
    max_length: int,
) -> VisibleCopyResult:
    if not isinstance(value, str):
        return VisibleCopyResult(False, None, VisibleCopyCause.INVALID_TYPE)
    if len(value) > MAX_VISIBLE_COPY_SCAN_LENGTH:
        return VisibleCopyResult(False, None, VisibleCopyCause.TOO_LONG)
    text = value.strip()
    if not text:
        return VisibleCopyResult(False, None, VisibleCopyCause.EMPTY)
    if cause := _rejection_cause(text, item=item, identity=identity):
        return VisibleCopyResult(False, None, cause)
    projected = text[:max_length]
    if projected != text and (
        cause := _rejection_cause(projected, item=item, identity=identity)
    ):
        return VisibleCopyResult(False, None, cause)
    return VisibleCopyResult(True, projected, VisibleCopyCause.ALLOWED)


__all__ = (
    "MAX_VISIBLE_COPY_SCAN_LENGTH",
    "VisibleCopyCause",
    "VisibleCopyResult",
    "classify_visible_business_copy",
)
