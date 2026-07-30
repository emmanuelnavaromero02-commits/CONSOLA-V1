from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

from fastapi import HTTPException, Request

from app.services.permission_catalog import (
    PERMISSIONS,
    PERMISSION_KEYS,
    RESOURCE_ACTION_PERMISSIONS,
)
from app.services.permission_roles import ROLE_DEFINITIONS, ROLE_PERMISSIONS


def canonical_role(role: str | None) -> str:
    value = (role or "user").strip() or "user"
    return value if value in ROLE_DEFINITIONS else "__unknown__"


def user_role(user: dict | None) -> str:
    if not user:
        return "anonymous"
    return canonical_role(user.get("role"))


def workspace_role(user: dict | None) -> str | None:
    """Return the scoped workspace role, never a platform-admin role."""

    if not user or not user.get("workspace_role"):
        return None
    resolved = canonical_role(user.get("workspace_role"))
    if resolved in {"admin", "owner", "super_admin", "security_admin"}:
        return "workspace_admin"
    if resolved in ROLE_PERMISSIONS:
        return resolved
    return None


def get_effective_permissions(
    user: dict | None = None, role: str | None = None
) -> set[str]:
    if role:
        resolved = canonical_role(role)
        return set(ROLE_PERMISSIONS.get(resolved, set()))
    if not user:
        return set()
    roles = {user_role(user)}
    scoped = workspace_role(user)
    if scoped:
        roles.add(scoped)
    effective: set[str] = set()
    for resolved in roles:
        effective.update(ROLE_PERMISSIONS.get(resolved, set()))
    return effective


def has_permission(user: dict | None, permission: str) -> bool:
    if permission not in PERMISSION_KEYS:
        return False
    return permission in get_effective_permissions(user)


def require_permission(permission: str) -> Callable:
    async def dependency(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(status_code=401, detail="authentication required")
        if not has_permission(user, permission):
            raise HTTPException(
                status_code=403, detail=f"permission required: {permission}"
            )
        return user

    dependency.__name__ = f"require_permission_{permission.replace('.', '_')}"
    dependency.required_permission = permission  # type: ignore[attr-defined]
    return dependency


def require_any_permission(*permissions: str) -> Callable:
    async def dependency(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(status_code=401, detail="authentication required")
        effective = get_effective_permissions(user)
        if not any(permission in effective for permission in permissions):
            raise HTTPException(status_code=403, detail="required permission missing")
        return user

    return dependency


def roles_payload() -> list[dict]:
    result = []
    for name, info in ROLE_DEFINITIONS.items():
        item = deepcopy(info)
        item["name"] = name
        item["permissions"] = sorted(ROLE_PERMISSIONS.get(name, set()))
        result.append(item)
    return result


def matrix_payload() -> dict[str, dict[str, bool]]:
    return {
        role: {
            permission: permission in ROLE_PERMISSIONS.get(role, set())
            for permission in sorted(PERMISSION_KEYS)
        }
        for role in ROLE_DEFINITIONS
    }


def permission_for_resource(resource: str, action: str) -> str | None:
    if (resource, action) in RESOURCE_ACTION_PERMISSIONS:
        return RESOURCE_ACTION_PERMISSIONS[(resource, action)]
    if action == "read" and resource.startswith("/security/audit"):
        return "security.audit.read"
    if action == "read" and resource.startswith("/security/sessions"):
        return "security.sessions.read"
    if action == "read" and resource.startswith("/api/admin/users"):
        return "iam.users.read"
    if action in {"write", "create", "update", "delete"} and resource.startswith(
        "/api/admin/users"
    ):
        return "iam.users.write"
    if resource.startswith("/api/vault/connections"):
        return (
            "vault.connections.write"
            if action in {"write", "create", "update", "delete"}
            else "vault.connections.read"
        )
    if resource.startswith("/api/vault/secrets") and action == "reveal":
        return "vault.secrets.reveal"
    if resource.startswith("/api/vault/secrets"):
        return (
            "vault.secrets.read_masked"
            if action == "read"
            else "vault.connections.write"
        )
    return None


def access_check(role: str, resource: str, action: str) -> dict:
    resolved_role = canonical_role(role)
    permission = permission_for_resource(resource, action)
    allowed = bool(
        permission and permission in ROLE_PERMISSIONS.get(resolved_role, set())
    )
    return {
        "role": resolved_role,
        "resource": resource,
        "action": action,
        "allowed": allowed,
        "permission": permission,
        "source": "backend_permission_registry",
        "reason": (
            "No permission mapped for this resource/action."
            if not permission
            else f"{resolved_role} {'includes' if allowed else 'does not include'} {permission}."
        ),
    }
