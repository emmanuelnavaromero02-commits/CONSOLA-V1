from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
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
