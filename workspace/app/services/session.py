"""
Token-bound session reader for the workspace container.

Workspace doesn't own login or auth tables. It can only execute the narrow
``omega_auth_resolve_workspace_session`` / ``omega_auth_destroy_session``
functions, passing a SHA-256 digest of the cookie. Raw tokens are never stored.
"""
from __future__ import annotations

import os
import hashlib
from datetime import timedelta

import asyncpg

COOKIE_NAME      = "mod_session"
SESSION_LIFETIME = timedelta(days=7)
SESSION_SLIDE    = timedelta(days=1)
MAX_SESSION_LIFETIME = timedelta(hours=12)

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


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _set_db_scope(conn: asyncpg.Connection, tenant_id: str | None, workspace_id: str | None) -> None:
    if tenant_id and workspace_id and hasattr(conn, "execute"):
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
            tenant_id,
            workspace_id,
        )


async def _workspace_cartridges(
    p: asyncpg.Pool,
    workspace_id: str | None,
    user_id: int | None = None,
    tenant_id: str | None = None,
) -> list[str]:
    if not workspace_id:
        return []
    if not hasattr(p, "acquire"):
        await _set_db_scope(p, tenant_id, workspace_id)
        return await _workspace_cartridges_for_conn(p, workspace_id, user_id)
    async with p.acquire() as conn:
        if not hasattr(conn, "transaction"):
            await _set_db_scope(conn, tenant_id, workspace_id)
            return await _workspace_cartridges_for_conn(conn, workspace_id, user_id)
        async with conn.transaction():
            await _set_db_scope(conn, tenant_id, workspace_id)
            return await _workspace_cartridges_for_conn(conn, workspace_id, user_id)


async def _workspace_cartridges_for_conn(conn, workspace_id: str, user_id: int | None) -> list[str]:
    has_entitlements = await conn.fetchval("SELECT to_regclass('public.tenant_entitlements')")
    if has_entitlements:
        has_user_overrides = await conn.fetchval("SELECT to_regclass('public.user_cartridge_overrides')")
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
        rows = await conn.fetch(
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
        rows = await conn.fetch(
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
    query = """SELECT * FROM omega_auth_resolve_workspace_session(
                   $1, $2::uuid
               )"""
    args = (
        hash_session_token(token),
        requested_workspace_id,
    )
    rows = await p.fetch(query, *args)
    if not rows and requested_workspace_id:
        # Distinguish an invalid session (401) from a valid caller asking for a
        # workspace outside its memberships (403). The second lookup remains
        # token-bound and returns no data for an expired/revoked session.
        membership_rows = await p.fetch(query, args[0], None)
        if membership_rows:
            raise PermissionError("workspace access forbidden")
    if not rows:
        return None
    row = rows[0]
    user = {
        "id":                   row["user_id"],
        "email":                row["email"],
        "name":                 row["name"],
        "role":                 row["role"],
        "is_active":            row["is_active"],
        "must_change_password": row["must_change_password"],
    }
    workspaces = [
        {
            "workspace_id": str(item["workspace_id"]),
            "workspace_name": item["workspace_name"],
            "tenant_id": str(item["tenant_id"]),
            "tenant_name": item["tenant_name"],
            "workspace_role": item["workspace_role"],
        }
        for item in rows
    ]
    active = workspaces[0]
    user.update({
        "active_workspace_id": active["workspace_id"],
        "active_tenant_id": active["tenant_id"],
        "workspace_role": active["workspace_role"],
        "workspaces": workspaces,
        "allowed_cartridges": await _workspace_cartridges(
            p,
            active["workspace_id"],
            user_id=row["user_id"],
            tenant_id=active["tenant_id"],
        ),
    })
    return user


async def destroy_session(token: str) -> None:
    if not token:
        return
    p = await pool()
    await p.fetchval(
        "SELECT omega_auth_destroy_session($1)", hash_session_token(token)
    )
