"""Single-use email tokens (invite | reset | vpn)."""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg

INVITE_TTL = timedelta(hours=int(os.environ.get("INVITE_TOKEN_TTL_HOURS", "72")))
RESET_TTL  = timedelta(hours=int(os.environ.get("RESET_TOKEN_TTL_HOURS",  "1")))
VPN_TTL    = timedelta(hours=int(os.environ.get("VPN_TOKEN_TTL_HOURS",   "72")))


_POOL: asyncpg.Pool | None = None


async def _pool() -> asyncpg.Pool:
    global _POOL
    if _POOL is None:
        dsn = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
        _POOL = await asyncpg.create_pool(dsn, min_size=1, max_size=4, command_timeout=10)
    return _POOL


async def close_pool() -> None:
    global _POOL
    if _POOL is not None:
        await _POOL.close()
        _POOL = None


def _ttl_for(kind: str) -> timedelta:
    if kind == "invite":
        return INVITE_TTL
    if kind == "vpn":
        return VPN_TTL
    return RESET_TTL


async def create(user_id: int, kind: str, wg_client_id: str | None = None) -> tuple[str, datetime]:
    """Generate and persist a single-use token. Returns (token, expires_at)."""
    if kind not in ("invite", "reset", "vpn"):
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
    if kind == "vpn":
        await p.execute(
            "INSERT INTO user_tokens (token, user_id, kind, expires_at, wg_client_id) "
            "VALUES ($1, $2, $3, $4, $5)",
            token, user_id, kind, expires, wg_client_id,
        )
    else:
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
        """SELECT t.user_id, t.expires_at, t.wg_client_id,
                  u.email, u.name, u.role, u.is_active
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


async def consume_lookup(db, token: str | None = None, kind: str | None = None) -> dict | None:
    """Atomically validate and consume a single-use token.

    Supports both the current call shape ``consume_lookup(token, kind)`` and
    the explicit test/service shape ``consume_lookup(db, token, kind)``.
    """
    if kind is None:
        actual_token = str(db or "")
        actual_kind = str(token or "")
        executor = await _pool()
    else:
        actual_token = str(token or "")
        actual_kind = str(kind or "")
        executor = db if db is not None else await _pool()

    if not actual_token or actual_kind not in ("invite", "reset", "vpn"):
        return None

    row = await executor.fetchrow(
        """UPDATE user_tokens AS t
              SET used_at = NOW()
             FROM users AS u
            WHERE t.user_id = u.id
              AND t.token = $1
              AND t.kind = $2
              AND t.used_at IS NULL
              AND t.expires_at > NOW()
        RETURNING t.user_id, t.expires_at, t.wg_client_id,
                  u.email, u.name, u.role, u.is_active""",
        actual_token,
        actual_kind,
    )
    return dict(row) if row else None


async def cleanup_expired() -> int:
    p = await _pool()
    res = await p.execute("DELETE FROM user_tokens WHERE expires_at < NOW() - INTERVAL '7 days'")
    try:    return int(res.split()[-1])
    except Exception: return 0
