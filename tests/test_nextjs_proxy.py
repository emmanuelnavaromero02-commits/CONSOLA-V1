"""Sprint v1.44.3.2.2 R-Mac-4 — Next.js same-origin proxy contract.

After three CORS firefights the strategy pivoted: the Next.js
app at :3000 now serves the API surface as itself and proxies
to FastAPI server-to-server. Same-origin from the browser →
no preflight, no Allow-Origin negotiation, no Set-Cookie
domain rewriting.

This file pins TWO classes of contracts:

  1. STATIC — the proxy source files exist with the right
     shape, the route handlers export every HTTP verb, the
     middleware allow-list covers the proxy paths, and the
     interlocking constants (BACKEND_INTERNAL_URL, base URL,
     readCookie helper, …) are all where the runtime expects
     them. These run on every commit, no docker needed.

  2. LIVE — actual HTTP round-trips against
     http://localhost:3000 verifying GET / POST / cookies /
     CSRF header / 200-on-login behave end-to-end. These
     SKIP when the stack isn't up — they're the wire-level
     truth, but CI without docker just checks the static
     contracts.
"""
from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
PROXY_LIB    = REPO / "console-next/src/lib/proxy.ts"
API_ROUTE    = REPO / "console-next/src/app/api/[...path]/route.ts"
AUTH_ROUTE   = REPO / "console-next/src/app/auth/[...path]/route.ts"
LOGIN_PROXY  = REPO / "console-next/src/app/login-proxy/route.ts"
MIDDLEWARE   = REPO / "console-next/src/middleware.ts"
API_TS       = REPO / "console-next/src/lib/api.ts"
AUTH_FLOW    = REPO / "console-next/src/lib/auth-flow.ts"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── Static contracts ─────────────────────────────────────────────────────


def test_proxy_helper_exists_and_exports_BACKEND_URL_and_proxyTo():
    assert PROXY_LIB.exists(), "console-next/src/lib/proxy.ts is missing"
    src = _read(PROXY_LIB)
    assert "export const BACKEND_URL" in src, (
        "proxy.ts must export BACKEND_URL so the route handlers can "
        "build target URLs against it"
    )
    assert "export async function proxyTo" in src, (
        "proxy.ts must export proxyTo(request, options) as the shared "
        "forwarder used by every route handler"
    )
    # The helper must drop hop-by-hop headers — `host` would point
    # the upstream at :3000 instead of the backend.
    assert '"host"' in src, "proxyTo must strip the host header"


def test_proxy_helper_handles_multiple_set_cookie_headers():
    """FastAPI emits separate Set-Cookie headers for csrf_token,
    mod_session and refresh_token. `Headers.forEach` collapses
    them with ", " which breaks the browser's cookie parser, so
    the helper must use `getSetCookie()` + `append`."""
    src = _read(PROXY_LIB)
    assert "getSetCookie" in src, (
        "proxy.ts must use Headers.getSetCookie() so multiple Set-Cookie "
        "headers survive the forwarding round-trip"
    )
    assert 'responseHeaders.append("set-cookie"' in src


def test_api_catch_all_route_exists_and_targets_backend_api_prefix():
    """The catch-all proxy strips the literal `/api/` from the URL
    pattern, so we must put it back when building the upstream
    target — FastAPI routes are mounted under /api/..."""
    assert API_ROUTE.exists(), "app/api/[...path]/route.ts is missing"
    src = _read(API_ROUTE)
    assert "${BACKEND_URL}/api/" in src, (
        "api/[...path]/route.ts must target ${BACKEND_URL}/api/${path} — "
        "the literal /api/ prefix was stripped by Next.js routing"
    )


def test_auth_catch_all_route_exists_and_targets_backend_auth_prefix():
    assert AUTH_ROUTE.exists(), "app/auth/[...path]/route.ts is missing"
    src = _read(AUTH_ROUTE)
    assert "${BACKEND_URL}/auth/" in src


