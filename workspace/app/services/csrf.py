"""CSRF — Double Submit Cookie (DSC) helper for workspace mutations.

Mirrors console/app/services/csrf.py. The ``csrf_token`` cookie is issued
by console at login and — like the ``mod_session`` cookie — is scoped by
domain (not port), so it flows to the workspace container too. Workspace
only needs to VERIFY it on state-changing requests; login/token rotation
stays in console.

The verification rule is simple and stateless:
  - There MUST be a ``csrf_token`` cookie on the request.
  - The same value MUST arrive either as an ``X-CSRF-Token`` header or as
    ``_csrf`` in the JSON body.
  - The two values MUST match exactly (constant-time compare).
"""
from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import HTTPException, Request, Response

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_BODY_FIELD = "_csrf"
# Bytes-of-entropy in the random token. 32 bytes → ~43-char URL-safe string.
_TOKEN_BYTES = 32


def cookie_secure() -> bool:
    """Should the cookie be marked Secure? Matches console/session policy:
    explicit COOKIE_SECURE wins, otherwise default to secure unless
    APP_ENV is development (local http://localhost can't take Secure)."""
    explicit = os.environ.get("COOKIE_SECURE")
    if explicit is not None:
        return explicit.lower() == "true"
    return os.environ.get("APP_ENV", "production").strip().lower() != "development"


def generate_csrf_token() -> str:
    """Cryptographically random URL-safe token suitable for cookies."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def set_csrf_cookie(response: Response, token: Optional[str] = None) -> str:
    """Attach ``csrf_token`` to the response and return the value used.

    NOT HttpOnly — the browser MUST be able to read it and echo it back.
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
    the X-CSRF-Token header. Empty string if neither was provided."""
    if body_token:
        return body_token
    return request.headers.get(CSRF_HEADER_NAME, "") or ""


def verify_csrf(request: Request, body_token: Optional[str] = None) -> bool:
    """Return True iff the cookie and the caller-supplied token match.

    Empty cookie or empty supplied value → False (default-deny)."""
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME, "") or ""
    if not cookie_value:
        return False
    provided = _provided_token(request, body_token)
    if not provided:
        return False
    # Constant-time compare so timing attacks can't shave off the token
    # one character at a time.
    return secrets.compare_digest(cookie_value, provided)


async def require_csrf(request: Request) -> None:
    """FastAPI dependency. 403 if the CSRF check fails.

    Reads the body lazily (only when the header is absent) so handlers
    that already declare ``body: dict`` keep working unchanged.

    Bearer-authed requests are exempt: CSRF only defends against requests
    where the browser AUTO-attaches credentials (cookie sessions). A
    bearer token requires explicit JS to set the header, so a cross-origin
    form post can't impersonate the user.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return

    if request.headers.get(CSRF_HEADER_NAME):
        # Fast path: header present, no need to peek at the body.
        if verify_csrf(request, None):
            return
        raise HTTPException(status_code=403, detail="csrf token invalid or missing")

    # Header missing: try the JSON body. Read once; FastAPI hands the
    # already-buffered bytes back to the route handler via ``body: dict``.
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
