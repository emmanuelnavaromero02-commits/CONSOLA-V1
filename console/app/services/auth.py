"""
Local authentication & session management for the console.

- Passwords hashed with bcrypt.
- Sessions are server-side (random token in user_sessions table) + HttpOnly cookie.
- Sessions slide on each authenticated request (renewed by SESSION_SLIDE_DAYS).
"""
from __future__ import annotations

import os
import secrets
import hashlib
from datetime import datetime, timedelta, timezone

import asyncpg
import bcrypt
from fastapi import Header, HTTPException

from app.security import get_internal_api_key

COOKIE_NAME      = "mod_session"
REFRESH_COOKIE_NAME = "refresh_token"
SESSION_LIFETIME = timedelta(days=7)
SESSION_SLIDE    = timedelta(days=1)   # extend if older than this
REFRESH_TOKEN_LIFETIME = timedelta(days=7)

_POOL: asyncpg.Pool | None = None


async def pool() -> asyncpg.Pool:
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


# ── Password hashing ────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:                       # invited but not yet activated → cannot log in
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── User CRUD ───────────────────────────────────────────────────────────────

async def create_user(email: str, password: str, name: str | None = None,
                      role: str = "user") -> dict:
    """Direct create with password — used by bootstrap_admin and admin override."""
    p = await pool()
    row = await p.fetchrow(
        """INSERT INTO users (email, name, password_hash, role, is_active)
           VALUES ($1, $2, $3, $4, TRUE)
           RETURNING id, email, name, role, is_active, must_change_password, created_at""",
        email.lower().strip(), name, hash_password(password), role,
    )
    return _user_to_dict(row)


async def create_invited_user(email: str, name: str | None = None, role: str = "user") -> dict:
    """Create a user without a password (must_change_password is moot here —
    the activation flow sets the password). is_active stays FALSE until the
    invitee clicks the email link and chooses a password."""
    p = await pool()
    row = await p.fetchrow(
        """INSERT INTO users (email, name, password_hash, role, is_active, must_change_password)
           VALUES ($1, $2, NULL, $3, FALSE, FALSE)
           RETURNING id, email, name, role, is_active, must_change_password, created_at""",
        email.lower().strip(), name, role,
    )
    return _user_to_dict(row)


async def activate_user(user_id: int, new_password: str) -> dict | None:
    """Mark user active and set their password (called from /auth/activate)."""
    if not new_password or len(new_password) < 8:
        return None
    p = await pool()
    row = await p.fetchrow(
        """UPDATE users
              SET password_hash = $1, is_active = TRUE,
                  must_change_password = FALSE, last_login = NOW()
            WHERE id = $2
            RETURNING id, email, name, role, is_active""",
        hash_password(new_password), user_id,
    )
    return dict(row) if row else None


async def reset_password_to(user_id: int, new_password: str) -> dict | None:
    """Token-based password reset — sets a new password and clears the
    must_change_password flag (the user just chose this one)."""
    if not new_password or len(new_password) < 8:
        return None
    p = await pool()
    row = await p.fetchrow(
        """UPDATE users
              SET password_hash = $1, must_change_password = FALSE
            WHERE id = $2 AND is_active = TRUE
            RETURNING id, email, name, role, is_active""",
        hash_password(new_password), user_id,
    )
    return dict(row) if row else None


async def _get_user_auth_record_by_email(email: str) -> dict | None:
    p = await pool()
    row = await p.fetchrow(
        "SELECT id, email, name, password_hash, role, is_active, must_change_password "
        "FROM users WHERE email = $1",
        email.lower().strip(),
    )
    return dict(row) if row else None


async def get_user_by_email(email: str) -> dict | None:
    return _user_to_dict(await _get_user_auth_record_by_email(email))


async def get_user_by_id(user_id: int) -> dict | None:
    p = await pool()
    row = await p.fetchrow(
        "SELECT id, email, name, role, is_active, must_change_password, created_at, last_login "
        "FROM users WHERE id = $1",
        user_id,
    )
    return _user_to_dict(row) if row else None


