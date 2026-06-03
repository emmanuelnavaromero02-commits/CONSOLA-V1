import hashlib
import hmac
import json
import os

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.services import auth as _auth
from app.services import audit_service as _audit
from app.services.csrf import require_csrf
from app.services.jwt_auth import DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES
from app.services.permissions import (
    PERMISSIONS,
    access_check,
    canonical_role,
    matrix_payload,
    require_permission,
    roles_payload,
)

router = APIRouter(prefix="/security", tags=["Security Center"])


_PLATFORM_ROLES = {"owner", "super_admin", "admin"}
_TENANT_ASSIGNABLE_ROLES = {"tenant_admin", "analyst", "viewer", "workspace_user", "user"}


def _is_platform_admin(user: dict | None) -> bool:
    return canonical_role((user or {}).get("role")) in _PLATFORM_ROLES


def _workspace_ids(user: dict | None) -> list[str]:
    if not user:
        return []
    values = {
        str(item.get("workspace_id") or "").strip()
        for item in (user.get("workspaces") or [])
        if isinstance(item, dict)
    }
    active = str(user.get("active_workspace_id") or user.get("workspace_id") or "").strip()
    if active:
        values.add(active)
    return sorted(value for value in values if value)


async def _visible_workspace_user_ids(conn, workspace_ids: list[str]) -> list[int]:
    if not workspace_ids or not await _table_exists(conn, "user_workspace_roles"):
        return []
    rows = await conn.fetch(
        """
        SELECT DISTINCT uwr.user_id
          FROM user_workspace_roles uwr
          JOIN users u ON u.id = uwr.user_id
         WHERE uwr.workspace_id = ANY($1::uuid[])
           AND COALESCE(u.role, 'user') <> ALL($2::text[])
        """,
        workspace_ids,
        sorted(_PLATFORM_ROLES),
    )
    return [int(row["user_id"]) for row in rows if row["user_id"] is not None]


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
        "secrets": "Masked; reveal requires an explicit vault.secrets.reveal grant.",
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

@router.delete("/sessions/{token}", dependencies=[Depends(require_csrf)])
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
        _select_column(audit_columns, "request_id", "NULL::text", table_alias="a"),
        _select_column(audit_columns, "created_at", "NULL::timestamptz", table_alias="a"),
    ]
    order_expr = "a.created_at DESC" if "created_at" in audit_columns else "a.id DESC"
    args: list = []
    where_clause = ""
    if not _is_platform_admin(user):
        workspace_ids = _workspace_ids(user)
        tenant_id = str(user.get("active_tenant_id") or user.get("tenant_id") or "").strip()
        visible_user_ids = await _visible_workspace_user_ids(p, workspace_ids)
        scope_parts: list[str] = []
        if "user_id" in audit_columns and visible_user_ids:
            args.append(visible_user_ids)
            scope_parts.append(f"a.user_id = ANY(${len(args)}::bigint[])")
        if "metadata" in audit_columns and workspace_ids:
            args.append(workspace_ids)
            scope_parts.append(f"a.metadata->>'workspace_id' = ANY(${len(args)}::text[])")
        if "metadata" in audit_columns and tenant_id:
            args.append(tenant_id)
            scope_parts.append(f"a.metadata->>'tenant_id' = ${len(args)}")
        if not scope_parts:
            return []
        where_clause = "WHERE (" + " OR ".join(scope_parts) + ")"
        if "user_id" in audit_columns:
            args.append(int(user["id"]))
            where_clause += (
                f" AND (a.user_id IS NULL OR COALESCE(u.role, 'user') <> ALL(ARRAY{sorted(_PLATFORM_ROLES)!r}::text[]) "
                f"OR a.user_id = ${len(args)}::bigint)"
            )
    rows = await p.fetch(
        f"""SELECT {", ".join(select_parts)}
           FROM audit_events a
           LEFT JOIN users u ON u.id = a.user_id
           {where_clause}
           ORDER BY {order_expr}
           LIMIT 100""",
        *args,
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
    platform = _is_platform_admin(user)
    role_rows = roles_payload()
    matrix = matrix_payload()
    permissions = PERMISSIONS
    notes = [
        "User management API accepts built-in canonical roles through users.role; admin/user remain backward compatible.",
        "High-value IAM, Security, Vault, Dataset and Pipeline endpoints are enforced by the central permission registry.",
        "Workspace membership is applied for tenant-created users and all tenant admin views are workspace-scoped.",
    ]
    if not platform:
        role_rows = [role for role in role_rows if role["name"] in _TENANT_ASSIGNABLE_ROLES]
        visible_role_names = {role["name"] for role in role_rows}
        visible_permission_keys = {
            permission
            for role in role_rows
            for permission in role.get("permissions", [])
        }
        matrix = {
            role: {
                permission: allowed
                for permission, allowed in permissions_by_role.items()
                if permission in visible_permission_keys
            }
            for role, permissions_by_role in matrix.items()
            if role in visible_role_names
        }
        permissions = [
            permission
            for permission in PERMISSIONS
            if permission["key"] in visible_permission_keys
        ]
        db_roles = [role for role in db_roles if role.get("name") in visible_role_names]
        notes = [
            "Tenant role and audit views are limited to the active workspace.",
            "Vault secrets are stored in workspace-scoped Vault paths; tenants can replace keys but cannot reveal existing secret values.",
            "Studio, Bronze query, global settings and platform security roles are hidden from tenant accounts.",
        ]
    assignable_roles = [role["name"] for role in role_rows if role.get("assignable")]
    return {
        "roles": role_rows,
        "db_roles": db_roles,
        "assignable_now": assignable_roles,
        "backend_defined_roles": [role["name"] for role in role_rows],
        "legacy_user_admin_roles": ["admin", "user"],
        "workspace_roles": assignable_roles,
        "permissions": permissions,
        "matrix": matrix,
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
            "assignable_roles": assignable_roles,
            "workspace_role_assignment": "tenant-created users are assigned to the active workspace with a scoped workspace role",
        },
        "notes": notes,
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
