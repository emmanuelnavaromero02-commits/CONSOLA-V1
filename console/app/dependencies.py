from collections.abc import Callable

from fastapi import Depends, HTTPException, Request

from app.services import auth as _auth
from app.services.jwt_auth import JWTAuthError, decode_access_token


ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_WORKSPACE_ADMIN = "workspace_admin"
ROLE_ANALYST = "analyst"
ROLE_VIEWER = "viewer"


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
        claims = decode_access_token(token)
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
        role = user.get("workspace_role") or user.get("role")
        if role not in allowed:
            raise HTTPException(status_code=403, detail="required role missing")
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
    if u.get("role") != "admin":
        raise HTTPException(403, "admin role required")
    return u
