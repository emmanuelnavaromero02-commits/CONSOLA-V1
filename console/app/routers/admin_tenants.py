from __future__ import annotations

import re
import secrets
import unicodedata
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import ROLE_ADMIN, require_global_any_role
from app.services import audit_service, auth
from app.services.csrf import require_csrf


router = APIRouter(prefix="/api/admin/tenants", tags=["Admin Tenants"])

PLATFORM_ADMIN = require_global_any_role("owner", "super_admin", ROLE_ADMIN)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
DANGEROUS_GLOBAL_ROLES = {"owner", "admin", "super_admin", "security_admin", "auditor"}
MAX_NAME = 160
MAX_EMAIL = 254


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or None
    return request.client.host if request.client else None


def _normalize_name(value: Any, field: str) -> str:
    name = str(value or "").strip()
    if not name:
        raise HTTPException(400, f"{field} is required")
    if len(name) > MAX_NAME:
        raise HTTPException(400, f"{field} is too long")
    return name


def _normalize_email(value: Any) -> str:
    # Mismo endurecimiento de identidad que accounts.lifecycle (gemelo): NFKC +
    # rechazo de Cf/Cc + ASCII cierra homografos, zero-width y NFC/NFD.
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    if any(unicodedata.category(ch) in {"Cf", "Cc"} for ch in raw):
        raise HTTPException(400, "valid email is required")
    email = raw.lower()
    if (
        not email
        or not email.isascii()
        or len(email) > MAX_EMAIL
        or not EMAIL_RE.fullmatch(email)
    ):
        raise HTTPException(400, "valid email is required")
    return email


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:64].strip("-") or "tenant"


def _normalize_slug(value: Any, fallback_name: str) -> str:
    raw = str(value or "").strip()
    slug = _slugify(raw or fallback_name)
    if not SLUG_RE.fullmatch(slug):
        raise HTTPException(
            400, "slug must be 3-64 chars using lowercase letters, numbers and hyphens"
        )
    return slug


def _normalize_uuid(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(400, f"{field} is required")
    try:
        return str(UUID(text))
    except ValueError as exc:
        raise HTTPException(400, f"{field} must be a valid UUID") from exc


def _temporary_password() -> str:
    return f"{secrets.token_urlsafe(24)}Aa1!"


def _tenant_row(row) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": str(data["id"]),
        "name": data["name"],
        "slug": data.get("slug"),
        "status": data.get("status"),
        "created_at": data["created_at"].isoformat()
        if data.get("created_at")
        else None,
        "updated_at": data["updated_at"].isoformat()
        if data.get("updated_at")
        else None,
        "workspace_count": int(data.get("workspace_count") or 0),
        "user_count": int(data.get("user_count") or 0),
    }


def _tenant_admin_row(row) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": int(data["id"]),
        "email": data["email"],
        "name": data["name"],
        "role": data["role"],
        "workspace_role": data.get("workspace_role") or "tenant_admin",
        "is_active": bool(data["is_active"]),
        "must_change_password": bool(data["must_change_password"]),
        "tenant_id": str(data["tenant_id"]) if data.get("tenant_id") else None,
        "created_at": data["created_at"].isoformat()
        if data.get("created_at")
        else None,
    }


def _workspace_row(row, tenant_admins: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": str(data["id"]),
        "tenant_id": str(data["tenant_id"]),
        "name": data["name"],
        "created_at": data["created_at"].isoformat()
        if data.get("created_at")
        else None,
        "user_count": int(data.get("user_count") or 0),
        "tenant_admins": tenant_admins or [],
    }


def _user_row(row) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": int(data["id"]),
        "email": data["email"],
        "name": data["name"],
        "role": data["role"],
        "is_active": bool(data["is_active"]),
        "must_change_password": bool(data["must_change_password"]),
        "tenant_id": str(data["tenant_id"]) if data.get("tenant_id") else None,
        "created_at": data["created_at"].isoformat()
        if data.get("created_at")
        else None,
    }


