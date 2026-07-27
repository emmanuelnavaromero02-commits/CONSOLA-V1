from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping

from app.services.control_room.business_copy_unicode import security_detection_forms
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
    r"|\bsk-(?:proj-)?[A-Z0-9_-]{20,255}\b"
    r"|\b(?:sk|rk)_(?:live|test)_[A-Z0-9]{16,255}\b"
    r"|\bglpat-[A-Z0-9_-]{20,255}\b"
    r"|\bxox[baprs]-[A-Z0-9-]{10,255}\b"
    r"|\bnpm_[A-Z0-9]{20,255}\b"
    r"|\bpypi-[A-Z0-9_-]{20,255}\b"
    r"|https://hooks\.slack(?:-gov)?\.com/services/"
    r"[A-Z0-9_-]+/[A-Z0-9_-]+/[A-Z0-9_-]+"
    r"|https://(?:canary\.)?discord(?:app)?\.com/api/webhooks/"
    r"[0-9]+/[A-Z0-9._-]+"
    r"|[?&#;](?:sig|signature|x-amz-signature|x-goog-signature)="
    r"[A-Z0-9%+/_=-]+"
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
_BASIC_AUTH = re.compile(r"(?i)\bBasic\s+([A-Za-z0-9+/]{8,}={0,2})(?![A-Za-z0-9+/=])")


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


def _contains_basic_auth(value: str) -> bool:
    for match in _BASIC_AUTH.finditer(value):
        token = match.group(1)
        try:
            decoded = base64.b64decode(token, validate=True)
        except ValueError:
            continue
        if b":" in decoded:
            return True
    return False


def contains_sensitive_copy(value: str) -> bool:
    for candidate in security_detection_forms(value):
        if (
            redact_diagnostic_value(candidate) != candidate
            or _RECOGNIZABLE_SECRET.search(candidate)
            or _contains_jwt(candidate)
            or _contains_basic_auth(candidate)
            or _UNICODE_EMAIL.search(candidate)
            or _URI_PASSWORD.search(candidate)
        ):
            return True
    return False


__all__ = ("contains_sensitive_copy",)
