from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from contextvars import ContextVar, Token
from typing import Any


CARTRIDGE_ID = "replicon"
SERVICE_SOURCE = "cartridge-replicon"
_SAFE_SCOPE_SEGMENT = re.compile(r"[A-Za-z0-9_.:-]+")
_SIGNATURE_FIELD = "_signature"
_SIGNED_AT_FIELD = "_signed_at"
_SIGNATURE_VERSION_FIELD = "_signature_version"
_SIGNATURE_VERSION = "hmac-sha256-v1"
_MIN_SIGNING_KEY_LEN = 32
_SIGNATURE_TTL_SECONDS = 300
_SIGNATURE_FUTURE_SKEW_SECONDS = 30
_CURRENT_SECURITY_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "replicon_security_context",
    default=None,
)


class SecurityContextError(ValueError):
    """Raised when an inbound trusted security_context cannot be verified."""


def set_security_context(
    ctx: dict[str, Any] | None,
    *,
    expected_tenant_id: str | None = None,
    expected_workspace_id: str | None = None,
) -> Token:
    verified = verify_security_context(
        ctx,
        expected_tenant_id=expected_tenant_id,
        expected_workspace_id=expected_workspace_id,
    )
    return _CURRENT_SECURITY_CONTEXT.set(verified)


def reset_security_context(token: Token) -> None:
    _CURRENT_SECURITY_CONTEXT.reset(token)


def get_security_context() -> dict[str, Any] | None:
    return _CURRENT_SECURITY_CONTEXT.get()


def _safe_segment(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not _SAFE_SCOPE_SEGMENT.fullmatch(text):
        return ""
    return text


def scope_values(ctx: dict[str, Any] | None = None) -> tuple[str, str]:
    ctx = ctx if ctx is not None else get_security_context()
    if not isinstance(ctx, dict) or not ctx.get("trusted"):
        return "", ""
    tenant = _safe_segment(ctx.get("tenant_id"))
    workspace = _safe_segment(ctx.get("workspace_id"))
    if not (tenant and workspace):
        return "", ""
    return tenant, workspace


def scoped_prefix(ctx: dict[str, Any] | None = None) -> str:
    tenant, workspace = scope_values(ctx)
    if not (tenant and workspace):
        return ""
    return f"tenant_id={tenant}/workspace_id={workspace}/"


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


def _sign_security_context(ctx: dict[str, Any]) -> dict[str, Any]:
    signed = dict(ctx)
    signed[_SIGNED_AT_FIELD] = int(time.time())
    signed[_SIGNATURE_VERSION_FIELD] = _SIGNATURE_VERSION
    signed[_SIGNATURE_FIELD] = hmac.new(
        _signing_key().encode("utf-8"),
        _canonical_context(signed),
        hashlib.sha256,
    ).hexdigest()
    return signed


def _signature_ttl_seconds() -> int:
    raw = (os.environ.get("SECURITY_CONTEXT_SIGNATURE_TTL_SECONDS") or "").strip()
    if not raw:
        return _SIGNATURE_TTL_SECONDS
    try:
        ttl = int(raw)
    except ValueError:
        return _SIGNATURE_TTL_SECONDS
    return ttl if ttl > 0 else _SIGNATURE_TTL_SECONDS


def verify_security_context(
    ctx: dict[str, Any] | None,
    expected_tenant_id: str | None = None,
    expected_workspace_id: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(ctx, dict) or not ctx.get("trusted"):
        return None
    if ctx.get(_SIGNATURE_VERSION_FIELD) != _SIGNATURE_VERSION:
        raise SecurityContextError("security_context signature version is not supported")
    signature = ctx.get(_SIGNATURE_FIELD)
    if not isinstance(signature, str) or not signature:
        raise SecurityContextError("security_context signature is required")
    try:
        signed_at = int(ctx.get(_SIGNED_AT_FIELD))
    except (TypeError, ValueError) as exc:
        raise SecurityContextError("security_context signed_at is invalid") from exc
    now = int(time.time())
    if signed_at > now + _SIGNATURE_FUTURE_SKEW_SECONDS:
        raise SecurityContextError("security_context signature is from the future")
    if now - signed_at > _signature_ttl_seconds():
        raise SecurityContextError("security_context signature has expired")
    expected_signature = hmac.new(
        _signing_key().encode("utf-8"),
        _canonical_context(ctx),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise SecurityContextError("security_context signature is invalid")
    if expected_tenant_id is not None and str(ctx.get("tenant_id") or "") != str(expected_tenant_id):
        raise SecurityContextError("security_context tenant mismatch")
    if expected_workspace_id is not None and str(ctx.get("workspace_id") or "") != str(expected_workspace_id):
        raise SecurityContextError("security_context workspace mismatch")
    return dict(ctx)


def refinement_security_context(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = ctx if ctx is not None else get_security_context()
    tenant, workspace = scope_values(ctx)
    if tenant and workspace:
        scope = f"tenant_id={tenant}/workspace_id={workspace}/"
        allowed_prefixes = [
            f"raw/{CARTRIDGE_ID}/{scope}",
            f"silver/{CARTRIDGE_ID}/{scope}",
            f"gold/{CARTRIDGE_ID}/{scope}",
        ]
    else:
        allowed_prefixes = [
            f"raw/{CARTRIDGE_ID}/",
            f"silver/{CARTRIDGE_ID}/",
            f"gold/{CARTRIDGE_ID}/",
        ]
    base: dict[str, Any] = {
        "trusted": True,
        "source": SERVICE_SOURCE,
        "role": "admin",
        "permissions": ["datasets.read", "datasets.write"],
        "allowed_buckets": ["lakehouse"],
        "allowed_cartridges": [CARTRIDGE_ID],
        "allowed_prefixes": allowed_prefixes,
    }
    if isinstance(ctx, dict):
        base["request_user_id"] = ctx.get("user_id") or ctx.get("request_user_id")
    if tenant and workspace:
        base["tenant_id"] = tenant
        base["workspace_id"] = workspace
    return _sign_security_context(base)
