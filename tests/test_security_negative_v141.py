"""Sprint v1.41.1 — Negative security tests against a live stack.

Confirms defense-in-depth across sprints v1.33–v1.41 didn't regress.
Skip cleanly when each individual service isn't reachable — these are
"live stack" assertions and CI runs them only when the operator opts
in by bringing the stack up first.
"""
from __future__ import annotations

import os

import httpx
import pytest


_LIVE_STACK_TESTS = os.environ.get("OMEGA_ENABLE_LIVE_STACK_TESTS", "").strip().lower() in {
    "1", "true", "yes", "on",
}
_CARTRIDGES = [
    ("replicon", os.environ.get("OMEGA_REPLICON_BASE", "http://localhost:8201"), 8201),
    ("sap_hcm",  os.environ.get("OMEGA_SAP_HCM_BASE", "http://localhost:8202"), 8202),
    ("sap_sf",   os.environ.get("OMEGA_SAP_SUCCESSFACTORS_BASE", "http://localhost:8203"), 8203),
    ("sap_s4",   os.environ.get("OMEGA_SAP_S4HANA_BASE", "http://localhost:8204"), 8204),
]

_CONSOLE   = os.environ.get("OMEGA_STACK_BASE", "http://localhost:8000")
_MCP_INFRA = os.environ.get("OMEGA_MCP_INFRA_BASE", "http://localhost:8010")


def _skip_if_unreachable(base: str, probe: str = "/healthz") -> None:
    if not _LIVE_STACK_TESTS:
        pytest.skip("live-stack security probes disabled; run `make test-hermetic` or set OMEGA_ENABLE_LIVE_STACK_TESTS=1")
    try:
        # We just need to know if the port answers — any HTTP status is fine.
        httpx.get(base + probe, timeout=2.0)
    except Exception as exc:
        pytest.skip(f"{base} not reachable ({type(exc).__name__})")


# ── Cartridges ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cartridge,base,port", _CARTRIDGES)
def test_cartridge_mcp_invoke_unauth_returns_401(cartridge, base, port):
    _skip_if_unreachable(base, "/health")
    r = httpx.post(f"{base}/mcp/invoke", json={}, timeout=5.0)
    assert r.status_code == 401, (
        f"{cartridge} /mcp/invoke must reject unauth (got {r.status_code})"
    )


@pytest.mark.parametrize("cartridge,base,port", _CARTRIDGES)
def test_cartridge_skills_unauth_returns_401(cartridge, base, port):
    _skip_if_unreachable(base, "/health")
    r = httpx.get(f"{base}/skills/entities", timeout=5.0)
    assert r.status_code == 401, (
        f"{cartridge} /skills/entities must reject unauth (got {r.status_code})"
    )


@pytest.mark.parametrize("cartridge,base,port", _CARTRIDGES)
def test_cartridge_test_connection_unauth_returns_401(cartridge, base, port):
    """v1.41.0 hardening: /skills/test_connection joined the auth ring."""
    _skip_if_unreachable(base, "/health")
    r = httpx.post(f"{base}/skills/test_connection", timeout=5.0)
    assert r.status_code == 401


# ── Console ─────────────────────────────────────────────────────────────────

def test_console_admin_users_unauth_returns_401_or_403():
    _skip_if_unreachable(_CONSOLE)
    r = httpx.get(f"{_CONSOLE}/api/admin/users", timeout=5.0)
    assert r.status_code in (401, 403)


def test_console_mcp_invoke_unauth_returns_401_or_403():
    _skip_if_unreachable(_CONSOLE)
    r = httpx.post(
        f"{_CONSOLE}/api/mcp/invoke",
        json={"server": "infra", "tool": "list_dags"},
        timeout=5.0,
    )
    assert r.status_code in (401, 403)


def test_console_freshness_unauth_returns_401():
    """v1.41.1 endpoint must be gated."""
    _skip_if_unreachable(_CONSOLE)
    r = httpx.get(f"{_CONSOLE}/api/freshness/replicon", timeout=5.0)
    assert r.status_code in (401, 403)


def test_console_settings_reveal_unauth_returns_401_or_403():
    """v1.41.0 forensic-complete: reveal of secrets is admin + CSRF."""
    _skip_if_unreachable(_CONSOLE)
    r = httpx.post(f"{_CONSOLE}/api/settings/replicon_token/reveal", timeout=5.0)
    assert r.status_code in (401, 403)


def test_console_cartridge_run_unauth_returns_401_or_403():
    _skip_if_unreachable(_CONSOLE)
    r = httpx.post(
        f"{_CONSOLE}/api/cartridges/replicon/entities/users/run?mode=full",
        timeout=5.0,
    )
    assert r.status_code in (401, 403)