@pytest.mark.parametrize("verb", ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_api_route_exports_every_http_verb(verb):
    """Each route handler must export GET, POST, PUT, PATCH, DELETE
    and OPTIONS bound to the same proxy. A missing verb means that
    method 405s at the Next.js layer before it ever reaches
    FastAPI."""
    src = _read(API_ROUTE)
    pattern = rf"handler as {verb}"
    assert re.search(pattern, src), (
        f"api/[...path]/route.ts must export {verb} as the proxy handler"
    )


@pytest.mark.parametrize("verb", ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_auth_route_exports_every_http_verb(verb):
    src = _read(AUTH_ROUTE)
    pattern = rf"handler as {verb}"
    assert re.search(pattern, src), (
        f"auth/[...path]/route.ts must export {verb} as the proxy handler"
    )


def test_login_proxy_route_exists_and_is_get_only():
    """/login-proxy is named differently from /login so it doesn't
    shadow the Next.js client-rendered login page. Only GET is
    needed — the browser just wants the Set-Cookie side-effect."""
    assert LOGIN_PROXY.exists(), "app/login-proxy/route.ts is missing"
    src = _read(LOGIN_PROXY)
    assert "export async function GET" in src
    assert "${BACKEND_URL}/login" in src


def test_middleware_allows_proxy_paths_without_session_cookie():
    """The auth-cookie middleware must NOT redirect /auth/*
    or /login-proxy to /login — those paths are literally how
    you GET a session cookie."""
    src = _read(MIDDLEWARE)
    assert '"/login-proxy"' in src, (
        "middleware.ts PUBLIC_PATHS must include /login-proxy"
    )
    assert '"/auth/"' in src, (
        "middleware.ts PUBLIC_PREFIXES must include /auth/ so the "
        "proxy catch-all can answer POST /auth/login without an "
        "existing session"
    )


def test_api_ts_browser_baseURL_is_empty_for_same_origin():
    """The browser axios instance must NOT reference the backend's
    public origin. Empty baseURL → relative URLs → same-origin
    fetch → Next.js proxy → FastAPI. Anything else re-introduces
    CORS as a failure surface."""
    src = _read(API_TS)
    assert "NEXT_PUBLIC_API_BASE" not in src
    assert "NEXT_PUBLIC_BACKEND_URL" not in src


def test_auth_flow_uses_relative_proxy_paths():
    """loginUser must hit /login-proxy + /auth/login on the SAME
    ORIGIN, not a hard-coded BACKEND_URL."""
    src = _read(AUTH_FLOW)
    assert 'fetch("/login-proxy"' in src
    assert 'fetch("/auth/login"' in src
    assert "NEXT_PUBLIC_API_BASE" not in src
    assert "NEXT_PUBLIC_BACKEND_URL" not in src


# ── Live HTTP contracts (skip when stack is down) ────────────────────────


FRONTEND_URL = "http://localhost:3000"


def _next_is_up() -> bool:
    """Best-effort liveness probe. We TCP-connect to :3000 rather
    than HTTP-probing /api/health so the check stays fast (50 ms
    timeout) and doesn't depend on the proxy itself working."""
    try:
        with socket.create_connection(("localhost", 3000), timeout=0.5):
            return True
    except (OSError, socket.timeout):
        return False


requires_next = pytest.mark.skipif(
    not _next_is_up(),
    reason=(
        "Next.js stack not reachable at http://localhost:3000. "
        "Bring it up with: docker compose -f infra/docker-compose.yml "
        "up -d console_next console"
    ),
)


@requires_next
def test_proxy_forwards_get():
    """GET /api/health proxies to the Next.js healthcheck (NOT
    through the catch-all — Next picks the static segment). Any
    200 from a known endpoint proves the runtime is up."""
    import urllib.request

    with urllib.request.urlopen(f"{FRONTEND_URL}/api/health", timeout=5) as r:
        assert r.status == 200


@requires_next
def test_proxy_forwards_post():
    """POST /auth/login via the catch-all proxy returns SOMETHING
    from FastAPI (the body / status is content-dependent; the
    contract here is just that the proxy round-trips the verb
    without 404'ing or 502'ing)."""
    import urllib.request

    req = urllib.request.Request(
        f"{FRONTEND_URL}/auth/login",
        data=b'{"email":"nobody@invalid.local","password":"wrong"}',
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    # FastAPI emits 401/403/422 depending on CSRF + body validity.
    # 502 = proxy couldn't reach upstream; 404 = catch-all wiring
    # is wrong. Anything 4xx from FastAPI means the proxy worked.
    assert 400 <= status < 500, (
        f"POST /auth/login returned {status} via the proxy — expected "
        f"a 4xx from FastAPI. 502 would mean the upstream is "
        f"unreachable; 404 would mean the catch-all wiring is wrong."
    )


@requires_next
def test_proxy_preserves_cookies():
    """GET /login-proxy must return a Set-Cookie header — that's
    the whole point of the round-trip (seeds csrf_token on the
    :3000 origin so the browser can echo it back)."""
    import urllib.request

    with urllib.request.urlopen(f"{FRONTEND_URL}/login-proxy", timeout=5) as r:
        # `getheader` only returns one value; for multi-Set-Cookie
        # we want all of them via getlist on the underlying msg.
        cookies = r.headers.get_all("Set-Cookie") or []
    joined = ";".join(cookies)
    assert "csrf_token=" in joined, (
        "GET /login-proxy must seed a csrf_token cookie (the whole "
        f"point of the round-trip). Got Set-Cookie headers: {cookies}"
    )


@requires_next
def test_proxy_preserves_csrf_header_on_post():
    """The proxy must forward X-CSRF-Token from the browser to
    FastAPI. We can't observe headers received by FastAPI from
    here, but we CAN verify the proxy doesn't 502 / 400 when we
    pass it the header (a malformed header would break the
    forward)."""
    import urllib.request

    req = urllib.request.Request(
        f"{FRONTEND_URL}/auth/login",
        data=b'{"email":"x","password":"y"}',
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-CSRF-Token": "dummy-value-for-header-passthrough",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    # We don't care about the status (the dummy CSRF will be
    # rejected); we just need it to be a real FastAPI response and
    # not a 502 from the proxy choking on the X-CSRF-Token header.
    assert status != 502, (
        "Proxy returned 502 with X-CSRF-Token set — the header is "
        "blocking the forward step. Check the REQ_DROP set in "
        "console-next/src/lib/proxy.ts."
    )


@requires_next
def test_auth_login_via_proxy_returns_200_with_real_creds():
    """End-to-end happy path. Requires emmanuel@local.ai /
    Admin123! to exist in the local-dev DB (per the local
    brief)."""
    import http.cookiejar
    import json
    import urllib.request

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # Step 1: seed csrf_token via /login-proxy.
    opener.open(f"{FRONTEND_URL}/login-proxy", timeout=5).read()
    csrf = next(
        (c.value for c in jar if c.name == "csrf_token"),
        None,
    )
    assert csrf, "csrf_token cookie not set by /login-proxy"

    # Step 2: POST /auth/login with the echoed header + cookie jar.
    body = json.dumps({
        "email": "emmanuel@local.ai",
        "password": "Admin123!",
    }).encode()
    req = urllib.request.Request(
        f"{FRONTEND_URL}/auth/login",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
        },
    )
    try:
        with opener.open(req, timeout=5) as r:
            assert r.status == 200, (
                f"POST /auth/login returned {r.status}. The same-origin "
                "proxy + CSRF dance round-tripped, but FastAPI rejected "
                "the credentials. Verify emmanuel@local.ai / Admin123! "
                "still exists in the local DB."
            )
            session_cookies = [c.name for c in jar]
            assert any(
                name in session_cookies
                for name in ("mod_session", "session", "access_token", "jwt")
            ), (
                f"Login 200 but no session cookie in the jar: "
                f"{session_cookies}. The proxy may be dropping "
                "Set-Cookie headers (check getSetCookie() handling in "
                "console-next/src/lib/proxy.ts)."
            )
    except urllib.error.HTTPError as e:
        pytest.fail(
            f"POST /auth/login raised HTTP {e.code} through the proxy. "
            f"Body: {e.read()[:200]!r}"
        )
