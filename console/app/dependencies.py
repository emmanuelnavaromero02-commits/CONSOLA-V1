import os
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request

from app.services import auth as _auth
from app.services.jwt_auth import JWTAuthError, verify_access_token_async


ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_WORKSPACE_ADMIN = "workspace_admin"
ROLE_ANALYST = "analyst"
ROLE_VIEWER = "viewer"
_GLOBAL_ADMIN_ROLES = {"admin", "owner", "super_admin"}


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


async def _user_from_jwt(token: str) -> dict:
    try:
        # Sprint v1.10: switched from sync decode_access_token to the
        # async variant so the Redis blacklist check runs on every
        # JWT-authenticated request. Same exception type.
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


async def _explicit_workspace_memberships(p, user_id: int) -> list[dict]:
    """Workspaces the user has an explicit role row for (user_workspace_roles)."""
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


async def _tenant_ids_for_user(p, user_id: int) -> set[str]:
    """Tenants a non-global user is attached to: the tenants of the workspaces
    they are an explicit member of, plus their own users.tenant_id if set.

    This is what scopes a tenant_admin: they administer every workspace under
    any tenant they belong to, even workspaces they are not explicitly in.
    """
    rows = await p.fetch(
        """SELECT DISTINCT w.tenant_id::text AS tenant_id
             FROM user_workspace_roles uwr
             JOIN workspaces w ON w.id = uwr.workspace_id
            WHERE uwr.user_id = $1""",
        user_id,
    )
    tenant_ids = {r["tenant_id"] for r in rows if r["tenant_id"]}
    own = await p.fetchval("SELECT tenant_id::text FROM users WHERE id = $1", user_id)
    if own:
        tenant_ids.add(own)
    return tenant_ids


async def _workspace_memberships(user_id: int) -> list[dict]:
    """Workspaces a user can act in, role-aware.

    * Global admins (owner/super_admin/admin) → every workspace across every
      tenant. Lets a super_admin switch tenant/workspace freely.
    * tenant_admin → every workspace under any tenant they belong to.
    * Everyone else → only their explicit user_workspace_roles memberships.
    """
    p = await _auth.pool()
    role = await p.fetchval("SELECT role FROM users WHERE id = $1", user_id)

    if role in _GLOBAL_ADMIN_ROLES:
        rows = await p.fetch(
            """SELECT w.id::text AS workspace_id,
                      w.name AS workspace_name,
                      t.id::text AS tenant_id,
                      t.name AS tenant_name
                 FROM workspaces w
                 JOIN tenants t ON t.id = w.tenant_id
                ORDER BY t.name ASC, w.created_at ASC, w.name ASC"""
        )
        return [{**dict(r), "workspace_role": role} for r in rows]

    if role == "tenant_admin":
        tenant_ids = await _tenant_ids_for_user(p, user_id)
        if tenant_ids:
            rows = await p.fetch(
                """SELECT w.id::text AS workspace_id,
                          w.name AS workspace_name,
                          t.id::text AS tenant_id,
                          t.name AS tenant_name
                     FROM workspaces w
                     JOIN tenants t ON t.id = w.tenant_id
                    WHERE w.tenant_id = ANY($1::uuid[])
                    ORDER BY t.name ASC, w.created_at ASC, w.name ASC""",
                list(tenant_ids),
            )
            if rows:
                return [{**dict(r), "workspace_role": "tenant_admin"} for r in rows]

    return await _explicit_workspace_memberships(p, user_id)


async def _workspace_cartridges(workspace_id: str | None, user_id: int | None = None) -> list[str]:
    if not workspace_id:
        return []
    p = await _auth.pool()
    if not hasattr(p, "fetchval"):
        return []
    has_entitlements = await p.fetchval("SELECT to_regclass('public.tenant_entitlements')")
    if has_entitlements:
        has_user_overrides = await p.fetchval("SELECT to_regclass('public.user_cartridge_overrides')")
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
        rows = await p.fetch(
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
        rows = await p.fetch(
            """SELECT DISTINCT cartridge
                 FROM datasets
                WHERE workspace_id = $1
                  AND cartridge IS NOT NULL
                  AND cartridge <> ''
                ORDER BY cartridge""",
            workspace_id,
        )
    cartridges = [str(row["cartridge"]) for row in rows if row["cartridge"]]
    # The platform cartridge is infrastructure (shared system DAGs / meta tools),
    # not an opt-in data product — it is active by default for every workspace.
    if "platform" not in cartridges:
        cartridges.append("platform")
    return cartridges


async def _with_workspace_context(user: dict, requested_workspace_id: str | None) -> dict:
    workspaces = await _workspace_memberships(user["id"])
    if not workspaces:
        raise HTTPException(status_code=403, detail="user has no assigned workspace")

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
    requested_workspace_id = (request.headers.get("x-workspace-id") or "").strip() or None
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
        role = user.get("workspace_role") or user.get("role")
        if role != role_name:
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
