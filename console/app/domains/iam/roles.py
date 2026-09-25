from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

GLOBAL_ASSIGNABLE_ROLES = {
    "owner",
    "super_admin",
    "admin",
    "security_admin",
    "auditor",
}

WORKSPACE_ASSIGNABLE_ROLES = {
    "tenant_admin",
    "analyst",
    "viewer",
    "workspace_user",
    "user",
}


def is_global_iam_admin(user: Mapping[str, Any] | None, *, role_admin: str) -> bool:
    return (user or {}).get("role") in {"owner", "super_admin", role_admin}


_ROLE_RANK = {
    "owner": 4,
    "super_admin": 3,
    "admin": 2,
    "security_admin": 1,
    "auditor": 1,
}


def _role_rank(role: str | None) -> int:
    return _ROLE_RANK.get(str(role or "").strip(), 0)


def session_workspace_ids(user: Mapping[str, Any] | None) -> set[str]:
    return {
        str(workspace.get("workspace_id"))
        for workspace in ((user or {}).get("workspaces") or [])
        if workspace.get("workspace_id")
    }


_IAM_ADMIN_WORKSPACE_ROLES = {
    "owner",
    "super_admin",
    "admin",
    "tenant_admin",
    "security_admin",
    "workspace_admin",
}


def iam_admin_workspace_ids(user: Mapping[str, Any] | None) -> set[str]:
    return {
        str(workspace.get("workspace_id"))
        for workspace in ((user or {}).get("workspaces") or [])
        if workspace.get("workspace_id")
        and str(workspace.get("workspace_role") or "").strip().lower()
        in _IAM_ADMIN_WORKSPACE_ROLES
    }


def workspace_scope_db_unavailable(exc: BaseException) -> bool:
    message = str(exc)
    asyncpg_module = sys.modules.get("asyncpg")
    return (
        isinstance(exc, RuntimeError) and "DATABASE_URL is not configured" in message
    ) or (
        isinstance(exc, AttributeError)
        and "create_pool" in message
        and getattr(asyncpg_module, "__name__", "") == "stub"
    )


def assignable_role(
    value: str | None,
    actor_user: Mapping[str, Any] | None,
    *,
    role_definitions: Mapping[str, Mapping[str, Any]],
    role_admin: str,
    global_assignable_roles: set[str] = GLOBAL_ASSIGNABLE_ROLES,
    workspace_assignable_roles: set[str] = WORKSPACE_ASSIGNABLE_ROLES,
) -> str:
    role = (value or "user").strip()
    info = role_definitions.get(role)
    if not info or not info.get("assignable"):
        return "user"
    if is_global_iam_admin(actor_user, role_admin=role_admin):
        if _role_rank(role) > _role_rank((actor_user or {}).get("role")):
            raise HTTPException(
                403, "cannot assign a role higher than your own"
            )
        return role
    if role in global_assignable_roles:
        raise HTTPException(403, "global role assignment requires platform admin")
    return role if role in workspace_assignable_roles else "user"

