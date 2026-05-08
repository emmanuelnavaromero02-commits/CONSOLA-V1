"""Single-use email tokens (invite | reset)."""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg

INVITE_TTL = timedelta(hours=int(os.environ.get("INVITE_TOKEN_TTL_HOURS", "72")))
RESET_TTL  = timedelta(hours=int(os.environ.get("RESET_TOKEN_TTL_HOURS",  "1")))


_POOL: asyncpg.Pool | None = None


async def _pool() -> asyncpg.Pool:
    global _POOL
    if _POOL is None:
        dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
        _POOL = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    return _POOL


async def close_pool() -> None:
    global _POOL
    if _POOL is not None:
        await _POOL.close()
        _POOL = None


def _ttl_for(kind: str) -> timedelta:
    return INVITE_TTL if kind == "invite" else RESET_TTL


async def create(user_id: int, kind: str) -> tuple[str, datetime]:
    """Generate and persist a single-use token. Returns (token, expires_at)."""
    if kind not in ("invite", "reset"):
        raise ValueError(f"unknown token kind: {kind}")
    token   = secrets.token_hex(32)
    expires = datetime.now(timezone.utc) + _ttl_for(kind)
    p = await _pool()
    # Invalidate any prior unused tokens of the same kind for the user
    await p.execute(
        "UPDATE user_tokens SET used_at = NOW() "
        "WHERE user_id = $1 AND kind = $2 AND used_at IS NULL",
        user_id, kind,
    )
    await p.execute(
        "INSERT INTO user_tokens (token, user_id, kind, expires_at) VALUES ($1, $2, $3, $4)",
        token, user_id, kind, expires,
    )
    return token, expires


async def lookup(token: str, kind: str) -> dict | None:
    """Return user info if token is valid (exists, matches kind, not expired,
    not used). DOES NOT mark it used — call `consume()` once the action succeeds."""
    if not token:
        return None
    p = await _pool()
    row = await p.fetchrow(
        """SELECT t.user_id, t.expires_at, u.email, u.name, u.role, u.is_active
             FROM user_tokens t
             JOIN users u ON u.id = t.user_id
            WHERE t.token = $1 AND t.kind = $2
              AND t.used_at IS NULL
              AND t.expires_at > NOW()""",
        token, kind,
    )
    return dict(row) if row else None


async def consume(token: str) -> None:
    p = await _pool()
    await p.execute("UPDATE user_tokens SET used_at = NOW() WHERE token = $1", token)


async def cleanup_expired() -> int:
    p = await _pool()
    res = await p.execute("DELETE FROM user_tokens WHERE expires_at < NOW() - INTERVAL '7 days'")
    try:    return int(res.split()[-1])
    except Exception: return 0
