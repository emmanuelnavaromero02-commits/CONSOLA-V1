"""
Session reader for the workspace container.

Workspace doesn't own login — that lives in the console. This module just reads
the shared `user_sessions` table (Postgres) so cookies set by console are
recognized here as well. Cookies are scoped by domain only (not port), so on
localhost (and behind any reverse proxy in prod) the cookie flows naturally.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import asyncpg

COOKIE_NAME      = "mod_session"
SESSION_LIFETIME = timedelta(days=7)
SESSION_SLIDE    = timedelta(days=1)

_POOL: asyncpg.Pool | None = None


def _is_production_env() -> bool:
    return os.environ.get("APP_ENV", os.environ.get("ENVIRONMENT", "production")).strip().lower() in {
        "production",
        "prod",
        "staging",
        "stage",
    }


async def pool() -> asyncpg.Pool:
    global _POOL
    if _POOL is None:
        dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
        _POOL = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    return _POOL


async def _workspace_memberships(p: asyncpg.Pool, user_id: int) -> list[dict]:
    rows = await p.fetch(
        """SELECT w.id::text AS workspace_id,
                  w.name AS workspace_name,
                  t.id::text AS tenant_id,
                  t.name AS tenant_name,
                  r.name AS workspace_role
             FROM user_workspace_roles uwr
             JOIN workspaces w ON w.id = uwr.workspace_id
             JOIN tenants t ON t.id = w.tenant_id
             JOIN roles r ON r.id = uwr.role_id
            WHERE uwr.user_id = $1
            ORDER BY w.created_at ASC, w.name ASC, r.name ASC""",
        user_id,
    )
    return [dict(row) for row in rows]


async def _workspace_cartridges(p: asyncpg.Pool, workspace_id: str | None, user_id: int | None = None) -> list[str]:
    if not workspace_id:
        return []
    has_entitlements = await p.fetchval("SELECT to_regclass('public.tenant_entitlements')")
    if has_entitlements:
        has_user_overrides = await p.fetchval("SELECT to_regclass('public.user_cartridge_overrides')")
        deny_filter = ""
        args: tuple = (workspace_id,)
        if has_user_overrides and user_id is not None:
            deny_filter = """
                  AND NOT EXISTS (
                    SELECT 1
                      FROM user_cartridge_overrides uco
                     WHERE uco.tenant_id = te.tenant_id
                       AND uco.workspace_id = te.workspace_id
                       AND uco.cartridge_id = te.cartridge_id
                       AND uco.user_id = $2
                       AND uco.mode = 'deny'
                  )
            """
            args = (workspace_id, user_id)
        rows = await p.fetch(
            f"""SELECT cartridge_id AS cartridge
                 FROM tenant_entitlements te
                WHERE te.workspace_id = $1
                  AND te.status = 'active'
                  AND (te.ends_at IS NULL OR te.ends_at > NOW())
                  AND EXISTS (
                    SELECT 1
                      FROM cartridge_installations ci
                     WHERE ci.tenant_id = te.tenant_id
                       AND ci.workspace_id = te.workspace_id
                       AND ci.cartridge_id = te.cartridge_id
                       AND ci.status = 'ready'
                  )
                  {deny_filter}
                ORDER BY cartridge""",
            *args,
        )
    else:
        if _is_production_env():
            return []
        rows = await p.fetch(
            """SELECT DISTINCT cartridge
                 FROM datasets
                WHERE workspace_id = $1
                  AND cartridge IS NOT NULL
                  AND cartridge <> ''
                ORDER BY cartridge""",
            workspace_id,
        )
    return [str(row["cartridge"]) for row in rows if row["cartridge"]]


async def get_session_user(token: str, requested_workspace_id: str | None = None) -> dict | None:
    if not token:
        return None
    p = await pool()
    row = await p.fetchrow(
        """SELECT s.token, s.expires_at,
                  u.id, u.email, u.name, u.role, u.is_active, u.must_change_password
             FROM user_sessions s
             JOIN users u ON u.id = s.user_id
            WHERE s.token = $1 AND s.expires_at > NOW() AND u.is_active = TRUE""",
        token,
    )
    if not row:
        return None
    # Sliding window
    new_exp = datetime.now(timezone.utc) + SESSION_LIFETIME
    if (row["expires_at"] - datetime.now(timezone.utc)) < (SESSION_LIFETIME - SESSION_SLIDE):
        await p.execute("UPDATE user_sessions SET expires_at = $1 WHERE token = $2",
                        new_exp, token)
    user = {
        "id":                   row["id"],
        "email":                row["email"],
        "name":                 row["name"],
        "role":                 row["role"],
        "is_active":            row["is_active"],
        "must_change_password": row["must_change_password"],
    }
    workspaces = await _workspace_memberships(p, row["id"])
    if workspaces:
        active = workspaces[0]
        if requested_workspace_id:
            active = next((w for w in workspaces if w["workspace_id"] == requested_workspace_id), None)
            if not active:
                raise PermissionError("workspace access forbidden")
        user.update({
            "active_workspace_id": active["workspace_id"],
            "active_tenant_id": active["tenant_id"],
            "workspace_role": active["workspace_role"],
            "workspaces": workspaces,
            "allowed_cartridges": await _workspace_cartridges(p, active["workspace_id"], user_id=row["id"]),
        })
    return user


async def destroy_session(token: str) -> None:
    if not token:
        return
    p = await pool()
    await p.execute("DELETE FROM user_sessions WHERE token = $1", token)