async def list_users(active_only: bool = True) -> list[dict]:
    p = await pool()
    sql = ("SELECT id, email, name, role, is_active, must_change_password, "
           "created_at, last_login FROM users")
    if active_only:
        sql += " WHERE is_active = TRUE"
    sql += " ORDER BY email"
    rows = await p.fetch(sql)
    return [_user_to_dict(r) for r in rows]


async def update_user(user_id: int, *, name: str | None = None, role: str | None = None,
                      is_active: bool | None = None, password: str | None = None) -> dict | None:
    """Admin update. If password is provided, force the user to change it on next login."""
    sets, params = [], []
    if name is not None:      params.append(name);      sets.append(f"name = ${len(params)}")
    if role is not None:      params.append(role);      sets.append(f"role = ${len(params)}")
    if is_active is not None: params.append(is_active); sets.append(f"is_active = ${len(params)}")
    if password is not None:
        params.append(hash_password(password))
        sets.append(f"password_hash = ${len(params)}")
        sets.append("must_change_password = TRUE")
    if not sets:
        return await get_user_by_id(user_id)
    params.append(user_id)
    p = await pool()
    row = await p.fetchrow(
        f"UPDATE users SET {', '.join(sets)} WHERE id = ${len(params)} "
        f"RETURNING id, email, name, role, is_active, must_change_password, created_at, last_login",
        *params,
    )
    return _user_to_dict(row) if row else None


async def change_own_password(user_id: int, current_password: str,
                              new_password: str) -> tuple[bool, str | None]:
    """Self-service password change. Returns (success, error_msg)."""
    if not new_password or len(new_password) < 8:
        return False, "el password debe tener al menos 8 caracteres"
    if current_password == new_password:
        return False, "el nuevo password debe ser distinto al actual"
    p = await pool()
    row = await p.fetchrow(
        "SELECT password_hash FROM users WHERE id = $1 AND is_active = TRUE",
        user_id,
    )
    if not row:
        return False, "usuario no encontrado"
    if not verify_password(current_password, row["password_hash"]):
        return False, "el password actual es incorrecto"
    await p.execute(
        "UPDATE users SET password_hash = $1, must_change_password = FALSE WHERE id = $2",
        hash_password(new_password), user_id,
    )
    return True, None


async def delete_user(user_id: int) -> bool:
    p = await pool()
    res = await p.execute("DELETE FROM users WHERE id = $1", user_id)
    return res != "DELETE 0"


# ── Authentication ──────────────────────────────────────────────────────────

async def authenticate(email: str, password: str, ip: str | None = None) -> dict | None:
    """Returns user dict (without password_hash) on success, else None."""
    p = await pool()
    normalized_email = email.lower().strip()

    # Check for brute force (5 failures in 15 minutes)
    recent_failures = await p.fetchval(
        """SELECT COUNT(*) FROM login_attempts
           WHERE email = $1 AND success = FALSE
           AND created_at >= NOW() - INTERVAL '15 minutes'""",
        normalized_email
    )
    if recent_failures >= 5:
        raise HTTPException(status_code=429, detail="Cuenta bloqueada temporalmente")

    u = await _get_user_auth_record_by_email(email)

    if not u or not u.get("is_active"):
        await p.execute(
            "INSERT INTO login_attempts (email, ip, success) VALUES ($1, $2, FALSE)",
            normalized_email, ip
        )
        return None

    if not verify_password(password, u["password_hash"]):
        await p.execute(
            "INSERT INTO login_attempts (email, ip, success) VALUES ($1, $2, FALSE)",
            normalized_email, ip
        )
        return None

    # Success
    await p.execute(
        "INSERT INTO login_attempts (email, ip, success) VALUES ($1, $2, TRUE)",
        normalized_email, ip
    )
    await p.execute("UPDATE users SET last_login = NOW() WHERE id = $1", u["id"])
    return {k: v for k, v in u.items() if k != "password_hash"}


