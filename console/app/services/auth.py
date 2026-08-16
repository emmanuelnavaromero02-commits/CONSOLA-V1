"""
Local authentication & session management for the console.

- Passwords hashed with bcrypt.
- Sessions are server-side (SHA-256 token digest in user_sessions) + HttpOnly cookie.
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

COOKIE_NAME = "mod_session"
REFRESH_COOKIE_NAME = "refresh_token"
SESSION_LIFETIME = timedelta(days=7)
SESSION_SLIDE = timedelta(days=1)  # extend if older than this
# Absolute cap on a session's age — overrides the sliding window so an
# attacker holding a stolen cookie cannot keep extending the session
# forever, and so password/role revocation eventually takes effect for
# users with long-running sessions. 12h matches the typical workday
# boundary the security team uses for similar caps elsewhere.
MAX_SESSION_LIFETIME = timedelta(hours=12)
REFRESH_TOKEN_LIFETIME = timedelta(days=7)

# Minimum password length enforced on every server-side setter
# (activate_user, change_own_password, reset_password). 12 chars matches
# NIST SP 800-63B 2024 guidance and lines up with the entropy bar most
# managed-password products use as default.
MIN_PASSWORD_LENGTH = 12

_POOL: asyncpg.Pool | None = None


def _login_attempt_lockout_disabled() -> bool:
    """Disable only the DB-backed lockout for local E2E runs.

    ``console.app.main`` already honors RATE_LIMIT_ENABLED=false for
    the Redis/IP limiter. ``login_attempts`` is a separate hardening
    mechanism: attempts must still be tracked for auditability, but the
    lockout check can be bypassed by the explicit local E2E switch so
    repeated full-suite runs do not poison the shared test account.
    """
    enabled_env = os.environ.get("RATE_LIMIT_ENABLED")
    if enabled_env is not None and enabled_env.strip().lower() in {
        "false",
        "0",
        "no",
        "off",
    }:
        return True
    return False


_ALLOWED_INTERNAL_SERVICES_TO_KEY_ENV: dict[str, str | None] = {
    "workspace": "INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE",
    "replicon": "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
    "cartridge-replicon": "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
    "hubspot": "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE",
    "cartridge-hubspot": "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE",
    "sap_hcm": "INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE",
    "cartridge-sap_hcm": "INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE",
    "sap_s4hana": "INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE",
    "cartridge-sap_s4hana": "INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE",
    "sap_successfactors": "INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE",
    "cartridge-sap_successfactors": "INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE",
    "salesforce": "INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE",
    "cartridge-salesforce": "INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE",
    "airflow": "INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE",
    "mcp-infra": "INTERNAL_API_KEY_MCP_INFRA_TO_CONSOLE",
    # The old whitelist allowed these too; kept via legacy key only outside prod.
    "console": None,
    "refinement": None,
}


def _is_production() -> bool:
    # v1.43.2 (Codex P1-2): default ``production`` — see security.py.
    return os.environ.get("APP_ENV", "production").lower() in {"production", "prod"}


def _require_pair_keys_in_production() -> None:
    if not _is_production():
        return
    required = sorted(
        {env for env in _ALLOWED_INTERNAL_SERVICES_TO_KEY_ENV.values() if env}
    )
    missing = [env for env in required if not os.environ.get(env)]
    if missing:
        raise RuntimeError(
            "Console production startup refused: missing per-pair internal API key(s): "
            + ", ".join(missing)
        )


_require_pair_keys_in_production()


async def pool() -> asyncpg.Pool:
    global _POOL
    if _POOL is None:
        dsn = os.environ.get("DATABASE_URL", "").replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        _POOL = await asyncpg.create_pool(
            dsn, min_size=1, max_size=4, command_timeout=10
        )
    return _POOL


async def close_pool() -> None:
    global _POOL
    if _POOL is not None:
        await _POOL.close()
        _POOL = None


# ── Password hashing ────────────────────────────────────────────────────────
#
# bcrypt silently truncates inputs at 72 bytes, so a long password manager
# entry like "a"*73 would collide with "a"*72. Pre-hashing with SHA-256 and
# encoding the digest in URL-safe base64 (44 ASCII chars, well under 72)
# keeps the entropy of the original password while staying inside bcrypt's
# bounds. Both hash and verify must use the same pre-hash, so existing
# stored hashes from the previous (truncating) implementation continue to
# verify only when the original password was ≤72 bytes — passwords longer
# than that were silently truncated before, and anyone affected can reset.

import base64 as _b64


def _bcrypt_input(plain: str) -> bytes:
    return _b64.urlsafe_b64encode(hashlib.sha256(plain.encode("utf-8")).digest())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_bcrypt_input(plain), bcrypt.gensalt(rounds=12)).decode(
        "utf-8"
    )


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:  # invited but not yet activated → cannot log in
        return False
    hashed_bytes = hashed.encode("utf-8")
    try:
        if bcrypt.checkpw(_bcrypt_input(plain), hashed_bytes):
            return True
        # Backward-compat: hashes written before the SHA-256 pre-hash was
        # introduced used the raw password bytes. Accept those once so
        # existing users can still log in; a successful login can re-hash
        # via the normal change-password flow.
        return bcrypt.checkpw(plain.encode("utf-8"), hashed_bytes)
    except Exception:
        return False


# ── User CRUD ───────────────────────────────────────────────────────────────

# Map non-canonical / legacy users.role values to one of the four workspace
# roles that exist in the `roles` table (admin / workspace_admin / analyst /
# viewer). Without this fallback a user created with role "user" or "owner"
# could not be granted membership: the SELECT against `roles` would miss and
# /api/me would 403 the user on first login.
_WORKSPACE_ROLE_FALLBACK = {
    "owner": "workspace_admin",
    "super_admin": "workspace_admin",
    "admin": "workspace_admin",
    "security_admin": "workspace_admin",
    "auditor": "analyst",
    "workspace_user": "viewer",
    "user": "viewer",
}


async def _resolve_workspace_role_id(conn, role: str) -> int | None:
    row = await conn.fetchrow("SELECT id FROM roles WHERE name = $1", role)
    if row:
        return row["id"]
    fallback = _WORKSPACE_ROLE_FALLBACK.get(role, "viewer")
    row = await conn.fetchrow("SELECT id FROM roles WHERE name = $1", fallback)
    return row["id"] if row else None


async def _assignment_workspace(
    conn,
    workspace_id: str | None = None,
    tenant_id: str | None = None,
):
    if workspace_id:
        workspace = await conn.fetchrow(
            "SELECT id, tenant_id FROM workspaces WHERE id = $1::uuid",
            workspace_id,
        )
    else:
        workspace = await conn.fetchrow(
            "SELECT id, tenant_id FROM workspaces ORDER BY created_at ASC, name ASC LIMIT 1"
        )
    if not workspace:
        raise RuntimeError("no workspaces configured; cannot assign user membership")
    if tenant_id and str(workspace["tenant_id"]) != str(tenant_id):
        raise RuntimeError("workspace does not belong to tenant")
    return workspace


async def _assign_default_workspace_role(
    conn,
    user_id: int,
    role: str,
    workspace_id: str | None = None,
    tenant_id: str | None = None,
) -> None:
    """Grant a freshly-created user membership in the target/default workspace.
    The dependency chain in dependencies._with_workspace_context refuses
    requests without at least one row in user_workspace_roles, so this must
    run in the same transaction as the INSERT into users."""
    workspace = await _assignment_workspace(conn, workspace_id, tenant_id)
    role_id = await _resolve_workspace_role_id(conn, role)
    if not role_id:
        raise RuntimeError(
            f"role not present in `roles` table and no fallback available: {role!r}"
        )
    await conn.execute(
        """INSERT INTO user_workspace_roles (user_id, workspace_id, role_id)
           VALUES ($1, $2, $3)
           ON CONFLICT DO NOTHING""",
        user_id,
        workspace["id"],
        role_id,
    )


async def create_user(
    email: str,
    password: str,
    name: str | None = None,
    role: str = "user",
    workspace_id: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    """Direct create with password — used by bootstrap_admin and admin override."""
    p = await pool()
    async with p.acquire() as conn:
        async with conn.transaction():
            workspace = await _assignment_workspace(conn, workspace_id, tenant_id)
            row = await conn.fetchrow(
                """INSERT INTO users (email, name, password_hash, role, is_active, must_change_password, tenant_id)
                   VALUES ($1, $2, $3, $4, TRUE, TRUE, $5)
                   RETURNING id, email, name, role, is_active, must_change_password, tenant_id, created_at""",
                email.lower().strip(),
                name,
                hash_password(password),
                role,
                workspace["tenant_id"],
            )
            await _assign_default_workspace_role(
                conn, row["id"], role, str(workspace["id"]), str(workspace["tenant_id"])
            )
    return _user_to_dict(row)


async def create_invited_user(
    email: str,
    name: str | None = None,
    role: str = "user",
    workspace_id: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    """Create a user without a password (must_change_password is moot here —
    the activation flow sets the password). is_active stays FALSE until the
    invitee clicks the email link and chooses a password."""
    p = await pool()
    async with p.acquire() as conn:
        async with conn.transaction():
            workspace = await _assignment_workspace(conn, workspace_id, tenant_id)
            row = await conn.fetchrow(
                """INSERT INTO users (email, name, password_hash, role, is_active, must_change_password, tenant_id)
                   VALUES ($1, $2, NULL, $3, FALSE, FALSE, $4)
                   RETURNING id, email, name, role, is_active, must_change_password, tenant_id, created_at""",
                email.lower().strip(),
                name,
                role,
                workspace["tenant_id"],
            )
            await _assign_default_workspace_role(
                conn, row["id"], role, str(workspace["id"]), str(workspace["tenant_id"])
            )
    return _user_to_dict(row)


async def activate_user(user_id: int, new_password: str) -> dict | None:
    """Mark user active and set their password (called from /auth/activate)."""
    if not new_password or len(new_password) < MIN_PASSWORD_LENGTH:
        return None
    p = await pool()
    row = await p.fetchrow(
        """UPDATE users
              SET password_hash = $1, is_active = TRUE,
                  must_change_password = FALSE, last_login = NOW()
            WHERE id = $2
            RETURNING id, email, name, role, is_active""",
        hash_password(new_password),
        user_id,
    )
    return dict(row) if row else None


async def reset_password_to(user_id: int, new_password: str) -> dict | None:
    """Token-based password reset — sets a new password and clears the
    must_change_password flag (the user just chose this one)."""
    if not new_password or len(new_password) < MIN_PASSWORD_LENGTH:
        return None
    p = await pool()
    row = await p.fetchrow(
        """UPDATE users
              SET password_hash = $1, must_change_password = FALSE
            WHERE id = $2 AND is_active = TRUE
            RETURNING id, email, name, role, is_active""",
        hash_password(new_password),
        user_id,
    )
    return dict(row) if row else None


async def _get_user_auth_record_by_email(email: str) -> dict | None:
    p = await pool()
    row = await p.fetchrow(
        "SELECT id, email, name, password_hash, role, is_active, must_change_password, tenant_id "
        "FROM users WHERE email = $1",
        email.lower().strip(),
    )
    return dict(row) if row else None


async def get_user_by_email(email: str) -> dict | None:
    return _user_to_dict(await _get_user_auth_record_by_email(email))


async def get_user_by_id(user_id: int) -> dict | None:
    p = await pool()
    row = await p.fetchrow(
        "SELECT id, email, name, role, is_active, must_change_password, "
        "tenant_id, created_at, last_login, escalation_notify "
        "FROM users WHERE id = $1",
        user_id,
    )
    return _user_to_dict(row) if row else None


async def list_users(active_only: bool = True) -> list[dict]:
    p = await pool()
    sql = (
        "SELECT id, email, name, role, is_active, must_change_password, "
        "tenant_id, created_at, last_login, escalation_notify FROM users"
    )
    if active_only:
        sql += " WHERE is_active = TRUE"
    sql += " ORDER BY email"
    rows = await p.fetch(sql)
    return [_user_to_dict(r) for r in rows]


async def update_user(
    user_id: int,
    *,
    name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    password: str | None = None,
    escalation_notify: bool | None = None,
) -> dict | None:
    """Admin update. If password is provided, force the user to change it on next login."""
    sets, params = [], []
    if name is not None:
        params.append(name)
        sets.append(f"name = ${len(params)}")
    if role is not None:
        params.append(role)
        sets.append(f"role = ${len(params)}")
    if is_active is not None:
        params.append(is_active)
        sets.append(f"is_active = ${len(params)}")
    if escalation_notify is not None:
        params.append(bool(escalation_notify))
        sets.append(f"escalation_notify = ${len(params)}")
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
        f"RETURNING id, email, name, role, is_active, must_change_password, "
        f"tenant_id, created_at, last_login, escalation_notify",
        *params,
    )
    return _user_to_dict(row) if row else None


async def change_own_password(
    user_id: int, current_password: str, new_password: str
) -> tuple[bool, str | None]:
    """Self-service password change. Returns (success, error_msg)."""
    if not new_password or len(new_password) < MIN_PASSWORD_LENGTH:
        return (
            False,
            f"el password debe tener al menos {MIN_PASSWORD_LENGTH} caracteres",
        )
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
        hash_password(new_password),
        user_id,
    )
    return True, None


async def delete_user(user_id: int) -> bool:
    p = await pool()
    async with p.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM users WHERE id = $1)", user_id
            )
            if not exists:
                return False
            await conn.execute(
                "DELETE FROM user_workspace_roles WHERE user_id = $1", user_id
            )
            await conn.fetchval("SELECT omega_auth_revoke_user_tokens($1)", user_id)
            try:
                res = await conn.execute("DELETE FROM users WHERE id = $1", user_id)
            except asyncpg.ForeignKeyViolationError as exc:
                raise RuntimeError(
                    "user has retained activity; deactivate the account instead of deleting it"
                ) from exc
    return res != "DELETE 0"


# ── Authentication ──────────────────────────────────────────────────────────


async def authenticate(email: str, password: str, ip: str | None = None) -> dict | None:
    """Returns user dict (without password_hash) on success, else None."""
    p = await pool()
    normalized_email = email.lower().strip()
    login_attempts_available = True
    lockout_disabled = _login_attempt_lockout_disabled()

    # Check for brute force (5 failures in 15 minutes)
    try:
        recent_failures = await p.fetchval(
            """SELECT COUNT(*) FROM login_attempts
               WHERE email = $1 AND success = FALSE
               AND created_at >= NOW() - INTERVAL '15 minutes'""",
            normalized_email,
        )
    except asyncpg.UndefinedTableError:
        login_attempts_available = False
        recent_failures = 0

    if not lockout_disabled and int(recent_failures or 0) >= 5:
        raise HTTPException(status_code=429, detail="Cuenta bloqueada temporalmente")

    u = await _get_user_auth_record_by_email(email)

    if not u or not u.get("is_active"):
        if login_attempts_available:
            await p.execute(
                "INSERT INTO login_attempts (email, ip, success) VALUES ($1, $2, FALSE)",
                normalized_email,
                ip,
            )
        return None

    if not verify_password(password, u["password_hash"]):
        if login_attempts_available:
            await p.execute(
                "INSERT INTO login_attempts (email, ip, success) VALUES ($1, $2, FALSE)",
                normalized_email,
                ip,
            )
        return None

    # Success
    if login_attempts_available:
        await p.execute(
            "INSERT INTO login_attempts (email, ip, success) VALUES ($1, $2, TRUE)",
            normalized_email,
            ip,
        )
    await p.execute("UPDATE users SET last_login = NOW() WHERE id = $1", u["id"])
    return _user_to_dict(u)


# ── Session management ─────────────────────────────────────────────────────


async def create_session(user_id: int, ip: str | None = None) -> tuple[str, datetime]:
    token = secrets.token_hex(32)
    expires = datetime.now(timezone.utc) + SESSION_LIFETIME
    p = await pool()
    await p.fetchval(
        "SELECT omega_auth_create_session($1, $2, $3, $4)",
        hash_session_token(token),
        user_id,
        expires,
        ip,
    )
    return token, expires


async def get_session_user(token: str) -> dict | None:
    """Return user dict if session is valid; slides expiration if close to expiring.

    Two independent windows apply:
      * SESSION_LIFETIME (sliding) — extended on each request via SESSION_SLIDE.
      * MAX_SESSION_LIFETIME (absolute) — measured from created_at; once
        exceeded the session is deleted server-side and the caller is forced
        to log in again. This guarantees password/role revocations propagate
        within the cap and prevents indefinite session extension.
    """
    if not token:
        return None
    p = await pool()
    row = await p.fetchrow(
        "SELECT * FROM omega_auth_resolve_session($1)",
        hash_session_token(token),
    )
    if not row:
        return None
    row_data = dict(row)
    tenant_id = row_data.get("tenant_id")
    return {
        "id": row["user_id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "is_active": row["is_active"],
        "must_change_password": row["must_change_password"],
        "tenant_id": str(tenant_id) if tenant_id else None,
    }


async def destroy_session(token: str) -> None:
    if not token:
        return
    p = await pool()
    await p.fetchval(
        "SELECT omega_auth_destroy_session($1)", hash_session_token(token)
    )


async def logout_tokens(
    session_token: str | None, refresh_token: str | None
) -> tuple[bool, bool]:
    """Atomically invalidate both browser credentials, if present."""
    p = await pool()
    row = await p.fetchrow(
        "SELECT * FROM omega_auth_logout($1, $2)",
        hash_session_token(session_token) if session_token else None,
        hash_refresh_token(refresh_token) if refresh_token else None,
    )
    return bool(row and row["session_deleted"]), bool(row and row["refresh_revoked"])


async def cleanup_expired_sessions() -> int:
    p = await pool()
    return int(await p.fetchval("SELECT omega_auth_cleanup_sessions()") or 0)


# ── Refresh token management ────────────────────────────────────────────────


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_refresh_token(user_id: int) -> tuple[str, datetime]:
    token = generate_refresh_token()
    expires = datetime.now(timezone.utc) + REFRESH_TOKEN_LIFETIME
    p = await pool()
    await p.fetchval(
        "SELECT omega_auth_create_refresh_token($1, $2, $3)",
        user_id,
        hash_refresh_token(token),
        expires,
    )
    return token, expires


async def get_refresh_token_user(token: str) -> dict | None:
    if not token:
        return None
    p = await pool()
    row = await p.fetchrow(
        "SELECT * FROM omega_auth_get_refresh_user($1)",
        hash_refresh_token(token),
    )
    if not row:
        return None
    row_data = dict(row)
    tenant_id = row_data.get("tenant_id")
    return {
        "id": row["user_id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "is_active": row["is_active"],
        "must_change_password": row["must_change_password"],
        "tenant_id": str(tenant_id) if tenant_id else None,
    }


async def revoke_refresh_token(token: str) -> None:
    if not token:
        return
    p = await pool()
    await p.fetchval(
        "SELECT omega_auth_revoke_refresh_token($1)", hash_refresh_token(token)
    )


async def rotate_refresh_token(
    token: str | None,
) -> tuple[dict, str, datetime] | None:
    """Consume one refresh token exactly once and create its successor.

    The database function performs the guarded UPDATE and successor INSERT in
    one statement. Concurrent callers therefore observe exactly one consumed
    row; there is intentionally no read/revoke/create fallback.
    """
    if not token:
        return None
    new_token = generate_refresh_token()
    expires = datetime.now(timezone.utc) + REFRESH_TOKEN_LIFETIME
    p = await pool()
    row = await p.fetchrow(
        "SELECT * FROM omega_auth_rotate_refresh_token($1, $2, $3)",
        hash_refresh_token(token),
        hash_refresh_token(new_token),
        expires,
    )
    if not row:
        return None
    row_data = dict(row)
    tenant_id = row_data.get("tenant_id")
    user = {
        "id": row["user_id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "is_active": row["is_active"],
        "must_change_password": row["must_change_password"],
        "tenant_id": str(tenant_id) if tenant_id else None,
    }
    return user, new_token, expires


# ── Helpers ─────────────────────────────────────────────────────────────────


def _user_to_dict(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in ("created_at", "last_login"):
        if d.get(k):
            d[k] = d[k].isoformat()
    if d.get("tenant_id"):
        d["tenant_id"] = str(d["tenant_id"])
    d.pop("password_hash", None)
    return d


def cookie_secure() -> bool:
    """Should the session cookie be marked Secure?

    Explicit COOKIE_SECURE env var always wins. Without it, default to
    True UNLESS APP_ENV is "development" (local dev over http://localhost
    cannot accept Secure cookies). This makes the safe production default
    automatic and leaves dev behaviour unchanged.
    """
    explicit = os.environ.get("COOKIE_SECURE")
    if explicit is not None:
        return explicit.lower() == "true"
    # v1.43.2 (Codex P1-2): default flipped to ``production`` so a
    # forgotten APP_ENV no longer ships insecure cookies to a real
    # browser. Local dev opts in via APP_ENV=development in compose.
    app_env = os.environ.get("APP_ENV", "production").lower()
    return app_env != "development"


def verify_internal_api_key(
    x_api_key: str | None = Header(None),
    x_internal_service: str | None = Header(None),
) -> str:
    # Sprint v1.12: console exposes /internal/* endpoints to workspace and
    # to the built-in cartridges. Each pair has its own dedicated key. The
    # legacy shared INTERNAL_API_KEY is still accepted during migration.
    if (
        not x_internal_service
        or x_internal_service not in _ALLOWED_INTERNAL_SERVICES_TO_KEY_ENV
    ):
        raise HTTPException(status_code=403, detail="Invalid internal service origin")
    if not x_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    accepted: list[str] = []
    pair_key_env = _ALLOWED_INTERNAL_SERVICES_TO_KEY_ENV.get(x_internal_service)
    if pair_key_env:
        pair_key = os.environ.get(pair_key_env)
        if pair_key:
            accepted.append(pair_key)
    legacy = get_internal_api_key()
    if legacy and not _is_production():
        accepted.append(legacy)

    if not any(secrets.compare_digest(x_api_key, k) for k in accepted if k):
        raise HTTPException(status_code=403, detail="Forbidden")
    return x_internal_service
