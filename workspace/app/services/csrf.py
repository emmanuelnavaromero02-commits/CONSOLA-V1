from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import HTTPException, Request, Response

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_BODY_FIELD = "_csrf"
_TOKEN_BYTES = 32


def cookie_secure() -> bool:
    explicit = os.environ.get("COOKIE_SECURE")
    if explicit is not None:
        return explicit.lower() == "true"
    return os.environ.get("APP_ENV", "production").strip().lower() != "development"


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def set_csrf_cookie(response: Response, token: Optional[str] = None) -> str:
    value = token or generate_csrf_token()
    response.set_cookie(
        CSRF_COOKIE_NAME,
        value,
        httponly=False,
        samesite="lax",
        secure=cookie_secure(),
        path="/",
    )
    return value


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


def _provided_token(request: Request, body_token: Optional[str]) -> str:
    if body_token:
        return body_token
    return request.headers.get(CSRF_HEADER_NAME, "") or ""


def verify_csrf(request: Request, body_token: Optional[str] = None) -> bool:
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME, "") or ""
    if not cookie_value:
        return False
    provided = _provided_token(request, body_token)
    if not provided:
        return False
    return secrets.compare_digest(cookie_value, provided)


async def require_csrf(request: Request) -> None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return

    if request.headers.get(CSRF_HEADER_NAME):
        if verify_csrf(request, None):
            return
        raise HTTPException(status_code=403, detail="csrf token invalid or missing")

    body_token: Optional[str] = None
    try:
        payload = await request.json()
        if isinstance(payload, dict):
            v = payload.get(CSRF_BODY_FIELD)
            if isinstance(v, str):
                body_token = v
    except Exception:
        body_token = None

    if verify_csrf(request, body_token):
        return
    raise HTTPException(status_code=403, detail="csrf token invalid or missing")