async def _fetch_tenant_summary(conn, tenant_id: str):
    return await conn.fetchrow(
        """
        SELECT t.id, t.name, t.slug, t.status, t.created_at, t.updated_at,
               COUNT(DISTINCT w.id) AS workspace_count,
               COUNT(DISTINCT uwr.user_id) AS user_count
          FROM tenants t
          LEFT JOIN workspaces w ON w.tenant_id = t.id
          LEFT JOIN user_workspace_roles uwr ON uwr.workspace_id = w.id
         WHERE t.id = $1::uuid
         GROUP BY t.id, t.name, t.slug, t.status, t.created_at, t.updated_at
        """,
        tenant_id,
    )


async def _fetch_workspace_summary(conn, workspace_id: str):
    return await conn.fetchrow(
        """
        SELECT w.id, w.tenant_id, w.name, w.created_at,
               COUNT(DISTINCT uwr.user_id) AS user_count
          FROM workspaces w
          LEFT JOIN user_workspace_roles uwr ON uwr.workspace_id = w.id
         WHERE w.id = $1::uuid
         GROUP BY w.id, w.tenant_id, w.name, w.created_at
        """,
        workspace_id,
    )


async def _fetch_workspace_tenant_admins(
    conn, tenant_id: str, workspace_id: str
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT uwr.workspace_id::text AS workspace_id,
               u.id, u.email, u.name, u.role, u.is_active,
               u.must_change_password, u.tenant_id, u.created_at,
               r.name AS workspace_role
          FROM user_workspace_roles uwr
          JOIN workspaces w ON w.id = uwr.workspace_id
          JOIN users u ON u.id = uwr.user_id
          JOIN roles r ON r.id = uwr.role_id
         WHERE w.tenant_id = $1::uuid
           AND w.id = $2::uuid
           AND r.name = 'tenant_admin'
         ORDER BY lower(u.email)
        """,
        tenant_id,
        workspace_id,
    )
    return [_tenant_admin_row(row) for row in rows]


async def _assign_existing_tenant_admins_to_workspace(
    conn, tenant_id: str, workspace_id: str
) -> None:
    role_id = await conn.fetchval("SELECT id FROM roles WHERE name = 'tenant_admin'")
    if not role_id:
        return
    await conn.execute(
        """
        INSERT INTO user_workspace_roles (user_id, workspace_id, role_id)
        SELECT DISTINCT uwr.user_id, $1::uuid, $2::integer
          FROM user_workspace_roles uwr
          JOIN workspaces source_w ON source_w.id = uwr.workspace_id
          JOIN roles source_r ON source_r.id = uwr.role_id
          JOIN users u ON u.id = uwr.user_id
         WHERE source_w.tenant_id = $3::uuid
           AND source_r.name = 'tenant_admin'
           AND u.is_active = TRUE
           AND (u.tenant_id IS NULL OR u.tenant_id = $3::uuid)
           AND NOT EXISTS (
             SELECT 1
               FROM user_workspace_roles existing
              WHERE existing.user_id = uwr.user_id
                AND existing.workspace_id = $1::uuid
           )
        ON CONFLICT DO NOTHING
        """,
        workspace_id,
        role_id,
        tenant_id,
    )


async def _record(
    request: Request,
    actor: dict,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    await audit_service.record_event(
        actor.get("id"),
        actor.get("email"),
        action,
        resource_type,
        resource_id,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata=metadata or {},
    )


@router.get("")
async def list_tenants(_admin: dict = Depends(PLATFORM_ADMIN)):
    pool = await auth.pool()
    rows = await pool.fetch(
        """
        SELECT t.id, t.name, t.slug, t.status, t.created_at, t.updated_at,
               COUNT(DISTINCT w.id) AS workspace_count,
               COUNT(DISTINCT uwr.user_id) AS user_count
          FROM tenants t
          LEFT JOIN workspaces w ON w.tenant_id = t.id
          LEFT JOIN user_workspace_roles uwr ON uwr.workspace_id = w.id
         GROUP BY t.id, t.name, t.slug, t.status, t.created_at, t.updated_at
         ORDER BY t.created_at DESC, t.name ASC
        """
    )
    return {"tenants": [_tenant_row(row) for row in rows]}


@router.post("", dependencies=[Depends(require_csrf)])
async def create_tenant(
    body: dict, request: Request, admin: dict = Depends(PLATFORM_ADMIN)
):
    name = _normalize_name(body.get("name"), "name")
    slug = _normalize_slug(body.get("slug"), name)
    pool = await auth.pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT id, name, slug, status, created_at, updated_at FROM tenants WHERE lower(name) = lower($1) OR slug = $2",
                name,
                slug,
            )
            created = False
            if existing:
                if existing["slug"] != slug or existing["name"].lower() != name.lower():
                    raise HTTPException(409, "tenant slug or name already exists")
                row = existing
            else:
                try:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO tenants (name, slug, status)
                        VALUES ($1, $2, 'active')
                        RETURNING id, name, slug, status, created_at, updated_at
                        """,
                        name,
                        slug,
                    )
                    created = True
                except asyncpg.UniqueViolationError as exc:
                    raise HTTPException(409, "tenant already exists") from exc
            summary = await _fetch_tenant_summary(conn, str(row["id"]))
    if created:
        await _record(
            request,
            admin,
            "tenant_created",
            "tenant",
            str(row["id"]),
            {"name": name, "slug": slug},
        )
    return {"tenant": _tenant_row(summary), "created": created}


