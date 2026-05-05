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


async def pool() -> asyncpg.Pool:
    global _POOL
    if _POOL is None:
        dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
        _POOL = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    return _POOL


async def get_session_user(token: str) -> dict | None:
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
    return {
        "id":                   row["id"],
        "email":                row["email"],
        "name":                 row["name"],
        "role":                 row["role"],
        "is_active":            row["is_active"],
        "must_change_password": row["must_change_password"],
    }


async def destroy_session(token: str) -> None:
    if not token:
        return
    p = await pool()
    await p.execute("DELETE FROM user_sessions WHERE token = $1", token)
