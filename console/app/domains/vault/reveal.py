from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import HTTPException, Request


REVEAL_PATH_RE = re.compile(r"/api/vault/connections/([^/]+)/[^/]+/reveal")


def cartridge_from_reveal_path(path: str) -> str | None:
    match = REVEAL_PATH_RE.fullmatch(path)
    return match.group(1) if match else None


def is_cartridge_vault_reveal_request(
    request: Request,
    *,
    reveal_keys: Mapping[str, Mapping[str, tuple[str, ...]]],
    environ: Mapping[str, str],
    is_production_env: Callable[[], bool],
    internal_api_key: str,
) -> bool:
    if request.method != "GET":
        return False
    cartridge = cartridge_from_reveal_path(request.url.path)
    if not cartridge:
        return False
    service_keys = reveal_keys.get(cartridge)
    if not service_keys:
        return False

    service = (request.headers.get("x-internal-service") or "").strip().lower()
    key_envs = service_keys.get(service)
    if not key_envs:
        return False
    supplied = (
        request.headers.get("x-api-key")
        or request.headers.get("x-internal-api-key")
        or ""
    )
    if not supplied:
        return False

    accepted = [environ.get(env, "") for env in key_envs]
    if not is_production_env():
        accepted.append(internal_api_key)
    return any(secrets.compare_digest(str(supplied), key) for key in accepted if key)


def cartridge_vault_reveal_user(
    request: Request,
    *,
    reveal_keys: Mapping[str, Mapping[str, tuple[str, ...]]],
    environ: Mapping[str, str],
    is_production_env: Callable[[], bool],
    internal_api_key: str,
    internal_service_user: Callable[[], dict],
    verify_signed_security_context: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict | None:
    if not is_cartridge_vault_reveal_request(
        request,
        reveal_keys=reveal_keys,
        environ=environ,
        is_production_env=is_production_env,
        internal_api_key=internal_api_key,
    ):
        return None

    header = (request.headers.get("x-security-context") or "").strip()
    if not header:
        raise HTTPException(403, "signed security context required")
    try:
        raw_ctx = json.loads(header)
        if not isinstance(raw_ctx, dict):
            raise ValueError("security_context must be an object")
        ctx = verify_signed_security_context(raw_ctx)
    except Exception as exc:
        raise HTTPException(403, "invalid signed security context") from exc
    if ctx.get("trusted") is not True or ctx.get("source") != "console":
        raise HTTPException(403, "invalid signed security context")

    cartridge = cartridge_from_reveal_path(request.url.path) or ""
    allowed = {
        str(c).strip() for c in (ctx.get("allowed_cartridges") or []) if str(c).strip()
    }
    if "*" not in allowed and cartridge not in allowed:
        raise HTTPException(403, "cartridge not allowed")

    user = internal_service_user()
    tenant_id = str(ctx.get("tenant_id") or "").strip() or None
    workspace_id = str(ctx.get("workspace_id") or "").strip() or None
    user.update(
        {
            "active_tenant_id": tenant_id,
            "tenant_id": tenant_id,
            "active_workspace_id": workspace_id,
            "workspace_id": workspace_id,
            "active_project_id": ctx.get("project_id"),
            "project_id": ctx.get("project_id"),
            "allowed_cartridges": sorted(allowed) if allowed else [cartridge],
            "security_context_actor_id": ctx.get("user_id"),
            "security_context_actor_email": ctx.get("email"),
            "_service_scoped_context": bool(tenant_id and workspace_id),
        }
    )
    return user
