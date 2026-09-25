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
        httpx.get(base + probe, timeout=2.0)
    except Exception as exc:
        pytest.skip(f"{base} not reachable ({type(exc).__name__})")


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
    _skip_if_unreachable(base, "/health")
    r = httpx.post(f"{base}/skills/test_connection", timeout=5.0)
    assert r.status_code == 401


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
    _skip_if_unreachable(_CONSOLE)
    r = httpx.get(f"{_CONSOLE}/api/freshness/replicon", timeout=5.0)
    assert r.status_code in (401, 403)


def test_console_settings_reveal_unauth_returns_401_or_403():
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
    _skip_if_unreachable(_CONSOLE)
    r = httpx.post(
        f"{_CONSOLE}/api/admin/users",
        json={"email": "test@x.com"},
        timeout=5.0,
    )
    assert r.status_code in (401, 403)


def test_mcp_infra_invoke_unauth_returns_401():
    _skip_if_unreachable(_MCP_INFRA)
    r = httpx.post(f"{_MCP_INFRA}/mcp/invoke", json={}, timeout=5.0)
    assert r.status_code == 401, (
        f"mcp-infra /mcp/invoke without auth headers must return 401 "
        f"(got {r.status_code})"
    )


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
    probe = "/health" if port != 8010 else "/healthz"
    _skip_if_unreachable(base, probe)
    r = httpx.post(f"{base}/mcp/invoke", json={}, timeout=5.0)
    assert r.status_code == 401, (
        f"{base} /mcp/invoke without auth headers must return 401, "
        f"got {r.status_code}"
    )
