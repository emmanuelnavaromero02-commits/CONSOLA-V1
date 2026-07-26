from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from math import isfinite

from app.services.control_room.business_copy_unicode import security_detection_forms
from app.services.control_room.diagnostic_redaction_keys import (
    sensitive_diagnostic_field,
)

_SENSITIVE_VALUE = re.compile(
    r"(?ix)"
    r"(\b(?:tokens?|passwords?|secrets?|api(?:[_ -])?keys?|authorization|"
    r"cookies?|client[_-]?secrets?|access[_-]?keys?|"
    r"(?:oauth[_-]?)?access[_-]?tokens?|refresh[_-]?tokens?|"
    r"session[_-]?tokens?|connection[_-]?strings?|database[_-]?url|dsn|"
    r"credentials?|secret[_-]?(?:strings?|binar(?:y|ies)|values?)|"
    r"(?:client[_-]?)?private[_-]?keys?)"
    r"\b[\"']?\s*(?::|=|\bis\b)\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\n,;&}]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_URI_CREDENTIALS = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s:]*:[^/@\s]+@")
_PRIVATE_KEY_BLOCK = re.compile(
    r"(?is)-----BEGIN[^\n-]*PRIVATE KEY-----.*?" r"-----END[^\n-]*PRIVATE KEY-----"
)
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)\+?\d(?:[\s().-]*\d){8,14}(?!\w)")
_CURP = re.compile(r"(?i)\b[A-Z][AEIOU][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b")
_RFC = re.compile(r"(?i)\b[A-Z&]{3,4}\d{6}[A-Z0-9]{3}\b")
_ADDRESS = re.compile(
    r"(?i)\b(address|direccion|domicilio)\s*(?::|=|\bis\b)\s*[^,;\n]+"
)
_KEY_VALUE = re.compile(
    r"(?ix)"
    r"(?P<key>(?<![A-Z0-9])[A-Z][A-Z0-9_. \[\]-]{0,79}?)"
    r"(?P<separator>\s*(?::|=|\bis\b)\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\n,;&}]+)"
)


def _redact_key_value(match: re.Match[str]) -> str:
    if not sensitive_diagnostic_field(match.group("key")):
        return match.group(0)
    return f"{match.group('key')}{match.group('separator')}[REDACTED]"


def _redact_text(value: str) -> str:
    clean = _PRIVATE_KEY_BLOCK.sub("[REDACTED]", value)
    clean = _KEY_VALUE.sub(_redact_key_value, clean)
    clean = _BEARER.sub("Bearer [REDACTED]", clean)
    clean = _SENSITIVE_VALUE.sub(r"\1[REDACTED]", clean)
    clean = _URI_CREDENTIALS.sub(r"\1[REDACTED]@", clean)
    clean = _EMAIL.sub("[REDACTED]", clean)
    clean = _PHONE.sub("[REDACTED]", clean)
    clean = _CURP.sub("[REDACTED]", clean)
    clean = _RFC.sub("[REDACTED]", clean)
    return _ADDRESS.sub(r"\1=[REDACTED]", clean)


def redact_diagnostic_value(value: object, *, field: str = "") -> object:
    if sensitive_diagnostic_field(field):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                continue
            redacted[key] = redact_diagnostic_value(nested, field=key)
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [redact_diagnostic_value(nested) for nested in value]
    if isinstance(value, float) and not isfinite(value):
        return None
    if not isinstance(value, str):
        return value
    clean = _redact_text(value)
    if clean != value:
        return clean
    if any(
        candidate != value and _redact_text(candidate) != candidate
        for candidate in security_detection_forms(value)
    ):
        return "[REDACTED]"
    return value


__all__ = ("redact_diagnostic_value",)
