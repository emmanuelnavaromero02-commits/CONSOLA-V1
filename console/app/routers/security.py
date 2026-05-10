import hashlib
import hmac
import json
import os

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from app.dependencies import require_admin
from app.services import auth as _auth
from app.services.jwt_auth import DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES

router = APIRouter(prefix="/security", tags=["Security Center"])

PERMISSION_MATRIX = [
    {
        "role": "admin",
        "description": "Full local console administration.",
        "console": {"access": "allowed", "status": "enforced", "note": "Authenticated admin."},
        "monitor": {"access": "allowed", "status": "enforced", "note": "Authenticated page/API access."},
        "studio": {"access": "allowed", "status": "enforced", "note": "Admin passes write guards."},
        "pipelines": {"access": "write", "status": "enforced", "note": "admin/workspace_admin guarded."},
        "datasets": {"access": "write", "status": "enforced", "note": "admin/workspace_admin for writes."},
        "vault": {"access": "write", "status": "enforced", "note": "admin/workspace_admin guarded."},
        "security": {"access": "admin", "status": "enforced", "note": "require_admin."},
        "users": {"access": "admin", "status": "enforced", "note": "require_role(admin)."},
        "audit": {"access": "read", "status": "enforced", "note": "require_admin."},
    },
    {
        "role": "workspace_admin",
        "description": "Workspace-scoped operations where endpoint guards include workspace_admin.",
        "console": {"access": "workspace", "status": "backend only", "note": "Workspace role exists; user UI cannot assign it yet."},
        "monitor": {"access": "read", "status": "enforced", "note": "Authenticated access."},
        "studio": {"access": "write", "status": "enforced", "note": "Some write endpoints include workspace_admin."},
        "pipelines": {"access": "write", "status": "enforced", "note": "Pipeline extract guarded for admin/workspace_admin."},
        "datasets": {"access": "write", "status": "enforced", "note": "Dataset writes guarded for admin/workspace_admin."},
        "vault": {"access": "write", "status": "enforced", "note": "Vault APIs include workspace_admin."},
        "security": {"access": "denied", "status": "enforced", "note": "Security APIs require admin."},
        "users": {"access": "denied", "status": "enforced", "note": "User APIs require admin."},
        "audit": {"access": "denied", "status": "enforced", "note": "Audit API requires admin."},
    },
    {
        "role": "analyst",
        "description": "Analyst read/query operations and constrained Studio tools.",
        "console": {"access": "limited", "status": "backend only", "note": "Workspace role exists; user UI cannot assign it yet."},
        "monitor": {"access": "read", "status": "enforced", "note": "Authenticated access."},
        "studio": {"access": "read", "status": "enforced", "note": "Studio Ops restricts analyst tools."},
        "pipelines": {"access": "read", "status": "enforced", "note": "Write routes deny analyst."},
        "datasets": {"access": "query", "status": "enforced", "note": "Bronze query allows analyst."},
        "vault": {"access": "denied", "status": "enforced", "note": "Vault APIs require admin/workspace_admin."},
        "security": {"access": "denied", "status": "enforced", "note": "Security APIs require admin."},
        "users": {"access": "denied", "status": "enforced", "note": "User APIs require admin."},
        "audit": {"access": "denied", "status": "enforced", "note": "Audit API requires admin."},
    },
    {
        "role": "user",
        "description": "Legacy/basic user role for workspace app access.",
        "console": {"access": "basic", "status": "enforced", "note": "Authenticated non-admin."},
        "monitor": {"access": "allowed", "status": "enforced", "note": "Monitor page is authenticated."},
        "studio": {"access": "allowed page / limited API", "status": "enforced", "note": "Page loads; APIs enforce roles."},
        "pipelines": {"access": "denied writes", "status": "enforced", "note": "Pipeline writes require admin/workspace_admin."},
        "datasets": {"access": "read if workspace membership exists", "status": "enforced", "note": "Depends on require_authenticated and workspace mapping."},
        "vault": {"access": "denied API", "status": "enforced", "note": "Vault APIs require admin/workspace_admin."},
        "security": {"access": "denied", "status": "enforced", "note": "IAM/Security pages and APIs require admin."},
        "users": {"access": "denied", "status": "enforced", "note": "User APIs require admin."},
        "audit": {"access": "denied", "status": "enforced", "note": "Audit API requires admin."},
    },
]

ACCESS_TESTS = [
    {"role": "admin", "resource": "/iam", "expected": "allowed", "status": "enforced", "reason": "GET /iam depends on admin role."},
    {"role": "admin", "resource": "/security/audit", "expected": "allowed", "status": "enforced", "reason": "GET /security/audit depends on require_admin."},
    {"role": "admin", "resource": "/api/admin/users", "expected": "allowed", "status": "enforced", "reason": "User API depends on admin role."},
    {"role": "admin", "resource": "/api/vault/connections/replicon", "expected": "allowed", "status": "enforced", "reason": "Vault API allows admin/workspace_admin."},
    {"role": "user", "resource": "/iam", "expected": "denied", "status": "enforced", "reason": "GET /iam depends on admin role."},
    {"role": "user", "resource": "/security/audit", "expected": "denied", "status": "enforced", "reason": "Security APIs require admin."},
    {"role": "user", "resource": "/api/admin/users", "expected": "denied", "status": "enforced", "reason": "Admin user APIs require admin."},
    {"role": "user", "resource": "/viewer/vault", "expected": "page allowed / API denied", "status": "enforced", "reason": "Viewer page is authenticated; Vault APIs are role guarded."},
    {"role": "workspace_admin", "resource": "/api/vault/connections/replicon", "expected": "allowed", "status": "backend only", "reason": "Backend guard includes workspace_admin; UI assignment is not wired."},
    {"role": "analyst", "resource": "/api/vault/connections/replicon", "expected": "denied", "status": "enforced", "reason": "Vault APIs require admin/workspace_admin."},
    {"role": "viewer", "resource": "/security/audit", "expected": "denied", "status": "backend only", "reason": "Workspace role exists; security requires legacy admin."},
]


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
async def get_sessions(user: dict = Depends(require_admin)):
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
async def revoke_session(token: str, user: dict = Depends(require_admin)):
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
    return {"status": "ok"}

@router.get("/audit")
async def get_audit_events(user: dict = Depends(require_admin)):
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
async def get_permissions(user: dict = Depends(require_admin)):
    p = await _auth.pool()
    db_roles = await _db_roles(p)
    return {
        "roles": db_roles or [
            {"name": item["role"], "description": item["description"]}
            for item in PERMISSION_MATRIX
        ],
        "legacy_user_admin_roles": ["admin", "user"],
        "workspace_roles": ["admin", "workspace_admin", "analyst", "viewer"],
        "matrix": PERMISSION_MATRIX,
        "access_tests": ACCESS_TESTS,
        "policies": _policy_summary(),
        "schema_status": await _schema_status(p),
        "user_management": {
            "direct_create": True,
            "invite": True,
            "edit_role_state": True,
            "reset_password_email": True,
            "reinvite": True,
            "assignable_roles": ["admin", "user"],
            "workspace_role_assignment": "not implemented",
        },
        "notes": [
            "User management API currently accepts legacy roles admin/user only.",
            "Workspace RBAC roles are enforced by require_role/require_any_role on protected endpoints.",
            "Security Center and user administration require admin.",
        ],
    }


@router.get("/login-attempts")
async def get_login_attempts(user: dict = Depends(require_admin)):
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
