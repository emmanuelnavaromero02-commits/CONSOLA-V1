from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException


GLOBAL_VAULT_SCOPES = {"global", "platform", "studio", "system", "_system"}


def tenant_vault_prefix(
    user: dict,
    *,
    is_global_admin: Callable[[dict | None], bool],
) -> str | None:
    if is_global_admin(user):
        return None
    tenant_id = str(user.get("active_tenant_id") or user.get("tenant_id") or "").strip()
    workspace_id = str(
        user.get("active_workspace_id") or user.get("workspace_id") or ""
    ).strip()
    if not tenant_id or not workspace_id:
        raise HTTPException(400, "active tenant/workspace is required for vault access")
    return f"tenant_{tenant_id}__workspace_{workspace_id}__"


def tenant_vault_conn_id(
    user: dict,
    conn_id: str,
    *,
    is_global_admin: Callable[[dict | None], bool],
) -> str:
    clean = (conn_id or "").strip()
    if not clean:
        raise HTTPException(400, "connection id is required")
    tenant_vault_prefix(user, is_global_admin=is_global_admin)
    return clean


def tenant_vault_display_conn(
    user: dict,
    conn: dict,
    *,
    is_global_admin: Callable[[dict | None], bool],
) -> dict | None:
    prefix = tenant_vault_prefix(user, is_global_admin=is_global_admin)
    if prefix is None:
        return conn
    key = str(conn.get("conn_id") or conn.get("id") or conn.get("key") or "")
    if key.startswith(prefix):
        display_key = key[len(prefix) :]
    elif key.startswith("tenant_") and "__workspace_" in key:
        return None
    else:
        display_key = key
    display = {**conn, "conn_id": display_key}
    if "id" in display:
        display["id"] = display["conn_id"]
    return display


def tenant_vault_scope(
    user: dict,
    scope: str,
    *,
    is_global_admin: Callable[[dict | None], bool],
) -> str:
    clean = (scope or "").strip()
    if not clean:
        raise HTTPException(400, "vault scope is required")
    if is_global_admin(user):
        return clean
    if clean in GLOBAL_VAULT_SCOPES:
        raise HTTPException(403, "global vault scope requires platform admin")
    tenant_vault_prefix(user, is_global_admin=is_global_admin)
    return clean


def require_vault_scope_visible(
    user: dict,
    scope: str,
    *,
    is_global_admin: Callable[[dict | None], bool],
) -> None:
    clean = (scope or "").strip()
    if clean in GLOBAL_VAULT_SCOPES:
        if not is_global_admin(user):
            raise HTTPException(403, "global vault scope requires platform admin")
        return
    if is_global_admin(user):
        return
