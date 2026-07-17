from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException


WORKSPACE_WIDE_ROLES = frozenset({"admin", "owner", "super_admin"})
WORKSPACE_WIDE_SCOPED_ROLES = frozenset({"tenant_admin", "workspace_admin"})


def actor_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def can_read_workspace_wide(user: Mapping[str, Any] | None) -> bool:
    payload = user or {}
    role = str(payload.get("role") or "").strip()
    scoped = str(
        payload.get("workspace_role") or payload.get("platform_role") or ""
    ).strip()
    return role in WORKSPACE_WIDE_ROLES or scoped in WORKSPACE_WIDE_SCOPED_ROLES


def owner_scope_id(user: Mapping[str, Any] | None) -> int | None:
    if can_read_workspace_wide(user):
        return None
    return actor_id((user or {}).get("id"))


def expected_item_owner(
    item: Mapping[str, Any], user: Mapping[str, Any] | None
) -> int | None:
    if "owner_user_id" in item:
        return actor_id(item.get("owner_user_id"))
    return actor_id((user or {}).get("id"))


def owner_projection(
    item: Mapping[str, Any], persisted: Mapping[str, Any]
) -> dict[str, int]:
    persisted_owner = actor_id(persisted.get("owner_user_id"))
    if persisted_owner is not None:
        return {"owner_user_id": persisted_owner}
    item_owner = actor_id(item.get("owner_user_id"))
    return {"owner_user_id": item_owner} if item_owner is not None else {}


def workspace_scope(user: Mapping[str, Any] | None) -> tuple[str | None, str]:
    payload = user or {}
    workspace_id = payload.get("active_workspace_id") or payload.get("workspace_id")
    tenant_id = payload.get("active_tenant_id") or payload.get("tenant_id")
    if not workspace_id:
        raise HTTPException(400, "active workspace is required")
    return (str(tenant_id) if tenant_id else None), str(workspace_id)


__all__ = (
    "actor_id",
    "can_read_workspace_wide",
    "expected_item_owner",
    "owner_projection",
    "owner_scope_id",
    "workspace_scope",
)
