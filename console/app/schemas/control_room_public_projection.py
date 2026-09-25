from __future__ import annotations

import re
import types
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime
from math import isfinite
from typing import Any, Literal, Union, get_args, get_origin

from app.services.control_room.business_copy_sensitivity import (
    contains_sensitive_copy,
)
from app.services.control_room.business_copy_unicode import security_detection_forms
from app.services.control_room.business_surface_identity import (
    BusinessSurfaceIdentity,
)
from app.services.control_room.business_visible_copy import (
    VisibleCopyCause,
    classify_visible_business_copy,
)
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from app.services.public_text_sensitivity import contains_public_technical_copy
from pydantic import BaseModel, ConfigDict, field_validator

PublicScalar = int | float | bool | None
_OMIT = object()
_PUBLIC_IDENTITY = BusinessSurfaceIdentity(
    domain="public",
    cartridge_id="public",
    module_id="public",
)
_PROSE_FIELDS = frozenset(
    {
        "action_taken",
        "description",
        "detail",
        "explanation",
        "expected_outcome",
        "hypothesis",
        "impact",
        "label",
        "learned_rule",
        "message",
        "note",
        "outcome_summary",
        "rationale",
        "reason",
        "recommendation",
        "root_cause",
        "rule",
        "title",
    }
)
_BUSINESS_IDENTIFIER_FIELDS = frozenset({"box_id", "employee_key", "id"})
_VISIBLE_REJECTIONS = frozenset(
    {
        VisibleCopyCause.DIAGNOSTIC_ONLY,
        VisibleCopyCause.SENSITIVE,
        VisibleCopyCause.STRUCTURED,
        VisibleCopyCause.TECHNICAL_IDENTIFIER,
        VisibleCopyCause.TOO_LONG,
        VisibleCopyCause.UNSAFE_UNICODE,
    }
)
_EXPLICIT_SECRET_MARKER = re.compile(
    r"(?i)(?:^|[^a-z0-9])"
    r"(?:password|credentials?|(?:oauth[_-]?)?access(?:[_-]?token)?|"
    r"refresh(?:[_-]?token)?|api(?:[_-]?key)?|client(?:[_-]?secret)?|"
    r"private(?:[_-]?key)?)"
    r"[_-]+(?:canary|marker|sentinel)(?:$|[^a-z0-9])"
)
_PUBLIC_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_SENSITIVE_SLUG_PART = re.compile(
    r"(?:^|[_-])(?:credential|password|private[_-]key|secret|token)(?:[_-]|$)"
)


def _safe_text(value: object, *, field: str) -> str | object:
    if isinstance(value, (datetime, date)):
        value = value.isoformat()
    if not isinstance(value, str):
        return _OMIT
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        return ""
    detection_forms = security_detection_forms(normalized)
    diagnostic_field = "" if field in _BUSINESS_IDENTIFIER_FIELDS else field
    diagnostic = redact_diagnostic_value(normalized, field=diagnostic_field)
    if (
        diagnostic != normalized
        or contains_sensitive_copy(normalized)
        or contains_public_technical_copy(normalized)
        or any(
            _EXPLICIT_SECRET_MARKER.search(candidate) for candidate in detection_forms
        )
    ):
        return "[REDACTED]"
    if field in _PROSE_FIELDS:
        result = classify_visible_business_copy(
            normalized,
            item={},
            identity=_PUBLIC_IDENTITY,
            max_length=1_000,
        )
        if not result.allowed and result.cause in _VISIBLE_REJECTIONS:
            return "[REDACTED]"
    return normalized[:1_000]


def _project_union(annotation: Any, value: object, *, field: str) -> object:
    options = get_args(annotation)
    if value is None and type(None) in options:
        return None
    if type(value) is float and not isfinite(value):
        return None if type(None) in options else _OMIT
    for option in options:
        if option is type(None):
            if value is None:
                return None
            continue
        projected = _project_value(option, value, field=field)
        if projected is not _OMIT:
            return projected
    return _OMIT


def _project_value(annotation: Any, value: object, *, field: str) -> object:
    origin = get_origin(annotation)
    if origin in {Union, types.UnionType}:
        return _project_union(annotation, value, field=field)
    if origin is Literal:
        projected = _safe_text(value, field=field)
        return projected if projected in get_args(annotation) else _OMIT
    if origin is list:
        if not isinstance(value, (list, tuple)):
            return _OMIT
        item_type = get_args(annotation)[0]
        items = [
            projected
            for item in value
            if (projected := _project_value(item_type, item, field=field)) is not _OMIT
        ]
        return items
    if isinstance(annotation, type) and issubclass(annotation, PublicProjectionModel):
        return annotation.project(value)
    if annotation is str:
        return _safe_text(value, field=field)
    if annotation is bool:
        return value if type(value) is bool else _OMIT
    if annotation is int:
        return value if type(value) is int else _OMIT
    if annotation is float:
        if type(value) not in {int, float}:
            return _OMIT
        try:
            projected = float(value)
        except OverflowError:
            return _OMIT
        return projected if isfinite(projected) else _OMIT
    return _OMIT


class PublicProjectionModel(BaseModel):

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def project(cls, value: object) -> PublicProjectionModel:
        source = value if isinstance(value, Mapping) else {}
        projected: dict[str, object] = {}
        for name, model_field in cls.model_fields.items():
            if name not in source:
                continue
            item = _project_value(model_field.annotation, source[name], field=name)
            if item is not _OMIT:
                projected[name] = item
        return cls.model_validate(projected)


class PublicSlugIdentity(PublicProjectionModel):
    id: str | None = None

    @field_validator("id")
    @classmethod
    def validate_public_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return (
            value
            if _PUBLIC_SLUG.fullmatch(value) and not _SENSITIVE_SLUG_PART.search(value)
            else None
        )


def project_public_control_room_response(
    model: type[PublicProjectionModel],
    value: object,
) -> PublicProjectionModel:
    return model.project(value)


__all__ = (
    "PublicProjectionModel",
    "PublicScalar",
    "PublicSlugIdentity",
    "project_public_control_room_response",
)
