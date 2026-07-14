from __future__ import annotations

import re
from typing import Any

from app.logging_config import _redact_value


_OPAQUE_ID_RE = re.compile(
    r"^(?:(?:mc|cal-state|cal-obs)-[a-f0-9]{32}|orch-[a-f0-9]{64})$",
    re.IGNORECASE,
)
_HASH_RE = re.compile(r"^[a-f0-9]{32,128}$", re.IGNORECASE)
_PUBLIC_HASH_KEYS = {
    "checksum_sha256",
    "payload_hash",
    "reproducibility_hash",
    "request_hash",
}


def redact_response_value(value: Any, key: str | None = None) -> Any:
    """Redact secrets without corrupting public OMEGA IDs and hashes."""
    if isinstance(value, dict):
        return {item_key: redact_response_value(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_response_value(item) for item in value)
    if not isinstance(value, str):
        return value

    redacted = _redact_value(value, key)
    if redacted == "***REDACTED***":
        return redacted
    if _OPAQUE_ID_RE.fullmatch(value):
        return value
    if key in _PUBLIC_HASH_KEYS and _HASH_RE.fullmatch(value):
        return value
    return redacted
