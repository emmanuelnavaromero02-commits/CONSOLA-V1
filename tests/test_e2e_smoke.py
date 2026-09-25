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
E2E_SMOKE_ENABLED = os.environ.get("OMEGA_ENABLE_E2E_SMOKE", "").strip().lower() in {
    "1", "true", "yes", "on",
}


def _stack_up() -> bool:
    try:
        with urllib.request.urlopen(f"{STACK_BASE}/healthz", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not E2E_SMOKE_ENABLED or not _stack_up(),
    reason=(
        "E2E smoke disabled or stack not reachable at "
        f"{STACK_BASE}/healthz — set OMEGA_ENABLE_E2E_SMOKE=1 after `make up` to enable."
    ),
)


def _get(url: str, *, follow_redirects: bool = True, timeout: int = 5):
    req = urllib.request.Request(url)
    if follow_redirects:
        opener = urllib.request.build_opener()
    else:
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


def test_e2e_console_healthz_returns_json():
    status, _hdr, body = _get(f"{STACK_BASE}/healthz")
    assert status == 200, f"console /healthz → {status}, expected 200"
    data = json.loads(body)
    assert data["ok"] is True
    assert data["service"] == "console"


def test_e2e_workspace_healthz_returns_json():
    status, _hdr, body = _get(f"{WORKSPACE_BASE}/healthz")
    assert status == 200, f"workspace /healthz → {status}"
    data = json.loads(body)
    assert data["ok"] is True


def test_e2e_mcp_infra_healthz_returns_json():
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
    status, _hdr, body = _get(f"{VAULT_BASE}/healthz")
    assert status == 200, f"vault /healthz → {status} (compose probe broken)"
    data = json.loads(body)
    assert data["ok"] is True
    assert data["service"] == "vault"


def test_e2e_unauthenticated_post_to_monitoring_invoke_is_rejected():
    status, _hdr, _body = _post(f"{STACK_BASE}/monitoring/invoke")
    assert status in (401, 403), (
        f"unauth POST /monitoring/invoke → {status}, expected 401 or 403"
    )


def test_e2e_unauthenticated_post_to_admin_users_is_rejected():
    status, _hdr, _body = _post(
        f"{STACK_BASE}/api/admin/users",
        body=b'{"email":"x@example.com"}',
    )
    assert status in (401, 403), f"unauth POST /api/admin/users → {status}"


def test_e2e_unauthenticated_get_to_protected_page_redirects_or_401s():
    status, hdr, body = _get(f"{STACK_BASE}/rag", follow_redirects=False)
    assert status in (307, 401, 403, 302), (
        f"unauth GET /rag → {status}, expected redirect or auth failure"
    )
    if status == 307:
        location = hdr.get("Location") or hdr.get("location") or ""
        assert "/login" in location, (
            f"redirect target should point at /login; got {location!r}"
        )


def _script_src_segment(csp: str) -> str:
    if "script-src" not in csp:
        return ""
    after = csp.split("script-src", 1)[1]
    return after.split(";", 1)[0].strip()


def test_e2e_console_login_has_strict_csp():
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
    _status, hdr, _body = _get(f"{WORKSPACE_BASE}/healthz")
    csp = hdr.get("Content-Security-Policy", "") or hdr.get("content-security-policy", "")
    assert csp, "no Content-Security-Policy header on workspace /healthz"
    script_src = _script_src_segment(csp)
    assert "'unsafe-inline'" not in script_src, (
        f"workspace shell script-src still allows 'unsafe-inline': {script_src!r} — "
        f"v1.24 regression"
    )


def test_e2e_console_login_has_baseline_security_headers():
    status, hdr, _body = _get(f"{STACK_BASE}/login")
    assert status == 200
    h = {k.lower(): v for k, v in hdr.items()}
    assert h.get("x-content-type-options", "").lower() == "nosniff"
    assert h.get("x-frame-options", "").upper() == "DENY"
    assert "referrer-policy" in h, "missing Referrer-Policy"
    assert "strict-transport-security" in h, "missing Strict-Transport-Security"
