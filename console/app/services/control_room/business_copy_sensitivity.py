from __future__ import annotations

import base64
import json
import re
import unicodedata
from collections.abc import Mapping

from app.services.control_room.diagnostic_redaction import redact_diagnostic_value


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
_URI_PASSWORD = re.compile(
    r"(?i)(?<![A-Z0-9+.-])[A-Z][A-Z0-9+.-]*://[^/@\s:]*:[^/@\s]+@"
)


def _security_skeleton(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.category(character).startswith("M")
    )


def _detection_forms(value: str) -> tuple[str, ...]:
    forms = (
        value,
        unicodedata.normalize("NFKC", value),
        _security_skeleton(value),
    )
    return tuple(dict.fromkeys(forms))


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


def contains_sensitive_copy(value: str) -> bool:
    for candidate in _detection_forms(value):
        if (
            redact_diagnostic_value(candidate) != candidate
            or _RECOGNIZABLE_SECRET.search(candidate)
            or _contains_jwt(candidate)
            or _UNICODE_EMAIL.search(candidate)
            or _URI_PASSWORD.search(candidate)
        ):
            return True
    return False


__all__ = ("contains_sensitive_copy",)
