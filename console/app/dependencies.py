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


async def get_current_user(request: Request) -> dict:
    token = _bearer_token(request)
    if token:
        return await _user_from_jwt(token)

    session_token = request.cookies.get(_auth.COOKIE_NAME)
    user = await _auth.get_session_user(session_token) if session_token else None
    if not user:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


require_authenticated = get_current_user


def require_role(role_name: str) -> Callable:
    async def dependency(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") != role_name:
            raise HTTPException(status_code=403, detail=f"{role_name} role required")
        return user

    return dependency


def require_any_role(*role_names: str) -> Callable:
    allowed = set(role_names)

    async def dependency(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in allowed:
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
