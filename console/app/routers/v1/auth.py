from __future__ import annotations

from fastapi import APIRouter
import types

import app.main as _console_main

# Import the current console runtime namespace, including private helper
# functions used by legacy handlers. Handlers are rebound to app.main's
# namespace before registration so existing tests and monkeypatches that
# patch app.main.<helper> continue to affect the handler at runtime.
globals().update(_console_main.__dict__)
router = APIRouter()


def _bind_to_main(fn):
    rebound = types.FunctionType(
        fn.__code__,
        _console_main.__dict__,
        fn.__name__,
        fn.__defaults__,
        fn.__closure__,
    )
    rebound.__kwdefaults__ = fn.__kwdefaults__
    rebound.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    rebound.__dict__.update(getattr(fn, "__dict__", {}))
    rebound.__doc__ = fn.__doc__
    rebound.__module__ = _console_main.__name__
    _console_main.__dict__[fn.__name__] = rebound
    return rebound

# /login
@router.get("/login")
@_bind_to_main
async def login_page(request: Request):
    # Seed the CSRF cookie so the page's POST /auth/login fetch can echo
    # it back without an extra round-trip. The cookie is re-issued on
    # every GET /login (cheap, and avoids a stale-token edge case when
    # the user keeps the tab open across logout/login).
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "login/index.html")

