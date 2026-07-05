from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services._copilot_helpers import coerce_uuid_or_none


def user_id(user: dict[str, Any]) -> int:
    uid = user.get("id") or user.get("user_id")
    if uid is None:
        raise HTTPException(401, "session has no user id")
    try:
        return int(uid)
    except (TypeError, ValueError) as exc:
        raise HTTPException(401, "invalid user id in session") from exc


def workspace_id(user: dict[str, Any]) -> str | None:
    ws = user.get("active_workspace_id") or user.get("workspace_id")
    if ws is None or ws == "":
        return None
    return str(ws)


def tenant_id(user: dict[str, Any]) -> str | None:
    tenant = user.get("active_tenant_id") or user.get("tenant_id")
    if tenant is None or tenant == "":
        return None
    return str(tenant)


def require_uuid_path(value: str, *, label: str) -> str:
    coerced = coerce_uuid_or_none(value)
    if coerced is None:
        raise HTTPException(400, f"{label} must be a valid UUID")
    return coerced
