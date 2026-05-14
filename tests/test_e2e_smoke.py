"""Sprint v1.26 (audit F10) — end-to-end happy-path smoke against a
running compose stack.

These tests REQUIRE `make up` to have completed. When the stack isn't
reachable, every test in this module is skipped at collection time
(no failure, no false signal). The skip detection is wrapped in
broad exception handling so weird network conditions (DNS, ECONNREFUSED,
EHOSTUNREACH, ...) all collapse to "skip — start the stack first".

What this differs from scripts/smoke_test.sh (v1.23):
  * smoke_test.sh runs OUTSIDE pytest, as part of `make smoke`. It's the
    operator's pre-flight check before declaring a deploy healthy and
    uses `docker exec` to probe Postgres directly.
  * test_e2e_smoke.py runs INSIDE pytest, with the rest of the test
    suite. It exercises HTTP surfaces (CSP, auth gates, healthz) and
    catches contract regressions that don't show up in unit tests.

Both are intentional: smoke_test.sh covers DB-internal invariants
(omega_vault role grants); test_e2e_smoke.py covers HTTP-surface
invariants (CSP, security headers, auth gates).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest


STACK_BASE     = os.environ.get("OMEGA_STACK_BASE",     "http://localhost:8000")
WORKSPACE_BASE = os.environ.get("OMEGA_WORKSPACE_BASE", "http://localhost:8001")
MCP_INFRA_BASE = os.environ.get("OMEGA_MCP_INFRA_BASE", "http://localhost:8010")
VAULT_BASE     = os.environ.get("OMEGA_VAULT_BASE",     "http://localhost:8300")
REFINEMENT_BASE = os.environ.get("OMEGA_REFINEMENT_BASE", "http://localhost:8500")


def _stack_up() -> bool:
    """Liveness probe for the whole module's skip decision. Returns True
    iff the console /healthz answers within a short timeout. Anything
    else (connection refused, DNS, timeout) → False so the whole module
    is skipped cleanly."""
    try:
        with urllib.request.urlopen(f"{STACK_BASE}/healthz", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _stack_up(),
    reason=(
        "Stack not reachable at "
        f"{STACK_BASE}/healthz — run `make up` to enable E2E tests."
    ),
)


# Small helpers — keep the tests readable.

def _get(url: str, *, follow_redirects: bool = True, timeout: int = 5):
    """GET that returns (status, headers, body_bytes). On HTTPError it
    still returns the response so the test can inspect 4xx/5xx without
    raising."""
    req = urllib.request.Request(url)
    if follow_redirects:
        opener = urllib.request.build_opener()
    else:
        # Custom opener that does NOT follow 3xx.
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def http_error_302(self, *a, **k): return None
            http_error_301 = http_error_307 = http_error_303 = http_error_302
        opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), (e.read() or b"")


def _post(url: str, body: bytes = b"{}", timeout: int = 5):
    req = urllib.request.Request(
        url, method="POST", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), (e.read() or b"")


# ── /healthz on every app service ────────────────────────────────────


def test_e2e_console_healthz_returns_json():
    """Console /healthz must answer 200 with {ok: true, service: console}.
    Without v1.21.1 (which added /healthz to console's _AUTH_PUBLIC_EXACT)
    this would 307-redirect to /login and the body parse would fail."""
    status, _hdr, body = _get(f"{STACK_BASE}/healthz")
    assert status == 200, f"console /healthz → {status}, expected 200"
    data = json.loads(body)
    assert data["ok"] is True
    assert data["service"] == "console"


def test_e2e_workspace_healthz_returns_json():
    """workspace /healthz must answer 200 with {ok: true}."""
    status, _hdr, body = _get(f"{WORKSPACE_BASE}/healthz")
    assert status == 200, f"workspace /healthz → {status}"
    data = json.loads(body)
    assert data["ok"] is True


def test_e2e_mcp_infra_healthz_returns_json():
    """mcp-infra has both /healthz (v1.21) and the legacy /health. We
    use /healthz so the test pins the canonical path."""
    status, _hdr, body = _get(f"{MCP_INFRA_BASE}/healthz")
    assert status == 200, f"mcp-infra /healthz → {status}"
    data = json.loads(body)
    assert data["ok"] is True


def test_e2e_refinement_healthz_returns_json():
    status, _hdr, body = _get(f"{REFINEMENT_BASE}/healthz")
    assert status == 200, f"refinement /healthz → {status}"
    data = json.loads(body)
    assert data["ok"] is True


def test_e2e_vault_healthz_returns_json():
    """vault has an app-level Depends(verify_api_key) — the v1.21.1
    short-circuit on _PUBLIC_PATHS = {"/healthz"} is what lets this
    test see a 200 without credentials. A regression of that short-
    circuit would fail this test immediately."""
    status, _hdr, body = _get(f"{VAULT_BASE}/healthz")
    assert status == 200, f"vault /healthz → {status} (compose probe broken)"
    data = json.loads(body)
    assert data["ok"] is True
    assert data["service"] == "vault"


# ── Auth gate ────────────────────────────────────────────────────────


def test_e2e_unauthenticated_post_to_monitoring_invoke_is_rejected():
    """v1.21 F1 fix: /monitoring/invoke now requires auth.
    Post-v1.22 the rejection code is 403 (CSRF fires first on cookie
    paths) but 401 is also acceptable. A 2xx would be a regression of
    v1.21 + v1.22."""
    status, _hdr, _body = _post(f"{STACK_BASE}/monitoring/invoke")
    assert status in (401, 403), (
        f"unauth POST /monitoring/invoke → {status}, expected 401 or 403"
    )


def test_e2e_unauthenticated_post_to_admin_users_is_rejected():
    """Sanity check on a second protected POST so the auth gate isn't
    a single-route accident."""
    status, _hdr, _body = _post(
        f"{STACK_BASE}/api/admin/users",
        body=b'{"email":"x@example.com"}',
    )
    assert status in (401, 403), f"unauth POST /api/admin/users → {status}"


def test_e2e_unauthenticated_get_to_protected_page_redirects_or_401s():
    """The console shell has an auth_middleware that redirects pages to
    /login. After v1.22 /rag also has a route-level dep. Either outcome
    is correct: 307 → /login, 401 from the dep, or 403 from CSRF.
    A 200 with the actual page body would be a regression."""
    status, hdr, body = _get(f"{STACK_BASE}/rag", follow_redirects=False)
    assert status in (307, 401, 403, 302), (
        f"unauth GET /rag → {status}, expected redirect or auth failure"
    )
    if status == 307:
        # Pin that the redirect target is the login page (not an
        # accidental open redirect to an external URL).
        location = hdr.get("Location") or hdr.get("location") or ""
        assert "/login" in location, (
            f"redirect target should point at /login; got {location!r}"
        )


# ── CSP / security headers ───────────────────────────────────────────


def _script_src_segment(csp: str) -> str:
    """Extract the script-src directive value from a CSP string. Returns
    empty string if not present."""
    if "script-src" not in csp:
        return ""
    after = csp.split("script-src", 1)[1]
    return after.split(";", 1)[0].strip()


def test_e2e_console_login_has_strict_csp():
    """v1.18-phase-3 + v1.22 contract: console's CSP drops
    'unsafe-inline' from script-src on every shell path. /login is
    public so we can hit it without credentials."""
    status, hdr, _body = _get(f"{STACK_BASE}/login")
    assert status == 200, f"GET /login → {status}"
    csp = hdr.get("Content-Security-Policy", "") or hdr.get("content-security-policy", "")
    assert csp, f"no Content-Security-Policy header on /login"
    script_src = _script_src_segment(csp)
    assert "'self'" in script_src, f"script-src missing 'self': {script_src!r}"
    assert "'unsafe-inline'" not in script_src, (
        f"script-src still allows 'unsafe-inline': {script_src!r} — "
        f"v1.18 phase 3 regression"
    )


def test_e2e_workspace_shell_has_strict_csp():
    """v1.24 contract: workspace shell drops 'unsafe-inline' from
    script-src. /healthz is on the shell path family (not /apps/) so
    it gets the strict header."""
    _status, hdr, _body = _get(f"{WORKSPACE_BASE}/healthz")
    csp = hdr.get("Content-Security-Policy", "") or hdr.get("content-security-policy", "")
    assert csp, "no Content-Security-Policy header on workspace /healthz"
    script_src = _script_src_segment(csp)
    assert "'unsafe-inline'" not in script_src, (
        f"workspace shell script-src still allows 'unsafe-inline': {script_src!r} — "
        f"v1.24 regression"
    )


def test_e2e_console_login_has_baseline_security_headers():
    """The non-CSP security headers (set in v1.x and maintained across
    sprints) must all be present on the public /login page. A reverse
    proxy that strips one of them is a deploy bug, not a code one —
    but the test catches it before it reaches prod."""
    status, hdr, _body = _get(f"{STACK_BASE}/login")
    assert status == 200
    # Case-insensitive header read.
    h = {k.lower(): v for k, v in hdr.items()}
    assert h.get("x-content-type-options", "").lower() == "nosniff"
    assert h.get("x-frame-options", "").upper() == "DENY"
    assert "referrer-policy" in h, "missing Referrer-Policy"
    assert "strict-transport-security" in h, "missing Strict-Transport-Security"
