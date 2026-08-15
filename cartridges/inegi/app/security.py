from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse

INSECURE_DEFAULTS = frozenset({"dev-secret-key", "changeme", "secret", ""})
ALLOWED_INTERNAL_SERVICES = {"console", "workspace", "refinement", "mcp-infra", "airflow"}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def get_internal_api_key() -> str:
    key = os.environ.get("INTERNAL_API_KEY", "")
    if not key or any(secrets.compare_digest(key, bad) for bad in INSECURE_DEFAULTS):
        raise RuntimeError("INTERNAL_API_KEY is not configured or uses insecure default")
    return key


def _accepted_keys(service: str | None) -> list[str]:
    keys: list[str] = []
    if service == "console":
        keys.append(os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE", ""))
    if service == "airflow":
        keys.append(os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE", ""))
    if not _is_production():
        keys.append(get_internal_api_key())
    return [key for key in keys if key]


def is_valid_internal_request(api_key: str | None, service: str | None) -> bool:
    if not service or service not in ALLOWED_INTERNAL_SERVICES:
        return False
    return bool(api_key and any(secrets.compare_digest(api_key, key) for key in _accepted_keys(service)))


class InternalApiKeyASGIGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in {"http", "websocket"}:
            headers = {
                key.decode("latin1").lower(): value.decode("latin1")
                for key, value in scope.get("headers", [])
            }
            if not is_valid_internal_request(headers.get("x-api-key"), headers.get("x-internal-service")):
                response = JSONResponse({"detail": "Missing or invalid X-Api-Key"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


SECURITY_CONTEXT_SIGNATURE_VERSION = "hmac-sha256-v1"
_SECURITY_CONTEXT_TTL_SECONDS = 300
_SECURITY_CONTEXT_FUTURE_SKEW_SECONDS = 30
_MIN_SIGNING_KEY_LENGTH = 32


class SecurityContextError(ValueError):
    """Raised when the transported signed security_context cannot be trusted."""


def _security_context_signing_key() -> str:
    key = (os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < _MIN_SIGNING_KEY_LENGTH:
        raise RuntimeError("SECURITY_CONTEXT_SIGNING_KEY is required to verify security_context")
    for name, value in os.environ.items():
        if name != "INTERNAL_API_KEY" and not name.startswith("INTERNAL_API_KEY_"):
            continue
        transport = str(value or "").strip()
        if transport and hmac.compare_digest(key, transport):
            raise RuntimeError(f"SECURITY_CONTEXT_SIGNING_KEY must be distinct from {name}")
    return key


def verify_security_context(raw_context: str | None) -> dict[str, Any]:
    """Verify the signed security_context transported via X-Security-Context."""
    if not raw_context or not str(raw_context).strip():
        raise SecurityContextError("signed security_context is required")
    try:
        ctx = json.loads(raw_context)
    except ValueError as exc:
        raise SecurityContextError("security_context must be valid JSON") from exc
    if not isinstance(ctx, dict) or ctx.get("trusted") is not True:
        raise SecurityContextError("trusted security_context is required")
    if ctx.get("_signature_version") != SECURITY_CONTEXT_SIGNATURE_VERSION:
        raise SecurityContextError("security_context signature version is not supported")
    signature = str(ctx.get("_signature") or "")
    if not signature:
        raise SecurityContextError("security_context signature is required")
    try:
        signed_at = int(ctx.get("_signed_at"))
    except (TypeError, ValueError) as exc:
        raise SecurityContextError("security_context signed_at is invalid") from exc
    now = int(time.time())
    if signed_at > now + _SECURITY_CONTEXT_FUTURE_SKEW_SECONDS:
        raise SecurityContextError("security_context signature is from the future")
    if now - signed_at > _SECURITY_CONTEXT_TTL_SECONDS:
        raise SecurityContextError("security_context signature has expired")
    unsigned = {key: value for key, value in ctx.items() if key != "_signature"}
    expected = hmac.new(
        _security_context_signing_key().encode("utf-8"),
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise SecurityContextError("security_context signature is invalid")
    return ctx


def resolve_signed_scope(
    raw_context: str | None,
    body: Mapping[str, Any] | None,
) -> tuple[str, str]:
    """Resolve tenant/workspace exclusively from the verified signed context.

    Body-supplied scope never routes data; if it disagrees with the signed
    context the request is rejected instead of honored.
    """
    ctx = verify_security_context(raw_context)
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    if not tenant_id or not workspace_id:
        raise SecurityContextError("security_context tenant/workspace scope is required")
    claims = body if isinstance(body, Mapping) else {}
    embedded = claims.get("security_context")
    sources: list[Mapping[str, Any]] = [claims]
    if isinstance(embedded, Mapping):
        sources.append(embedded)
    for source in sources:
        for field, expected_value in (("tenant_id", tenant_id), ("workspace_id", workspace_id)):
            supplied = str(source.get(field) or "").strip()
            if supplied and supplied != expected_value:
                raise SecurityContextError(f"body {field} does not match signed security_context")
    return tenant_id, workspace_id
