from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any


_SIGNATURE_FIELD = "_signature"
_SIGNED_AT_FIELD = "_signed_at"
_SIGNATURE_VERSION_FIELD = "_signature_version"
_SIGNATURE_VERSION = "hmac-sha256-v1"
_MIN_SIGNING_KEY_LEN = 32


def _transport_keys() -> dict[str, str]:
    return {
        name: value.strip()
        for name, value in os.environ.items()
        if (name == "INTERNAL_API_KEY" or name.startswith("INTERNAL_API_KEY_"))
        and isinstance(value, str)
        and value.strip()
    }


def _signing_key() -> str:
    key = (os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < _MIN_SIGNING_KEY_LEN:
        raise RuntimeError("SECURITY_CONTEXT_SIGNING_KEY is required to sign security_context")
    for env_name, transport_key in _transport_keys().items():
        if hmac.compare_digest(key, transport_key):
            raise RuntimeError(f"SECURITY_CONTEXT_SIGNING_KEY must be distinct from {env_name}")
    return key


def _canonical_context(ctx: dict[str, Any]) -> bytes:
    payload = {key: value for key, value in ctx.items() if key != _SIGNATURE_FIELD}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_security_context(ctx: dict[str, Any]) -> dict[str, Any]:
    if not ctx.get("trusted"):
        return dict(ctx)
    signed = dict(ctx)
    signed[_SIGNED_AT_FIELD] = int(time.time())
    signed[_SIGNATURE_VERSION_FIELD] = _SIGNATURE_VERSION
    signed[_SIGNATURE_FIELD] = hmac.new(
        _signing_key().encode("utf-8"),
        _canonical_context(signed),
        hashlib.sha256,
    ).hexdigest()
    return signed
