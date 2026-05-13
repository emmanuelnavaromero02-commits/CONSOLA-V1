"""CSRF — Double Submit Cookie (DSC) helper for sensitive auth endpoints.

The cookie is intentionally NOT HttpOnly (the browser side must read it
and echo it back as a header / body field). Same-Origin Lax stays
on the session cookies and on this token; we layer on top of those.

The verification rule is simple and stateless:
  - There MUST be a `csrf_token` cookie on the request.
  - The same value MUST arrive either as an `X-CSRF-Token` header or as
    `_csrf` in the JSON body.
  - The two values MUST match exactly (constant-time compare).

Token rotation: callers rotate after login / password change / logout to
limit the window for replay if the token leaks via some XSS sink.
"""
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import HTTPException, Request, Response

from app.services.auth import cookie_secure

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_BODY_FIELD = "_csrf"
# Bytes-of-entropy in the random token.  32 bytes → ~43-char URL-safe string.
_TOKEN_BYTES = 32


def generate_csrf_token() -> str:
    """Cryptographically random URL-safe token suitable for cookies."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def set_csrf_cookie(response: Response, token: Optional[str] = None) -> str:
    """Attach `csrf_token` to the response and return the value used.

    NOT HttpOnly — the browser MUST be able to read it and echo it back.
    Same-Site Lax matches the rest of the auth cookies on this app.
    """
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
    """Pick the caller-supplied token: explicit body field wins; otherwise
    the X-CSRF-Token header. Empty string if neither was provided.
    """
    if body_token:
        return body_token
    return request.headers.get(CSRF_HEADER_NAME, "") or ""


def verify_csrf(request: Request, body_token: Optional[str] = None) -> bool:
    """Return True iff the cookie and the caller-supplied token match.

    Empty cookie or empty supplied value → False (default-deny).
    """
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME, "") or ""
    if not cookie_value:
        return False
    provided = _provided_token(request, body_token)
    if not provided:
        return False
    # Constant-time compare so timing attacks can't shave off the first
    # few characters one round at a time.
    return secrets.compare_digest(cookie_value, provided)


async def require_csrf(request: Request) -> None:
    """FastAPI dependency.  403 if CSRF check fails.

    Reads the body lazily (only when the header is absent) so consumers
    that already declare `body: dict` keep working unchanged.
    """
    if request.headers.get(CSRF_HEADER_NAME):
        # Fast path: header is present, no need to peek at the body.
        if verify_csrf(request, None):
            return
        raise HTTPException(status_code=403, detail="csrf token invalid or missing")

    # Header missing: try the JSON body. Read once; FastAPI will hand the
    # already-buffered bytes back to the route handler via `body: dict`.
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
