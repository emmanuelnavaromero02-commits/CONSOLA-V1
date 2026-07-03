from __future__ import annotations

from typing import Any


COPILOT_ADMIN_ROLE_ALLOWLIST = frozenset({
    "owner",
    "super_admin",
    "admin",
    "workspace_admin",
})


def has_admin(user: dict[str, Any]) -> bool:
    role = str(user.get("role") or "").lower()
    if role in COPILOT_ADMIN_ROLE_ALLOWLIST:
        return True
    try:
        from app.services import permissions as _perms

        return "iam.users.write" in _perms.get_effective_permissions(user)
    except Exception:
        return False
