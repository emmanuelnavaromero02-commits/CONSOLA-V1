from __future__ import annotations

import hmac
import hashlib
import json
import os
import time
from typing import Any

from app.services import permissions


ADMIN_ROLES = {"admin", "owner", "super_admin"}
_SIGNATURE_FIELD = "_signature"
_SIGNED_AT_FIELD = "_signed_at"
_SIGNATURE_VERSION_FIELD = "_signature_version"
_SIGNATURE_VERSION = "hmac-sha256-v1"
_MIN_SIGNING_KEY_LEN = 32
_SIGNATURE_TTL_SECONDS = 300
_SIGNATURE_FUTURE_SKEW_SECONDS = 30


def _runtime_env() -> str:
    return (os.environ.get("APP_ENV") or os.environ.get("ENV") or "production").strip().lower()


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
    signed = dict(ctx)
    signed[_SIGNED_AT_FIELD] = int(time.time())
    signed[_SIGNATURE_VERSION_FIELD] = _SIGNATURE_VERSION
    key = _signing_key()
    if key:
        signed[_SIGNATURE_FIELD] = hmac.new(
            key.encode("utf-8"),
            _canonical_context(signed),
            hashlib.sha256,
        ).hexdigest()
    return signed


def verify_signed_security_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Validate a trusted security_context signed by Console.

    Internal callers may omit a security context for legacy unscoped flows, but
    once a payload declares ``trusted=true`` it must be signed, fresh, and
    transport-key distinct in every environment.
    """
    if not isinstance(ctx, dict):
        raise ValueError("security_context must be an object")
    if not ctx.get("trusted"):
        return dict(ctx)
    signature = str(ctx.get(_SIGNATURE_FIELD) or "")
    if not signature:
        raise ValueError("security_context signature is required")
    if ctx.get(_SIGNATURE_VERSION_FIELD) != _SIGNATURE_VERSION:
        raise ValueError("unsupported security_context signature version")
    try:
        signed_at = int(ctx.get(_SIGNED_AT_FIELD))
    except (TypeError, ValueError) as exc:
        raise ValueError("security_context signed_at is required") from exc
    now = int(time.time())
    if signed_at > now + _SIGNATURE_FUTURE_SKEW_SECONDS:
        raise ValueError("security_context signature is from the future")
    if now - signed_at > int(os.environ.get("SECURITY_CONTEXT_SIGNATURE_TTL_SECONDS", _SIGNATURE_TTL_SECONDS)):
        raise ValueError("security_context signature expired")
    key = _signing_key()
    expected = hmac.new(
        key.encode("utf-8"),
        _canonical_context(ctx),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("security_context signature mismatch")
    return dict(ctx)


def build_security_context(user: dict | None) -> dict[str, Any]:
    """Build the server-owned context forwarded to internal MCP services.

    This payload is intentionally derived from the authenticated backend user,
    never from LLM/tool arguments. Downstream services should treat request
    body ``user_context`` fields as untrusted unless this context accompanies
    the internal request.
    """
    if not user:
        return {
            "trusted": False,
            "permissions": [],
            "role": "anonymous",
            "allowed_cartridges": [],
            "allowed_buckets": [],
            "allowed_prefixes": [],
        }

    role = permissions.user_role(user)
    workspace_role = permissions.workspace_role(user)
    effective = permissions.get_effective_permissions(user)
    explicit_cartridges = user.get("allowed_cartridges") if "allowed_cartridges" in user else user.get("cartridges")
    explicit_scope = explicit_cartridges is not None
    has_workspace_scope = bool(user.get("active_workspace_id") or user.get("workspace_id"))
    if explicit_scope:
        allowed_cartridges = list(explicit_cartridges or [])
    elif role in ADMIN_ROLES and not has_workspace_scope and ("datasets.read" in effective or "cartridges.read" in effective):
        allowed_cartridges = ["*"]
    elif has_workspace_scope and (
        "vault.connections.read" in effective
        or "vault.connections.write" in effective
        or "cartridges.read" in effective
    ):
        allowed_cartridges = ["*"]
    else:
        allowed_cartridges = []

    return sign_security_context({
        "trusted": True,
        "source": "console",
        "user_id": user.get("id"),
        "email": user.get("email", ""),
        "role": role,
        "workspace_role": workspace_role,
        "tenant_id": user.get("active_tenant_id") or user.get("tenant_id"),
        "workspace_id": user.get("active_workspace_id") or user.get("workspace_id"),
        "project_id": user.get("active_project_id") or user.get("project_id"),
        "permissions": sorted(effective),
        "allowed_cartridges": allowed_cartridges,
        "allowed_buckets": _allowed_buckets(),
        "allowed_prefixes": _allowed_prefixes(
            allowed_cartridges,
            role,
            tenant_id=user.get("active_tenant_id") or user.get("tenant_id"),
            workspace_id=user.get("active_workspace_id") or user.get("workspace_id"),
        ),
    })


def _allowed_buckets() -> list[str]:
    """Buckets the Console may authorize downstream data readers to touch.

    Local MinIO historically uses ``lakehouse``. AWS deployments use the real
    S3 bucket name via ``S3_BUCKET_NAME``/``MINIO_BUCKET`` while still reading
    through the same Refinement guards. Include only configured bucket names,
    never wildcard buckets.
    """
    buckets: list[str] = []
    for candidate in (
        "lakehouse",
        os.environ.get("MINIO_BUCKET"),
        os.environ.get("S3_BUCKET_NAME"),
    ):
        value = str(candidate or "").strip()
        if value and value not in buckets:
            buckets.append(value)
    return buckets


def rls_user_context(user: dict | None) -> dict[str, Any]:
    """RLS context for refinement, derived from the same server-owned source."""
    ctx = build_security_context(user)
    return {
        "id": ctx.get("user_id"),
        "email": ctx.get("email", ""),
        "role": ctx.get("role"),
        "tenant_id": ctx.get("tenant_id"),
        "workspace_id": ctx.get("workspace_id"),
        "project_id": ctx.get("project_id"),
        "workspace_role": ctx.get("workspace_role"),
        "_server_trusted_context": bool(ctx.get("trusted")),
    }


def _allowed_prefixes(
    cartridges: list[str],
    role: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> list[str]:
    scoped = bool(tenant_id and workspace_id)
    if "*" in cartridges and not scoped:
        return ["raw/", "silver/", "gold/", "uploads/", "cartridges/", "inbound/", "inbound-processed/"]
    prefixes: list[str] = []
    for cart in cartridges:
        c = str(cart).strip().strip("/")
        if not c:
            continue
        if scoped:
            scope = f"tenant_id={tenant_id}/workspace_id={workspace_id}/"
            prefixes.extend([
                f"raw/{c}/{scope}",
                f"silver/{c}/{scope}",
                f"gold/{c}/{scope}",
                f"uploads/{c}/{scope}",
                f"cartridges/{c}/",
            ])
        else:
            prefixes.extend([
                f"raw/{c}/",
                f"silver/{c}/",
                f"gold/{c}/",
                f"uploads/{c}/",
                f"cartridges/{c}/",
            ])
    return prefixes