def test_csrf_required_on_mutating_admin_route():
    """Without a session, the response is 401; with a session but no CSRF
    token it's 403. Either is acceptable defense-in-depth — both close
    the unauth path."""
    _skip_if_unreachable(_CONSOLE)
    r = httpx.post(
        f"{_CONSOLE}/api/admin/users",
        json={"email": "test@x.com"},
        timeout=5.0,
    )
    assert r.status_code in (401, 403)


# ── MCP Infra ───────────────────────────────────────────────────────────────

def test_mcp_infra_invoke_unauth_returns_401():
    """v1.42.1 auditor fix: missing headers → 401 (not 403). 403 is
    reserved for 'auth presented but service name not in allow-list'."""
    _skip_if_unreachable(_MCP_INFRA)
    r = httpx.post(f"{_MCP_INFRA}/mcp/invoke", json={}, timeout=5.0)
    assert r.status_code == 401, (
        f"mcp-infra /mcp/invoke without auth headers must return 401 "
        f"(got {r.status_code})"
    )


# ── X-Request-ID propagation (v1.41.1 + v1.42.1) ────────────────────────────
#
# Every response from any of the 5 services MUST carry an X-Request-ID
# header — successful responses, auth rejections (401), CSRF / forbidden
# (403), even 404s. The middleware is registered as the outermost ASGI
# wrapper and injects the header at the send() level so no inner
# exception path can strip it.

_ALL_SERVICES = [
    ("console",   _CONSOLE,                 "/api/whatever-unauth"),
    ("mcp-infra", _MCP_INFRA,               "/api/whatever-unauth"),
    ("replicon",  _CARTRIDGES[0][1],        "/mcp/tools"),
    ("sap_hcm",   _CARTRIDGES[1][1],        "/mcp/tools"),
    ("sap_sf",    _CARTRIDGES[2][1],        "/mcp/tools"),
    ("sap_s4",    _CARTRIDGES[3][1],        "/mcp/tools"),
]


@pytest.mark.parametrize("name,base,path", _ALL_SERVICES,
                         ids=lambda v: v if isinstance(v, str) else "")
def test_unauth_responses_still_carry_request_id(name, base, path):
    """401/403/404 responses must include X-Request-ID."""
    probe = "/health" if name not in ("console", "mcp-infra") else "/healthz"
    _skip_if_unreachable(base, probe)
    r = httpx.get(f"{base}{path}", timeout=5.0)
    assert r.headers.get("x-request-id"), (
        f"{name} ({base}{path}) dropped X-Request-ID on a "
        f"{r.status_code} response"
    )


@pytest.mark.parametrize("name,base,path", _ALL_SERVICES,
                         ids=lambda v: v if isinstance(v, str) else "")
def test_403_responses_still_carry_request_id(name, base, path):
    """When auth headers ARE presented but with an invalid service
    name, the response should be 403 (or 401 if the service rejects
    earlier in the chain) — either way the X-Request-ID must travel
    on the response so the rejection is correlatable."""
    probe = "/health" if name not in ("console", "mcp-infra") else "/healthz"
    _skip_if_unreachable(base, probe)
    r = httpx.post(
        f"{base}/mcp/invoke" if name != "console" else f"{base}/api/mcp/invoke",
        json={},
        headers={
            "X-Api-Key": "not-the-real-one",
            "X-Internal-Service": "not-on-the-allow-list",
        },
        timeout=5.0,
    )
    assert r.status_code in (401, 403)
    assert r.headers.get("x-request-id"), (
        f"{name} dropped X-Request-ID on a {r.status_code} reject"
    )


@pytest.mark.parametrize("base,port", [
    (_CARTRIDGES[0][1], 8201),
    (_CARTRIDGES[1][1], 8202),
    (_CARTRIDGES[2][1], 8203),
    (_CARTRIDGES[3][1], 8204),
    (_MCP_INFRA, 8010),
])
def test_mcp_invoke_returns_401_when_auth_headers_missing(base, port):
    """v1.42.1 — consistent 401 across the 5 services when no
    X-Internal-Service / X-Api-Key headers are presented."""
    probe = "/health" if port != 8010 else "/healthz"
    _skip_if_unreachable(base, probe)
    r = httpx.post(f"{base}/mcp/invoke", json={}, timeout=5.0)
    assert r.status_code == 401, (
        f"{base} /mcp/invoke without auth headers must return 401, "
        f"got {r.status_code}"
    )
