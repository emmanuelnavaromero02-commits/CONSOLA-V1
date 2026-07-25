from __future__ import annotations

import base64
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.services.control_room.business_surface_identity import (
    BusinessSurfaceIdentity,
    is_technical_surface_copy,
)
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value


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


_RECOGNIZABLE_SECRET = re.compile(
    r"(?ix)"
    r"(?:"
    r"(?<![A-Z0-9_-])eyJ[A-Z0-9_-]{5,}\.[A-Z0-9_-]{2,}\."
    r"[A-Z0-9_-]{5,}(?![A-Z0-9_-])"
    r"|\b(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|ASCA)[A-Z0-9]{16}\b"
    r"|\bAIza[A-Z0-9_-]{35}\b"
    r"|\bgh[pousr]_[A-Z0-9]{20,255}\b"
    r"|\bgithub_pat_[A-Z0-9_]{20,255}\b"
    r"|-----BEGIN[^\n-]*PRIVATE KEY-----"
    r")"
)
_JWT_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"([A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{5,})"
    r"(?![A-Za-z0-9_-])"
)
_UNICODE_EMAIL = re.compile(
    r"(?iu)(?<![\w@])[\w.!#$%&'*+/=?^`{|}~-]+@" r"(?:[\w-]+\.)+[\w-]{2,}(?![\w@])"
)
_UNSAFE_BIDI = frozenset(
    {
        "LRE",
        "RLE",
        "LRO",
        "RLO",
        "PDF",
        "LRI",
        "RLI",
        "FSI",
        "PDI",
    }
)
_DIAGNOSTIC_ONLY = frozenset(
    {
        "blocked",
        "cartucho inactivo o bloqueado",
        "cartucho sin permiso para este usuario",
        "dataset con contrato invalido",
        "dataset no materializado",
        "dataset requerido no registrado",
        "dataset sin materializar",
        "datos no disponibles",
        "empty",
        "error",
        "faltan datos",
        "fuente operativa no disponible",
        "fuente parcial no apta para operacion completa",
        "fuente requiere atencion",
        "fuente sin datos materializados",
        "fuente stub no apta para decisiones operativas",
        "insufficient data",
        "invalid schema",
        "materialization pending",
        "missing",
        "n d",
        "nd",
        "no disponible",
        "no permission",
        "schema only",
        "sin datos",
        "sin permiso",
        "source state",
        "source unavailable",
        "stub",
        "technical diagnostic",
        "unavailable",
    }
)
_DIAGNOSTIC_WORDS = frozenset(
    {
        "apta",
        "atencion",
        "blocked",
        "bloqueado",
        "cartucho",
        "completa",
        "contrato",
        "data",
        "dataset",
        "datos",
        "decisiones",
        "diagnostic",
        "disponible",
        "empty",
        "error",
        "este",
        "faltan",
        "fuente",
        "inactivo",
        "insufficient",
        "invalid",
        "invalido",
        "materialization",
        "materializado",
        "materializados",
        "missing",
        "no",
        "only",
        "operacion",
        "operativa",
        "parcial",
        "para",
        "pending",
        "permission",
        "registrado",
        "requiere",
        "requerido",
        "schema",
        "sin",
        "source",
        "state",
        "stub",
        "technical",
        "unavailable",
        "usuario",
    }
)


def _diagnostic_form(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return " ".join(re.findall(r"[^\W_]+", without_marks.casefold()))


def _unsafe_unicode(value: str) -> bool:
    return any(
        unicodedata.category(character) in {"Cc", "Cf"}
        or unicodedata.bidirectional(character) in _UNSAFE_BIDI
        for character in value
    )


def _structured_copy(value: str) -> bool:
    if value[:1] not in "[{" or value[-1:] not in "]}":
        return False
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, (Mapping, list))


def _sensitive_copy(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    return bool(
        redact_diagnostic_value(value) != value
        or redact_diagnostic_value(normalized) != normalized
        or _RECOGNIZABLE_SECRET.search(normalized)
        or _contains_jwt(normalized)
        or _UNICODE_EMAIL.search(normalized)
    )


def _decoded_json_segment(segment: str) -> tuple[bool, object]:
    if len(segment) > 4096:
        return False, None
    padded = f"{segment}{'=' * (-len(segment) % 4)}"
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        return True, json.loads(decoded)
    except (UnicodeDecodeError, ValueError):
        return False, None


def _contains_jwt(value: str) -> bool:
    for match in _JWT_CANDIDATE.finditer(value):
        header_segment, payload_segment, _signature = match.group(1).split(".")
        header_ok, header = _decoded_json_segment(header_segment)
        payload_ok, payload = _decoded_json_segment(payload_segment)
        if (
            header_ok
            and payload_ok
            and isinstance(header, Mapping)
            and isinstance(header.get("alg"), str)
            and isinstance(payload, Mapping)
        ):
            return True
    return False


def _rejection_cause(
    text: str,
    *,
    item: Mapping[str, object],
    identity: BusinessSurfaceIdentity,
) -> VisibleCopyCause | None:
    if _unsafe_unicode(text):
        return VisibleCopyCause.UNSAFE_UNICODE
    if _structured_copy(text):
        return VisibleCopyCause.STRUCTURED
    if _sensitive_copy(text):
        return VisibleCopyCause.SENSITIVE
    if is_technical_surface_copy(item, identity, text):
        return VisibleCopyCause.TECHNICAL_IDENTIFIER
    diagnostic = _diagnostic_form(text)
    diagnostic_words = tuple(diagnostic.split())
    if diagnostic in _DIAGNOSTIC_ONLY or (
        len(diagnostic_words) >= 2
        and all(
            word in _DIAGNOSTIC_WORDS or word.isdecimal() for word in diagnostic_words
        )
    ):
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
    text = value.strip()
    if not text:
        return VisibleCopyResult(False, None, VisibleCopyCause.EMPTY)
    if len(text) > MAX_VISIBLE_COPY_SCAN_LENGTH:
        return VisibleCopyResult(False, None, VisibleCopyCause.TOO_LONG)
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