@router.get("/{tenant_id}/workspaces")
async def list_workspaces(tenant_id: str, _admin: dict = Depends(PLATFORM_ADMIN)):
    tenant_id = _normalize_uuid(tenant_id, "tenant_id")
    pool = await auth.pool()
    tenant_exists = await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM tenants WHERE id = $1::uuid)", tenant_id
    )
    if not tenant_exists:
        raise HTTPException(404, "tenant not found")
    rows = await pool.fetch(
        """
        SELECT w.id, w.tenant_id, w.name, w.created_at,
               COUNT(DISTINCT uwr.user_id) AS user_count
          FROM workspaces w
          LEFT JOIN user_workspace_roles uwr ON uwr.workspace_id = w.id
         WHERE w.tenant_id = $1::uuid
         GROUP BY w.id, w.tenant_id, w.name, w.created_at
         ORDER BY w.created_at ASC, w.name ASC
        """,
        tenant_id,
    )
    admin_rows = await pool.fetch(
        """
        SELECT uwr.workspace_id::text AS workspace_id,
               u.id, u.email, u.name, u.role, u.is_active,
               u.must_change_password, u.tenant_id, u.created_at,
               r.name AS workspace_role
          FROM user_workspace_roles uwr
          JOIN workspaces w ON w.id = uwr.workspace_id
          JOIN users u ON u.id = uwr.user_id
          JOIN roles r ON r.id = uwr.role_id
         WHERE w.tenant_id = $1::uuid
           AND r.name = 'tenant_admin'
         ORDER BY lower(u.email)
        """,
        tenant_id,
    )
    admins_by_workspace: dict[str, list[dict[str, Any]]] = {}
    for row in admin_rows:
        admins_by_workspace.setdefault(row["workspace_id"], []).append(
            _tenant_admin_row(row)
        )
    return {
        "workspaces": [
            _workspace_row(row, admins_by_workspace.get(str(row["id"]), []))
            for row in rows
        ]
    }


