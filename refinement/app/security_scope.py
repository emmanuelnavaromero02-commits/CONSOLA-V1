from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_ADMIN_ROLES = {"admin", "owner", "super_admin"}
# Carried on engine user contexts built from a verified security context, because the
# engine never sees allowed_cartridges.
UNSCOPED_ADMIN_FLAG = "_unscoped_admin"


def _is_admin_security_context(sec: Mapping[str, Any]) -> bool:
    return (
        bool(sec.get("trusted")) and str(sec.get("role") or "").lower() in _ADMIN_ROLES
    )


def _is_unscoped_admin_security_context(sec: Mapping[str, Any]) -> bool:
    if not _is_admin_security_context(sec):
        return False
    if sec.get("tenant_id") or sec.get("workspace_id"):
        return False
    allowed = {
        str(item).strip()
        for item in (sec.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    return "*" in allowed


def is_unscoped_admin_user_context(user_context: Mapping[str, Any] | None) -> bool:
    if not isinstance(user_context, Mapping):
        return False
    return (
        user_context.get("_server_trusted_context") is True
        and user_context.get(UNSCOPED_ADMIN_FLAG) is True
        and str(user_context.get("role") or "").lower() in _ADMIN_ROLES
        and not user_context.get("tenant_id")
        and not user_context.get("workspace_id")
    )
