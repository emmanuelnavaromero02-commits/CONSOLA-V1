from __future__ import annotations

from typing import Any


DEFAULT_STUDIO_OPS_WRITE_ROLES = {"owner", "super_admin", "admin"}


def cartridge_visible_for_context(ctx: dict[str, Any], cartridge_id: str) -> bool:
    allowed = {
        str(cartridge).strip()
        for cartridge in (ctx.get("allowed_cartridges") or [])
        if str(cartridge).strip()
    }
    if "*" in allowed:
        return True
    return str(cartridge_id) in allowed


def studio_ops_role_name(user: dict[str, Any] | None) -> str:
    if not user:
        return ""
    return str(user.get("workspace_role") or user.get("role") or "")


def has_studio_ops_write_role(
    user: dict[str, Any] | None,
    *,
    write_roles: set[str] | None = None,
) -> bool:
    roles = write_roles or DEFAULT_STUDIO_OPS_WRITE_ROLES
    role = str((user or {}).get("role") or "").lower()
    return role in roles