@router.post("/{tenant_id}/workspaces", dependencies=[Depends(require_csrf)])
async def create_workspace(
    tenant_id: str, body: dict, request: Request, admin: dict = Depends(PLATFORM_ADMIN)
):
    tenant_id = _normalize_uuid(tenant_id, "tenant_id")
    name = _normalize_name(body.get("name"), "name")
    pool = await auth.pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            tenant_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM tenants WHERE id = $1::uuid)", tenant_id
            )
            if not tenant_exists:
                raise HTTPException(404, "tenant not found")
            existing = await conn.fetchrow(
                "SELECT id, tenant_id, name, created_at FROM workspaces WHERE tenant_id = $1::uuid AND lower(name) = lower($2)",
                tenant_id,
                name,
            )
            created = False
            if existing:
                row = existing
            else:
                try:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO workspaces (tenant_id, name)
                        VALUES ($1::uuid, $2)
                        RETURNING id, tenant_id, name, created_at
                        """,
                        tenant_id,
                        name,
                    )
                    created = True
                except asyncpg.UniqueViolationError as exc:
                    raise HTTPException(409, "workspace already exists") from exc
            await _assign_existing_tenant_admins_to_workspace(
                conn, tenant_id, str(row["id"])
            )
            summary = await _fetch_workspace_summary(conn, str(row["id"]))
            tenant_admins = await _fetch_workspace_tenant_admins(
                conn, tenant_id, str(row["id"])
            )
    if created:
        await _record(
            request,
            admin,
            "workspace_created",
            "workspace",
            str(row["id"]),
            {"tenant_id": tenant_id, "name": name},
        )
    return {
        "workspace": _workspace_row(summary, tenant_admins),
        "created": created,
    }


@router.post("/{tenant_id}/bootstrap-admin", dependencies=[Depends(require_csrf)])
async def bootstrap_tenant_admin(
    tenant_id: str,
    body: dict,
    request: Request,
    admin: dict = Depends(PLATFORM_ADMIN),
):
    tenant_id = _normalize_uuid(tenant_id, "tenant_id")
    workspace_id = _normalize_uuid(body.get("workspace_id"), "workspace_id")
    email = _normalize_email(body.get("email"))
    name = str(body.get("name") or "").strip() or None
    if name and len(name) > MAX_NAME:
        raise HTTPException(400, "name is too long")

    temporary_password: str | None = None
    pool = await auth.pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            workspace = await conn.fetchrow(
                """
                SELECT w.id, w.tenant_id, w.name, t.name AS tenant_name
                  FROM workspaces w
                  JOIN tenants t ON t.id = w.tenant_id
                 WHERE w.id = $1::uuid
                   AND w.tenant_id = $2::uuid
                """,
                workspace_id,
                tenant_id,
            )
            if not workspace:
                raise HTTPException(404, "workspace not found for tenant")

            role_id = await conn.fetchval(
                "SELECT id FROM roles WHERE name = 'tenant_admin'"
            )
            if not role_id:
                raise HTTPException(500, "tenant_admin role missing")

            existing = await conn.fetchrow(
                "SELECT id, email, name, role, is_active, must_change_password, tenant_id, created_at FROM users WHERE lower(email) = lower($1)",
                email,
            )
            created = False
            if existing:
                if existing["tenant_id"] and str(existing["tenant_id"]) != tenant_id:
                    raise HTTPException(
                        409, "existing user already belongs to another tenant"
                    )
                if str(existing["role"]) in DANGEROUS_GLOBAL_ROLES:
                    raise HTTPException(409, "existing user has a platform role")
                memberships = await conn.fetch(
                    """
                    SELECT uwr.workspace_id::text AS workspace_id, w.tenant_id::text AS tenant_id
                      FROM user_workspace_roles uwr
                      JOIN workspaces w ON w.id = uwr.workspace_id
                     WHERE uwr.user_id = $1
                    """,
                    existing["id"],
                )
                if any(row["workspace_id"] != workspace_id for row in memberships):
                    raise HTTPException(
                        409, "existing user already belongs to another workspace"
                    )
                row = await conn.fetchrow(
                    """
                    UPDATE users
                       SET name = COALESCE($2, name),
                           role = 'user',
                           is_active = TRUE,
                           tenant_id = $3::uuid
                     WHERE id = $1
                     RETURNING id, email, name, role, is_active, must_change_password, tenant_id, created_at
                    """,
                    existing["id"],
                    name,
                    tenant_id,
                )
            else:
                temporary_password = _temporary_password()
                row = await conn.fetchrow(
                    """
                    INSERT INTO users (email, name, password_hash, role, is_active, must_change_password, tenant_id)
                    VALUES ($1, $2, $3, 'user', TRUE, TRUE, $4::uuid)
                    RETURNING id, email, name, role, is_active, must_change_password, tenant_id, created_at
                    """,
                    email,
                    name,
                    auth.hash_password(temporary_password),
                    tenant_id,
                )
                created = True

            await conn.execute(
                "DELETE FROM user_workspace_roles WHERE user_id = $1", row["id"]
            )
            await conn.execute(
                """
                INSERT INTO user_workspace_roles (user_id, workspace_id, role_id)
                VALUES ($1, $2::uuid, $3)
                ON CONFLICT DO NOTHING
                """,
                row["id"],
                workspace_id,
                role_id,
            )

    if created:
        await _record(
            request,
            admin,
            "tenant_admin_created",
            "user",
            str(row["id"]),
            {"tenant_id": tenant_id, "workspace_id": workspace_id, "role": "user"},
        )
    await _record(
        request,
        admin,
        "tenant_admin_assigned",
        "workspace",
        workspace_id,
        {
            "tenant_id": tenant_id,
            "user_id": int(row["id"]),
            "workspace_role": "tenant_admin",
        },
    )
    return {
        "user": _user_row(row),
        "created": created,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "workspace_role": "tenant_admin",
        "temporary_password": temporary_password,
        "password_delivery": "one_time_response"
        if temporary_password
        else "existing_user_no_password_generated",
        "login_url": "/login",
    }


@router.post(
    "/{tenant_id}/admins/{user_id}/temporary-password",
    dependencies=[Depends(require_csrf)],
)
async def issue_tenant_admin_temporary_password(
    tenant_id: str,
    user_id: int,
    body: dict,
    request: Request,
    admin: dict = Depends(PLATFORM_ADMIN),
):
    tenant_id = _normalize_uuid(tenant_id, "tenant_id")
    workspace_id = _normalize_uuid(body.get("workspace_id"), "workspace_id")
    if user_id <= 0:
        raise HTTPException(400, "user_id must be positive")

    temporary_password = _temporary_password()
    pool = await auth.pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT u.id, u.email, u.name, u.role, u.is_active,
                       u.must_change_password, u.tenant_id, u.created_at,
                       r.name AS workspace_role,
                       w.id::text AS workspace_id
                  FROM users u
                  JOIN user_workspace_roles uwr ON uwr.user_id = u.id
                  JOIN roles r ON r.id = uwr.role_id
                  JOIN workspaces w ON w.id = uwr.workspace_id
                 WHERE u.id = $1
                   AND w.id = $2::uuid
                   AND w.tenant_id = $3::uuid
                   AND r.name = 'tenant_admin'
                 LIMIT 1
                """,
                user_id,
                workspace_id,
                tenant_id,
            )
            if not row:
                raise HTTPException(404, "tenant admin not found for workspace")
            if row["tenant_id"] and str(row["tenant_id"]) != tenant_id:
                raise HTTPException(409, "tenant admin belongs to another tenant")
            if str(row["role"]) in DANGEROUS_GLOBAL_ROLES:
                raise HTTPException(409, "tenant admin has a platform role")

            updated = await conn.fetchrow(
                """
                UPDATE users
                   SET password_hash = $1,
                       must_change_password = TRUE,
                       is_active = TRUE,
                       tenant_id = $3::uuid
                 WHERE id = $2
                 RETURNING id, email, name, role, is_active, must_change_password, tenant_id, created_at
                """,
                auth.hash_password(temporary_password),
                user_id,
                tenant_id,
            )
            await conn.fetchval("SELECT omega_auth_revoke_user_tokens($1)", user_id)

    await _record(
        request,
        admin,
        "tenant_admin_temporary_password_issued",
        "user",
        str(user_id),
        {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "workspace_role": "tenant_admin",
        },
    )
    return {
        "user": _user_row(updated),
        "created": False,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "workspace_role": "tenant_admin",
        "temporary_password": temporary_password,
        "password_delivery": "one_time_response",
        "login_url": "/login",
    }
