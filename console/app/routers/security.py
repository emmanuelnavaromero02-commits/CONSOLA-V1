import hashlib
import hmac
import json
import os

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.services import auth as _auth
from app.services import audit_service as _audit
from app.services.jwt_auth import DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES
from app.services.permissions import (
    PERMISSIONS,
    access_check,
    matrix_payload,
    require_permission,
    roles_payload,
)

router = APIRouter(prefix="/security", tags=["Security Center"])


def _session_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _table_exists(conn, table_name: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1)", f"public.{table_name}"))


async def _columns(conn, table_name: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = $1
        """,
        table_name,
    )
    return {row["column_name"] for row in rows}


def _select_column(
    columns: set[str],
    name: str,
    fallback: str,
    alias: str | None = None,
    table_alias: str = "s",
) -> str:
    target = alias or name
    if name in columns:
        return f"{table_alias}.{name} AS {target}"
    return f"{fallback} AS {target}"


async def _db_roles(conn) -> list[dict]:
    if not await _table_exists(conn, "roles"):
        return []
    rows = await conn.fetch("SELECT name, description FROM roles ORDER BY name")
    return [dict(row) for row in rows]


def _policy_summary() -> dict:
    access_minutes = os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", str(DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES))
    return {
        "brute_force": "5 failed login attempts / 15 minutes",
        "session_lifetime": f"{_auth.SESSION_LIFETIME.days} days",
        "session_sliding_window": f"{_auth.SESSION_SLIDE.days} day",
        "access_token_ttl": f"{access_minutes} minutes",
        "refresh_token_ttl": f"{_auth.REFRESH_TOKEN_LIFETIME.days} days",
        "internal_api": "Required for internal service endpoints",
        "security_headers": "Enabled by Console middleware",
        "secrets": "Masked; reveal requires admin/workspace_admin where supported",
    }


async def _schema_status(conn) -> dict:
    session_columns = await _columns(conn, "user_sessions") if await _table_exists(conn, "user_sessions") else set()
    return {
        "user_sessions": {
            "exists": bool(session_columns),
            "missing_columns": sorted({"last_seen", "user_agent"} - session_columns),
        },
        "audit_events": {
            "exists": await _table_exists(conn, "audit_events"),
            "migration": "infra/init/16_audit_events.sql",
        },
        "login_attempts": {
            "exists": await _table_exists(conn, "login_attempts"),
            "migration": "infra/init/17_login_security.sql",
        },
    }


@router.get("/sessions")
async def get_sessions(user: dict = Depends(require_permission("security.sessions.read"))):
    p = await _auth.pool()
    if not await _table_exists(p, "user_sessions"):
        return []
    session_columns = await _columns(p, "user_sessions")
    select_parts = [
        "s.token",
        "s.user_id",
        "u.email AS user_email",
        _select_column(session_columns, "ip", "NULL::text"),
        _select_column(session_columns, "last_seen", "NULL::timestamptz"),
        _select_column(session_columns, "user_agent", "NULL::text"),
        _select_column(session_columns, "created_at", "NULL::timestamptz"),
        _select_column(session_columns, "expires_at", "NULL::timestamptz"),
    ]
    order_expr = "s.last_seen DESC NULLS LAST" if "last_seen" in session_columns else "s.created_at DESC NULLS LAST"
    rows = await p.fetch(
        f"""SELECT {", ".join(select_parts)}
           FROM user_sessions s
           JOIN users u ON u.id = s.user_id
           ORDER BY {order_expr}"""
    )
    res = []
    for r in rows:
        d = dict(r)
        token = d.pop("token")
        d["session_id"] = _session_id(token)
        d["token_preview"] = token[:8] + "..." if token and len(token) > 8 else "***"
        res.append(d)
    return res

@router.delete("/sessions/{token}")
async def revoke_session(token: str, request: Request, user: dict = Depends(require_permission("security.sessions.revoke"))):
    p = await _auth.pool()
    res = await p.execute("DELETE FROM user_sessions WHERE token = $1", token)
    if res == "DELETE 0" and len(token) == 64:
        rows = await p.fetch("SELECT token FROM user_sessions")
        matched = next(
            (
                r["token"]
                for r in rows
                if hmac.compare_digest(_session_id(r["token"]), token)
            ),
            None,
        )
        if matched:
            res = await p.execute("DELETE FROM user_sessions WHERE token = $1", matched)
    if res == "DELETE 0":
        raise HTTPException(status_code=404, detail="Session not found")
    await _audit.record_event(
        user.get("id"), user.get("email"), "session.revoked", "session", token[-8:] if token else None,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "ok"}

@router.get("/audit")
async def get_audit_events(user: dict = Depends(require_permission("security.audit.read"))):
    p = await _auth.pool()
    if not await _table_exists(p, "audit_events"):
        return []
    audit_columns = await _columns(p, "audit_events")
    select_parts = [
        _select_column(audit_columns, "id", "NULL::bigint", table_alias="a"),
        _select_column(audit_columns, "user_id", "NULL::bigint", table_alias="a"),
        "COALESCE(u.email, a.email) AS user_email" if "email" in audit_columns else "u.email AS user_email",
        _select_column(audit_columns, "action", "NULL::text", table_alias="a"),
        _select_column(audit_columns, "resource_type", "NULL::text", table_alias="a"),
        _select_column(audit_columns, "resource_id", "NULL::text", table_alias="a"),
        _select_column(audit_columns, "metadata", "NULL::jsonb", "details", table_alias="a"),
        _select_column(audit_columns, "ip", "NULL::text", table_alias="a"),
        _select_column(audit_columns, "created_at", "NULL::timestamptz", table_alias="a"),
    ]
    order_expr = "a.created_at DESC" if "created_at" in audit_columns else "a.id DESC"
    rows = await p.fetch(
        f"""SELECT {", ".join(select_parts)}
           FROM audit_events a
           LEFT JOIN users u ON u.id = a.user_id
           ORDER BY {order_expr}
           LIMIT 100"""
    )
    res = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("details"), asyncpg.Record):
            d["details"] = dict(d["details"])
        if isinstance(d.get("details"), str):
            try:
                d["details"] = json.loads(d["details"])
            except json.JSONDecodeError:
                pass
        res.append(d)
    return res


@router.get("/permissions")
async def get_permissions(user: dict = Depends(require_permission("iam.roles.read"))):
    p = await _auth.pool()
    db_roles = await _db_roles(p)
    lifetimes = _policy_summary()
    return {
        "roles": roles_payload(),
        "db_roles": db_roles,
        "assignable_now": [role["name"] for role in roles_payload() if role.get("assignable")],
        "backend_defined_roles": [role["name"] for role in roles_payload()],
        "legacy_user_admin_roles": ["admin", "user"],
        "workspace_roles": ["admin", "workspace_admin", "analyst", "viewer"],
        "permissions": PERMISSIONS,
        "matrix": matrix_payload(),
        "policies": {
            "deny_by_default": True,
            "secrets_never_exposed": True,
            "session_tokens_masked": True,
            "internal_api_required": True,
            "security_headers_enabled": True,
        },
        "credential_lifetimes": {
            "access_token_minutes": int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", str(DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES))),
            "refresh_token_days": _auth.REFRESH_TOKEN_LIFETIME.days,
            "password_reset_token_minutes": int(os.environ.get("RESET_TOKEN_TTL_HOURS", "1")) * 60,
            "invitation_token_minutes": int(os.environ.get("INVITE_TOKEN_TTL_HOURS", "72")) * 60,
            "lockout_window_minutes": 15,
            "max_login_attempts": 5,
            "session_lifetime_days": _auth.SESSION_LIFETIME.days,
            "session_sliding_window_days": _auth.SESSION_SLIDE.days,
        },
        "policy_summary": lifetimes,
        "schema_status": await _schema_status(p),
        "user_management": {
            "direct_create": True,
            "invite": True,
            "edit_role_state": True,
            "reset_password_email": True,
            "reinvite": True,
            "assignable_roles": [role["name"] for role in roles_payload() if role.get("assignable")],
            "workspace_role_assignment": "legacy users.role only; workspace membership assignment is not implemented",
        },
        "notes": [
            "User management API accepts built-in canonical roles through users.role; admin/user remain backward compatible.",
            "High-value IAM, Security, Vault, Dataset and Pipeline endpoints are enforced by the central permission registry.",
            "Workspace membership assignment is not implemented in this phase; workspace-scoped roles are stored as user roles only.",
        ],
    }


@router.get("/login-attempts")
async def get_login_attempts(user: dict = Depends(require_permission("security.login_attempts.read"))):
    p = await _auth.pool()
    if not await _table_exists(p, "login_attempts"):
        return []
    attempt_columns = await _columns(p, "login_attempts")
    select_parts = [
        _select_column(attempt_columns, "id", "NULL::bigint", table_alias="la"),
        _select_column(attempt_columns, "email", "NULL::text", table_alias="la"),
        _select_column(attempt_columns, "ip", "NULL::text", table_alias="la"),
        _select_column(attempt_columns, "success", "NULL::boolean", table_alias="la"),
        _select_column(attempt_columns, "created_at", "NULL::timestamptz", table_alias="la"),
    ]
    order_expr = "la.created_at DESC" if "created_at" in attempt_columns else "la.id DESC"
    rows = await p.fetch(
        f"""SELECT {", ".join(select_parts)}
              FROM login_attempts la
             ORDER BY {order_expr}
             LIMIT 100"""
    )
    return [dict(row) for row in rows]


@router.get("/access-check")
async def get_access_check(
    request: Request,
    role: str = Query(...),
    resource: str = Query(...),
    action: str = Query("read"),
    user: dict = Depends(require_permission("iam.roles.read")),
):
    result = access_check(role, resource, action)
    await _audit.record_event(
        user.get("id"), user.get("email"), "iam.access_check", "permission", result.get("permission"),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"role": role, "resource": resource, "action": action, "allowed": result["allowed"]},
    )
    return result
