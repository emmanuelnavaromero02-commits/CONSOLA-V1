from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import os
from typing import Any

from fastapi import HTTPException


INTERNAL_SERVICE_ID = 0
INTERNAL_SERVICE_EMAIL = "internal@omega.local"


def internal_service_user(*, role_admin: str) -> dict[str, Any]:
    return {
        "id": INTERNAL_SERVICE_ID,
        "email": INTERNAL_SERVICE_EMAIL,
        "role": role_admin,
        "workspace_role": None,
        "active_tenant_id": None,
        "active_workspace_id": None,
    }


def user_payload(
    user: Mapping[str, Any] | None,
    *,
    get_effective_permissions: Callable[[Mapping[str, Any]], Iterable[str]],
) -> dict[str, Any] | None:
    if not user:
        return None
    payload = dict(user)
    payload["permissions"] = sorted(get_effective_permissions(payload))
    return payload


def is_internal_service_actor(user: Mapping[str, Any] | None) -> bool:
    if not user:
        return False
    return (
        int(user.get("id") or -1) == INTERNAL_SERVICE_ID
        and user.get("email") == INTERNAL_SERVICE_EMAIL
    )


def require_effective_permission(
    user: Mapping[str, Any] | None,
    permission: str,
    *,
    has_permission: Callable[[Mapping[str, Any] | None, str], bool],
) -> None:
    if not has_permission(user, permission):
        raise HTTPException(
            status_code=403, detail=f"permission required: {permission}"
        )


def internal_outbound_key(
    server: str,
    *,
    internal_api_key: str | None,
    is_production: bool,
    environ: Mapping[str, str] | None = None,
) -> str:
    env = os.environ if environ is None else environ
    pair = env.get(f"INTERNAL_API_KEY_CONSOLE_TO_{server}")
    if pair:
        return pair
    if is_production:
        raise RuntimeError(
            f"Missing INTERNAL_API_KEY_CONSOLE_TO_{server}; legacy fallback disabled in production"
        )
    if internal_api_key:
        return internal_api_key
    raise RuntimeError(
        f"Missing INTERNAL_API_KEY_CONSOLE_TO_{server} (no legacy fallback either)"
    )


def internal_outbound_headers(
    server: str,
    *,
    internal_api_key: str | None,
    is_production: bool,
    request_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "x-api-key": internal_outbound_key(
            server,
            internal_api_key=internal_api_key,
            is_production=is_production,
            environ=environ,
        ),
        "x-internal-service": "console",
    }
    if request_id:
        headers["x-request-id"] = request_id
    return headers


def internal_cartridge_headers(
    *,
    cartridge_api_key: str | None,
    internal_api_key: str | None,
    is_production: bool,
) -> dict[str, str]:
    if not cartridge_api_key and is_production:
        raise RuntimeError(
            "Missing INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE; legacy fallback disabled in production"
        )
    return {
        "x-api-key": cartridge_api_key or internal_api_key or "",
        "x-internal-service": "console",
    }
