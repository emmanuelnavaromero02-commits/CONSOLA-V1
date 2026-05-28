"""Workspace CSRF protection using the same double-submit shape as Console."""
from __future__ import annotations

import secrets
import os

from fastapi import HTTPException, Request, Response

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_BODY_FIELD = "_csrf"
_TOKEN_BYTES = 32


def _cookie_secure() -> bool:
    raw = os.environ.get("COOKIE_SECURE")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod", "staging", "stage"}


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def set_csrf_cookie(response: Response, token: str | None = None) -> str:
    value = token or generate_csrf_token()
    response.set_cookie(
        CSRF_COOKIE_NAME,
        value,
        httponly=False,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )
    return value


def verify_csrf(request: Request, body_token: str | None = None) -> bool:
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME, "") or ""
    supplied = body_token or request.headers.get(CSRF_HEADER_NAME, "") or ""
    return bool(cookie_value and supplied and secrets.compare_digest(cookie_value, supplied))


async def require_csrf(request: Request) -> None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return
    if request.headers.get(CSRF_HEADER_NAME):
        if verify_csrf(request):
            return
        raise HTTPException(status_code=403, detail="csrf token invalid or missing")
    body_token = None
    try:
        payload = await request.json()
        if isinstance(payload, dict) and isinstance(payload.get(CSRF_BODY_FIELD), str):
            body_token = payload[CSRF_BODY_FIELD]
    except Exception:
        body_token = None
    if verify_csrf(request, body_token):
        return
    raise HTTPException(status_code=403, detail="csrf token invalid or missing")
