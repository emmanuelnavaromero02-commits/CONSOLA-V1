"""Sprint v1.41.1 — Negative security tests against a live stack.

Confirms defense-in-depth across sprints v1.33–v1.41 didn't regress.
Skip cleanly when each individual service isn't reachable — these are
"live stack" assertions and CI runs them only when the operator opts
in by bringing the stack up first.
"""
from __future__ import annotations

import httpx
import pytest


_CARTRIDGES = [
    ("replicon", 8201),
    ("sap_hcm",  8202),
    ("sap_sf",   8203),
    ("sap_s4",   8204),
]

_CONSOLE   = "http://localhost:8000"
_MCP_INFRA = "http://localhost:8010"


def _skip_if_unreachable(base: str, probe: str = "/healthz") -> None:
    try:
        # We just need to know if the port answers — any HTTP status is fine.
        httpx.get(base + probe, timeout=2.0)
    except Exception as exc:
        pytest.skip(f"{base} not reachable ({type(exc).__name__})")


# ── Cartridges ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cartridge,port", _CARTRIDGES)
def test_cartridge_mcp_invoke_unauth_returns_401(cartridge, port):
    base = f"http://localhost:{port}"
    _skip_if_unreachable(base, "/health")
    r = httpx.post(f"{base}/mcp/invoke", json={}, timeout=5.0)
    assert r.status_code == 401, (
        f"{cartridge} /mcp/invoke must reject unauth (got {r.status_code})"
    )


@pytest.mark.parametrize("cartridge,port", _CARTRIDGES)
def test_cartridge_skills_unauth_returns_401(cartridge, port):
    base = f"http://localhost:{port}"
    _skip_if_unreachable(base, "/health")
    r = httpx.get(f"{base}/skills/entities", timeout=5.0)
    assert r.status_code == 401, (
        f"{cartridge} /skills/entities must reject unauth (got {r.status_code})"
    )


@pytest.mark.parametrize("cartridge,port", _CARTRIDGES)
def test_cartridge_test_connection_unauth_returns_401(cartridge, port):
    """v1.41.0 hardening: /skills/test_connection joined the auth ring."""
    base = f"http://localhost:{port}"
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
    _skip_if_unreachable(_MCP_INFRA)
    r = httpx.post(f"{_MCP_INFRA}/mcp/invoke", json={}, timeout=5.0)
    assert r.status_code == 401


# ── X-Request-ID propagation (v1.41.1) ──────────────────────────────────────

@pytest.mark.parametrize("base", [_CONSOLE, _MCP_INFRA])
def test_unauth_responses_still_carry_request_id(base):
    """Auth-rejection responses must still pass through the request_id
    middleware so the rejection can be correlated in logs."""
    _skip_if_unreachable(base)
    r = httpx.get(f"{base}/api/whatever-unauth", timeout=5.0)
    assert r.headers.get("x-request-id"), (
        f"{base} dropped X-Request-ID on auth-rejected request"
    )