# ── Session management ─────────────────────────────────────────────────────

async def create_session(user_id: int, ip: str | None = None) -> tuple[str, datetime]:
    token   = secrets.token_hex(32)
    expires = datetime.now(timezone.utc) + SESSION_LIFETIME
    p = await pool()
    await p.execute(
        "INSERT INTO user_sessions (token, user_id, expires_at, ip) VALUES ($1, $2, $3, $4)",
        token, user_id, expires, ip,
    )
    return token, expires


async def get_session_user(token: str) -> dict | None:
    """Return user dict if session is valid; slides expiration if close to expiring."""
    if not token:
        return None
    p = await pool()
    row = await p.fetchrow(
        """SELECT s.token, s.user_id, s.expires_at,
                  u.id, u.email, u.name, u.role, u.is_active, u.must_change_password
             FROM user_sessions s
             JOIN users u ON u.id = s.user_id
            WHERE s.token = $1 AND s.expires_at > NOW() AND u.is_active = TRUE""",
        token,
    )
    if not row:
        return None
    # Sliding window: if older than SESSION_SLIDE remaining, push expiry forward
    new_exp = datetime.now(timezone.utc) + SESSION_LIFETIME
    if (row["expires_at"] - datetime.now(timezone.utc)) < (SESSION_LIFETIME - SESSION_SLIDE):
        await p.execute("UPDATE user_sessions SET expires_at = $1 WHERE token = $2",
                        new_exp, token)
    return {
        "id":                   row["user_id"],
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


async def cleanup_expired_sessions() -> int:
    p = await pool()
    res = await p.execute("DELETE FROM user_sessions WHERE expires_at < NOW()")
    try:
        return int(res.split()[-1])
    except Exception:
        return 0


# ── Refresh token management ────────────────────────────────────────────────

def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_refresh_token(user_id: int) -> tuple[str, datetime]:
    token = generate_refresh_token()
    expires = datetime.now(timezone.utc) + REFRESH_TOKEN_LIFETIME
    p = await pool()
    await p.execute(
        "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES ($1, $2, $3)",
        user_id, hash_refresh_token(token), expires,
    )
    return token, expires


async def get_refresh_token_user(token: str) -> dict | None:
    if not token:
        return None
    p = await pool()
    row = await p.fetchrow(
        """SELECT rt.id AS refresh_token_id, rt.user_id, rt.expires_at,
                  u.id, u.email, u.name, u.role, u.is_active, u.must_change_password
             FROM refresh_tokens rt
             JOIN users u ON u.id = rt.user_id
            WHERE rt.token_hash = $1
              AND rt.revoked_at IS NULL
              AND rt.expires_at > NOW()
              AND u.is_active = TRUE""",
        hash_refresh_token(token),
    )
    if not row:
        return None
    return {
        "id":                   row["user_id"],
        "email":                row["email"],
        "name":                 row["name"],
        "role":                 row["role"],
        "is_active":            row["is_active"],
        "must_change_password": row["must_change_password"],
    }


async def revoke_refresh_token(token: str) -> None:
    if not token:
        return
    p = await pool()
    await p.execute(
        "UPDATE refresh_tokens SET revoked_at = NOW() WHERE token_hash = $1 AND revoked_at IS NULL",
        hash_refresh_token(token),
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _user_to_dict(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in ("created_at", "last_login"):
        if d.get(k):
            d[k] = d[k].isoformat()
    d.pop("password_hash", None)
    return d


def cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "false").lower() == "true"


def verify_internal_api_key(
    x_api_key: str | None = Header(None),
    x_internal_service: str | None = Header(None),
) -> None:
    if not x_internal_service or x_internal_service not in {"console", "workspace", "refinement", "mcp-infra", "airflow"}:
        raise HTTPException(status_code=403, detail="Invalid internal service origin")
    if not x_api_key or not secrets.compare_digest(x_api_key, get_internal_api_key()):
        raise HTTPException(status_code=403, detail="Forbidden")