# /auth/login
@router.post("/auth/login", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def auth_login(request: Request, body: dict):
    return await _login_response(request, body)

# /auth/refresh
@router.post("/auth/refresh", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def auth_refresh(request: Request):
    refresh_token = request.cookies.get(_auth.REFRESH_COOKIE_NAME)
    # Hash a prefix of the token into the subject so per-token buckets isolate
    # spamming attempts without writing the secret material to Redis keys.
    subject = _auth.hash_refresh_token(refresh_token or "")[:16]
    await _rate_limit(request, "/auth/refresh", subject)
    rotated = await _auth.rotate_refresh_token(refresh_token)
    if not rotated:
        resp = JSONResponse({"detail": "invalid refresh token"}, status_code=401)
        resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
        return resp
    user, new_refresh_token, refresh_expires = rotated

    if not user:
        resp = JSONResponse({"detail": "invalid refresh token"}, status_code=401)
        resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
        return resp

    access_token = _access_token_for_user(user)
    resp = JSONResponse({"access_token": access_token, "token_type": "bearer"})
    _set_refresh_cookie(resp, new_refresh_token, refresh_expires)
    return resp

# /auth/logout
@router.post("/auth/logout", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def auth_logout(request: Request):
    token = request.cookies.get(_auth.COOKIE_NAME)
    if token:
        await _auth.destroy_session(token)
    refresh_token = request.cookies.get(_auth.REFRESH_COOKIE_NAME)
    if refresh_token:
        await _auth.revoke_refresh_token(refresh_token)

    # Sprint v1.10 — blacklist the bearer access token's jti so a stolen
    # JWT can't keep authenticating up to its exp. Silent if the caller
    # is cookie-only (most of our UI) or the token is already invalid.
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        bearer = auth_header.split(" ", 1)[1].strip()
        try:
            claims = decode_access_token(bearer)
            jti = claims.get("jti")
            exp = claims.get("exp")
            if jti and exp is not None:
                from app.services.jwt_blacklist import get_blacklist
                await get_blacklist().revoke(jti, int(exp))
        except Exception:
            # JWT already expired / malformed / signature mismatch —
            # nothing to revoke, nothing to do.
            pass

    resp = JSONResponse({"logged_out": True})
    resp.delete_cookie(_auth.COOKIE_NAME, path="/")
    resp.delete_cookie(_auth.REFRESH_COOKIE_NAME, path="/")
    clear_csrf_cookie(resp)
    return resp

# /auth/me
@router.get("/auth/me")
@_bind_to_main
async def auth_me(request: Request):
    return {"user": _user_payload(current_user(request))}

# /auth/me-jwt
@router.get("/auth/me-jwt")
@_bind_to_main
async def auth_me_jwt(authorization: str | None = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid authorization header")
    try:
        # Sprint v1.10: blacklist-aware verification.
        claims = await verify_access_token_async(token)
    except JWTAuthError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    return {"claims": {
        "sub": claims["sub"],
        "email": claims["email"],
        "role": claims["role"],
        "iat": claims["iat"],
        "exp": claims["exp"],
        "jti": claims["jti"],
    }}

# /auth/me-current
@router.get("/auth/me-current")
@_bind_to_main
async def auth_me_current(user: dict = Depends(get_current_user_dependency)):
    return {"user": _user_payload(user)}

# /activate
@router.get("/activate")
@_bind_to_main
async def viewer_activate():
    response = FileResponse(STATIC / "activate.html")
    set_csrf_cookie(response)
    return response

# /auth/activate
@router.post("/auth/activate", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def auth_activate(request: Request, body: dict):
    token = (body.get("token") or "").strip()
    pw    = body.get("new_password") or ""
    await _rate_limit(request, "/auth/activate", token[:16])
    if not token or not pw:
        raise HTTPException(400, "token and new_password are required")
    _validate_password_or_400(pw, field="new_password")
    info = await _tokens.consume_lookup(token, "invite")
    if not info:
        raise HTTPException(400, "token inválido o expirado")
    user = await _auth.activate_user(info["user_id"], pw)
    if not user:
        raise HTTPException(400, "el password debe tener al menos 12 caracteres")
    ip = request.client.host if request.client else None
    sess_token, expires = await _auth.create_session(user["id"], ip=ip)
    resp = JSONResponse({"activated": True, "user": user})
    _set_session_cookie(resp, sess_token, expires)
    return resp

# /auth/activate/info
@router.get("/auth/activate/info")
@_bind_to_main
async def auth_activate_info(token: str = ""):
    """Public probe so the activate page can show the user's email/name."""
    info = await _tokens.lookup(token, "invite")
    if not info:
        return {"valid": False}
    return {"valid": True, "email": info["email"], "name": info["name"]}

# /forgot-password
@router.get("/forgot-password")
@_bind_to_main
async def viewer_forgot():
    # Seed CSRF cookie so the form's POST /auth/forgot-password fetch can
    # echo it back without a prior visit to /login.
    response = FileResponse(STATIC / "forgot_password.html")
    set_csrf_cookie(response)
    return response

# /auth/forgot-password
@router.post("/auth/forgot-password", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def auth_forgot(request: Request, body: dict):
    """Generic OK regardless of whether the email exists (avoids enum oracle)."""
    email = (body.get("email") or "").strip().lower()
    await _rate_limit(request, "/auth/forgot-password", email)
    if email:
        u = await _auth.get_user_by_email(email)
        if u and u.get("is_active"):
            tok, _ = await _tokens.create(u["id"], "reset")
            subject, html = _email.render_password_reset(u.get("name"), _reset_link(tok), RESET_TTL_HOURS)
            await _email.send_email(u["email"], subject, html)
    return {"sent": True}

# /reset-password
@router.get("/reset-password")
@_bind_to_main
async def viewer_reset():
    response = FileResponse(STATIC / "reset_password.html")
    set_csrf_cookie(response)
    return response

# /auth/reset/info
@router.get("/auth/reset/info")
@_bind_to_main
async def auth_reset_info(token: str = ""):
    info = await _tokens.lookup(token, "reset")
    if not info:
        return {"valid": False}
    return {"valid": True, "email": info["email"]}

# /auth/reset-password
@router.post("/auth/reset-password", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def auth_reset(request: Request, body: dict):
    token = (body.get("token") or "").strip()
    pw    = body.get("new_password") or ""
    await _rate_limit(request, "/auth/reset-password", token[:16])
    if not token or not pw:
        raise HTTPException(400, "token and new_password are required")
    _validate_password_or_400(pw, field="new_password")
    info = await _tokens.consume_lookup(token, "reset")
    if not info:
        raise HTTPException(400, "token inválido o expirado")
    user = await _auth.reset_password_to(info["user_id"], pw)
    if not user:
        raise HTTPException(400, f"el password debe tener al menos {_password_min_length()} caracteres")
    ip = request.client.host if request.client else None
    sess_token, expires = await _auth.create_session(user["id"], ip=ip)
    resp = JSONResponse({"reset": True, "user": user})
    _set_session_cookie(resp, sess_token, expires)
    # Rotate CSRF after the password reset so any leaked pre-reset token
    # cannot replay.
    set_csrf_cookie(resp)
    return resp
