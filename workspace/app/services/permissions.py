from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request


_CONTROL_ROOM_WRITE_ROLES = frozenset(
    {"owner", "super_admin", "admin", "workspace_admin", "tenant_admin"}
)
_WORKSPACE_ADMIN_ALIASES = frozenset(
    {"admin", "owner", "super_admin", "security_admin"}
)


def _canonical_role(value: object) -> str:
    return str(value or "").strip().lower()


def _effective_roles(user: dict | None) -> set[str]:
    if not user:
        return set()
    roles = {_canonical_role(user.get("role"))}
    scoped = _canonical_role(user.get("workspace_role"))
    if scoped in _WORKSPACE_ADMIN_ALIASES:
        scoped = "workspace_admin"
    if scoped:
        roles.add(scoped)
    return roles


def has_permission(user: dict | None, permission: str) -> bool:

    if permission != "control_room.write":
        return False
    return bool(_effective_roles(user) & _CONTROL_ROOM_WRITE_ROLES)


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
