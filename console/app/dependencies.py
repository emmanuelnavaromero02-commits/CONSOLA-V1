import os
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request

from app.services import auth as _auth
from app.services.db_scope import scoped_db
from app.services.jwt_auth import JWTAuthError, verify_access_token_async


ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_WORKSPACE_ADMIN = "workspace_admin"
ROLE_ANALYST = "analyst"
ROLE_VIEWER = "viewer"
_GLOBAL_ADMIN_ROLES = {"admin", "owner", "super_admin"}
ACTIVE_WORKSPACE_COOKIE = "omega_active_workspace_id"


def _is_production_env() -> bool:
    value = os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT") or "production"
    return value.strip().lower() in {"prod", "production", "staging"}


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid authorization header")
    return token


def requested_workspace_id_from_request(request: Request) -> str | None:
    return (
        request.headers.get("x-workspace-id")
        or request.cookies.get(ACTIVE_WORKSPACE_COOKIE)
        or ""
    ).strip() or None


async def _user_from_jwt(token: str) -> dict:
    try:
        claims = await verify_access_token_async(token)
    except JWTAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        user_id = int(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="invalid token subject") from exc

    user = await _auth.get_user_by_id(user_id)
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=401, detail="user not found or inactive")
    if claims.get("email") and user.get("email") != claims["email"]:
        raise HTTPException(status_code=401, detail="token user mismatch")
    return user


async def _workspace_memberships(user_id: int) -> list[dict]:
    p = await _auth.pool()
    rows = await p.fetch(
        """SELECT w.id::text AS workspace_id,
                  w.name AS workspace_name,
                  t.id::text AS tenant_id,
                  t.name AS tenant_name,
                  r.name AS workspace_role
             FROM user_workspace_roles uwr
             JOIN workspaces w ON w.id = uwr.workspace_id
             JOIN tenants t ON t.id = w.tenant_id
             JOIN roles r ON r.id = uwr.role_id
            WHERE uwr.user_id = $1
            ORDER BY w.created_at ASC, w.name ASC, r.name ASC""",
        user_id,
    )
    return [dict(row) for row in rows]


def _is_global_admin_user(user: dict | None) -> bool:
    return (user or {}).get("role") in _GLOBAL_ADMIN_ROLES


async def _all_workspace_options() -> list[dict]:
    p = await _auth.pool()
    rows = await p.fetch(
        """SELECT w.id::text AS workspace_id,
                  w.name AS workspace_name,
                  t.id::text AS tenant_id,
                  t.name AS tenant_name,
                  'workspace_admin'::text AS workspace_role
             FROM workspaces w
             JOIN tenants t ON t.id = w.tenant_id
            ORDER BY lower(t.name), w.created_at ASC, lower(w.name) ASC"""
    )
    return [dict(row) for row in rows]


async def _workspace_access_options(user: dict) -> list[dict]:
    if _is_global_admin_user(user):
        return await _all_workspace_options()
    return await _workspace_memberships(user["id"])


async def _workspace_cartridges(workspace_id: str | None, user_id: int | None = None) -> list[str]:
    if not workspace_id:
        return []
    p = await _auth.pool()
    if not hasattr(p, "fetchval"):
        return []
    tenant_id = await p.fetchval(
        "SELECT tenant_id::text FROM workspaces WHERE id = $1::uuid",
        workspace_id,
    )
    conn_ctx = scoped_db(p, tenant_id, workspace_id) if tenant_id else None
    conn = None
    if conn_ctx is not None:
        conn = await conn_ctx.__aenter__()
    try:
        db = conn or p
        has_entitlements = await db.fetchval("SELECT to_regclass('public.tenant_entitlements')")
        if has_entitlements:
            has_user_overrides = await db.fetchval("SELECT to_regclass('public.user_cartridge_overrides')")
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
            rows = await db.fetch(
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
            rows = await db.fetch(
                """SELECT DISTINCT cartridge
                     FROM datasets
                    WHERE workspace_id = $1
                      AND cartridge IS NOT NULL
                      AND cartridge <> ''
                    ORDER BY cartridge""",
                workspace_id,
            )
    finally:
        if conn_ctx is not None:
            await conn_ctx.__aexit__(None, None, None)
    return [str(row["cartridge"]) for row in rows if row["cartridge"]]


async def _with_workspace_context(user: dict, requested_workspace_id: str | None) -> dict:
    workspaces = await _workspace_access_options(user)
    if not workspaces:
        detail = (
            "no workspaces configured"
            if _is_global_admin_user(user)
            else "user has no assigned workspace"
        )
        raise HTTPException(status_code=403, detail=detail)

    if requested_workspace_id:
        active = next((w for w in workspaces if w["workspace_id"] == requested_workspace_id), None)
        if not active:
            raise HTTPException(status_code=403, detail="workspace access forbidden")
    else:
        active = workspaces[0]

    enriched = dict(user)
    enriched.update({
        "active_workspace_id": active["workspace_id"],
        "active_tenant_id": active["tenant_id"],
        "workspace_role": active["workspace_role"],
        "workspaces": workspaces,
        "allowed_cartridges": await _workspace_cartridges(active["workspace_id"], user_id=user["id"]),
    })
    return enriched


async def get_current_user(request: Request) -> dict:
    requested_workspace_id = requested_workspace_id_from_request(request)
    token = _bearer_token(request)
    if token:
        user = await _user_from_jwt(token)
        return await _with_workspace_context(user, requested_workspace_id)

    session_token = request.cookies.get(_auth.COOKIE_NAME)
    user = await _auth.get_session_user(session_token) if session_token else None
    if not user:
        raise HTTPException(status_code=401, detail="authentication required")
    return await _with_workspace_context(user, requested_workspace_id)


require_authenticated = get_current_user


async def get_current_global_user(request: Request) -> dict:
    token = _bearer_token(request)
    if token:
        return await _user_from_jwt(token)

    session_token = request.cookies.get(_auth.COOKIE_NAME)
    user = await _auth.get_session_user(session_token) if session_token else None
    if not user:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def require_role(role_name: str) -> Callable:
    async def dependency(user: dict = Depends(get_current_user)) -> dict:
        from app.services.permissions import workspace_role

        if user.get("role") != role_name and workspace_role(user) != role_name:
            raise HTTPException(status_code=403, detail=f"{role_name} role required")
        return user

    return dependency


def require_any_role(*role_names: str) -> Callable:
    allowed = set(role_names)

    async def dependency(user: dict = Depends(get_current_user)) -> dict:
        roles = {role for role in (user.get("workspace_role"), user.get("role")) if role}
        if not roles.intersection(allowed):
            raise HTTPException(status_code=403, detail="required role missing")
        return user

    return dependency


def require_global_any_role(*role_names: str) -> Callable:
    allowed = set(role_names)

    async def dependency(user: dict = Depends(get_current_global_user)) -> dict:
        role = user.get("role")
        if role not in allowed:
            raise HTTPException(status_code=403, detail="global role required")
        return user

    return dependency


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)

def require_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "authentication required")
    return u

def require_admin(request: Request) -> dict:
    u = require_user(request)
    if u.get("role") not in _GLOBAL_ADMIN_ROLES:
        raise HTTPException(403, "admin role required")
    return u
