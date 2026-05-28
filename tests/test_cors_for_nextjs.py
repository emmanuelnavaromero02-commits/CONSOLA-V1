"""Sprint v1.44.3.2.2 R-Mac-3 — runtime CORS contract for Workspace.

Codex's Mac reproduced the failure mode:

  $ curl -i -H "Origin: http://localhost:8001" http://localhost:8000/auth/login
  → access-control-allow-credentials: true   ✓
  → access-control-allow-origin:    MISSING  ✗

Root cause: the v1.44.2 CORSMiddleware registration sat at line 186,
BEFORE security_headers, auth and RequestID were registered. Because
Starlette's ``add_middleware`` does ``user_middleware.insert(0, …)``,
each later registration shoved CORS deeper into the stack — by the
time the app was built, CORS was the INNERMOST wrapper. When the
OUTER auth_middleware short-circuited a request (401 / redirect /
preflight 405), the response never reached CORS, so Allow-Origin
was never added.

The fix moves CORSMiddleware registration to the END of the module,
AFTER RequestIDMiddleware, so it lands at ``user_middleware[0]`` and
becomes STRICTLY OUTERMOST. RequestID stays at ``user_middleware[1]``
(second-outermost), preserving the v1.42.1 invariant that
X-Request-ID lands on auth 401s.

These tests exercise the LIVE ASGI stack via Starlette's TestClient
(no docker, no real DB — the security_headers + auth middlewares
short-circuit before any DB access on these probes). They would have
caught the v1.44.3.2.2 R-Mac regression in CI rather than waiting
for Codex's Mac curl.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "console"))


@pytest.fixture(scope="module")
def client(monkeypatch_module):
    """TestClient with the live middleware stack mounted.

    R-Mac-3 review (Security/DevOps P2): all env mutations live in
    this fixture (via monkeypatch_module) so they're auto-restored
    after the module finishes. Earlier drafts mutated os.environ at
    import time, which persisted for the entire pytest session and
    could mask regressions in tests that assert on the un-set env
    state (e.g. test_internal_key_required_in_prod.py).
    """
    monkeypatch_module.setenv("APP_ENV", "test")
    monkeypatch_module.setenv("INTERNAL_API_KEY", "x" * 64)
    # Other tests in the suite legitimately set FIELD_ENCRYPTION_KEY
    # via monkeypatch; if it's already in the env we keep it, only
    # planting a placeholder when there's no value at all.
    if "FIELD_ENCRYPTION_KEY" not in os.environ:
        monkeypatch_module.setenv(
            "FIELD_ENCRYPTION_KEY",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=",
        )

    # Evict any stale ``app.*`` modules so the import below re-runs
    # against the current env. Other tests (cartridge harness) may
    # have populated sys.modules with their own ``app`` package.
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from starlette.testclient import TestClient

    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def monkeypatch_module():
    """Module-scoped monkeypatch. pytest's built-in ``monkeypatch``
    is function-scoped and can't be consumed by a module-scoped
    fixture, but ``MonkeyPatch`` itself supports manual lifecycle
    management."""
    mp = pytest.MonkeyPatch()
    try:
        yield mp
    finally:
        mp.undo()


# ── Preflight: OPTIONS must respond 200 with all three CORS headers ──


def test_options_preflight_returns_allow_origin(client):
    """Browser preflight for the login POST. The CORS middleware
    intercepts OPTIONS before any inner middleware runs and replies
    directly. Allow-Origin must echo the request Origin verbatim."""
    r = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:8001",
            "Access-Control-Request-Method":  "POST",
            "Access-Control-Request-Headers": "X-CSRF-Token,Content-Type",
        },
    )
    assert r.status_code == 200, (
        f"OPTIONS preflight to /auth/login returned {r.status_code} — "
        f"the CORS middleware short-circuit is not firing. Verify "
        f"CORSMiddleware is OUTERMOST in the stack."
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8001"
    assert "POST" in r.headers.get("access-control-allow-methods", "")
    allow_headers = r.headers.get("access-control-allow-headers", "")
    assert "X-CSRF-Token".lower() in allow_headers.lower()
    assert "Content-Type".lower() in allow_headers.lower()


def test_options_preflight_caches_for_an_hour(client):
    """max_age=3600 keeps the browser from re-OPTIONSing every XHR."""
    r = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:8001",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.headers.get("access-control-max-age") == "3600"


def test_options_preflight_for_disallowed_origin_returns_no_allow_origin(client):
    """An origin not in the allowlist must NOT receive Allow-Origin —
    otherwise CORS is wide open."""
    r = client.options(
        "/auth/login",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Either the preflight rejects or it succeeds without Allow-Origin.
    # Starlette's CORSMiddleware emits 400 on origin mismatch.
    assert r.headers.get("access-control-allow-origin") is None or \
           r.headers.get("access-control-allow-origin") != "http://evil.example.com"


# ── Simple requests carry Allow-Origin on the response ─────────────────


def test_get_login_includes_access_control_allow_origin(client):
    """GET /login from the Workspace origin must come back with
    Allow-Origin. This is the request the Workspace loginUser helper
    fires first to seed the csrf_token cookie."""
    r = client.get(
        "/login",
        headers={"Origin": "http://localhost:8001"},
    )
    # The page itself returns whatever it returns (200 HTML, 302
    # redirect, etc.) — the CORS header is what matters.
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8001", (
        f"GET /login Origin=:8001 returned headers: {dict(r.headers)} — "
        f"Allow-Origin missing. CORS middleware ordering may be wrong."
    )


def test_post_auth_login_with_origin_returns_allow_origin(client):
    """The actual POST /auth/login response (with whatever status —
    401/422 here because we don't seed credentials) must carry
    Allow-Origin. Without it the browser swallows the response."""
    r = client.post(
        "/auth/login",
        json={"email": "nobody@invalid.local", "password": "wrong"},
        headers={
            "Origin": "http://localhost:8001",
            "Content-Type": "application/json",
        },
    )
    # Status will be 4xx (no creds) but that's expected. We only
    # care about the CORS header round-trip.
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8001", (
        f"POST /auth/login Origin=:8001 missing Allow-Origin (status="
        f"{r.status_code}, headers={dict(r.headers)})"
    )


def test_credentials_true_in_response(client):
    """Allow-Credentials must echo true on EVERY CORS response so
    the browser accepts the Set-Cookie header from /auth/login."""
    r = client.get(
        "/login",
        headers={"Origin": "http://localhost:8001"},
    )
    assert r.headers.get("access-control-allow-credentials") == "true"


# ── Exposed headers + CSRF allow-list ───────────────────────────────────


def test_x_csrf_token_in_allow_headers(client):
    """Preflight Allow-Headers must include X-CSRF-Token so the
    browser permits the actual POST. The login flow attaches the
    header via lib/auth-flow.ts."""
    r = client.options(
        "/auth/login",
        headers={
            "Origin": "http://localhost:8001",
            "Access-Control-Request-Method":  "POST",
            "Access-Control-Request-Headers": "X-CSRF-Token",
        },
    )
    allow_headers = r.headers.get("access-control-allow-headers", "").lower()
    assert "x-csrf-token" in allow_headers


def test_expose_headers_includes_set_cookie_and_csrf(client):
    """expose_headers lets the browser READ Set-Cookie + X-CSRF-Token
    from credentialed cross-origin responses. Without it the
    Workspace console can't echo the cookie value back."""
    r = client.get(
        "/login",
        headers={"Origin": "http://localhost:8001"},
    )
    expose = r.headers.get("access-control-expose-headers", "").lower()
    assert "set-cookie" in expose, (
        "Allow-Expose-Headers must include Set-Cookie — without it the "
        "browser hides the csrf_token + mod_session cookies from JS"
    )
    assert "x-csrf-token" in expose


# ── Middleware ordering invariant ──────────────────────────────────────


def test_cors_middleware_is_outermost(client):
    """REGRESSION GUARD: CORSMiddleware MUST end up OUTERMOST in the
    ASGI stack. The pre-R-Mac-3 bug was that CORS was registered
    FIRST (before security_headers/auth/RequestID), which let
    auth_middleware short-circuit responses without ever reaching
    CORS — so 401s came back with no Allow-Origin.

    Starlette's `add_middleware` does `user_middleware.insert(0, …)`,
    so the most-recently-registered middleware lives at index 0 and
    becomes the OUTERMOST wrapper. Registering CORS LAST in main.py
    therefore puts it at user_middleware[0].
    """
    from starlette.middleware.cors import CORSMiddleware
    from app.main import app

    outermost = app.user_middleware[0]
    assert outermost.cls is CORSMiddleware, (
        f"CORSMiddleware must be OUTERMOST (user_middleware[0]). "
        f"Current outermost middleware: {outermost.cls.__name__}. "
        f"Full registration order (outermost → innermost): "
        f"{[m.cls.__name__ for m in app.user_middleware]}"
    )


def test_middleware_stack_full_snapshot(client):
    """R-Mac-3 review (DevOps P2): pinning only [0] and [1] lets a
    future refactor slip a new middleware in at index 2 without
    anyone noticing. Snapshot the full ordering so any reordering
    forces a test update + reviewer awareness."""
    from app.main import app

    actual = [m.cls.__name__ for m in app.user_middleware]
    expected = [
        "CORSMiddleware",        # OUTERMOST — must see every request, incl. preflight
        "RequestIDMiddleware",   # X-Request-ID on auth 401s (v1.42.1 invariant)
        "BaseHTTPMiddleware",    # auth_middleware (@app.middleware decorator)
        "BaseHTTPMiddleware",    # security_headers_middleware (@app.middleware decorator)
    ]
    assert actual == expected, (
        f"Middleware ordering changed unexpectedly.\n"
        f"  expected (outermost → innermost): {expected}\n"
        f"  actual:                          {actual}\n"
        f"If this change was intentional, update this test AND verify "
        f"that:\n"
        f"  - CORSMiddleware is still OUTERMOST (Allow-Origin on 401s)\n"
        f"  - RequestIDMiddleware is still SECOND (X-Request-ID on 401s)\n"
        f"  - auth_middleware still wraps the inner stack"
    )


def test_request_id_middleware_remains_second_outermost(client):
    """The v1.42.1 invariant — X-Request-ID on auth 401s — still
    holds because RequestIDMiddleware sits one layer inside CORS
    (user_middleware[1])."""
    from app.middleware.request_id import RequestIDMiddleware
    from app.main import app

    second = app.user_middleware[1]
    assert second.cls is RequestIDMiddleware, (
        f"RequestIDMiddleware must be SECOND-outermost "
        f"(user_middleware[1]). Found: {second.cls.__name__}. "
        f"Full order (outermost → innermost): "
        f"{[m.cls.__name__ for m in app.user_middleware]}"
    )
