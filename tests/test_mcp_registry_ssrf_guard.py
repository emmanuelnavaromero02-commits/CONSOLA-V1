from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def registry_module():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path.insert(0, str(REPO / "console"))
    from app.services import mcp_registry
    return mcp_registry


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.parametrize(
    "url",
    [
        "http://replicon:8201/health",
        "http://hubspot:8210/health",
        "http://sap-hcm:8202/mcp",
        "http://sap-s4hana:8204/mcp",
        "http://sap-successfactors:8203/mcp",
        "http://mcp-infra:8010/mcp",
        "http://console:8000/studio_ops",
        "http://refinement:8500/mcp",
    ],
)
def test_mcp_registry_allows_internal_hosts(registry_module, url):
    registry_module._validate_mcp_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8201/health",
        "http://localhost:8000/monitoring",
    ],
)
def test_mcp_registry_allows_loopback_only_in_development(registry_module, monkeypatch, url):
    monkeypatch.setenv("APP_ENV", "development")
    registry_module._validate_mcp_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8201/health",
        "http://localhost:8000/monitoring",
    ],
)
def test_mcp_registry_blocks_loopback_in_production(registry_module, monkeypatch, url):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError):
        registry_module._validate_mcp_url(url)


def test_mcp_registry_ignores_loopback_override_in_production(registry_module, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MCP_ALLOW_LOOPBACK", "true")
    with pytest.raises(ValueError):
        registry_module._validate_mcp_url("http://127.0.0.1:8201/health")


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam",
        "http://evil.com/mcp",
        "https://example.org/mcp",
        "http://10.0.0.12:8201/mcp",
        "http://192.168.1.10:8201/mcp",
        "http://100.64.0.1:8201/mcp",
        "http://100.127.255.254:8201/mcp",
        "http://localhost.evil.com:8000/mcp",
        "file:///etc/passwd",
    ],
)
def test_mcp_registry_blocks_external_or_metadata_hosts(registry_module, url):
    with pytest.raises(ValueError):
        registry_module._validate_mcp_url(url)


def test_register_rejects_unallowlisted_host_before_db(registry_module):
    with pytest.raises(HTTPException) as exc:
        _run(registry_module.register({
            "id": "evil",
            "name": "evil",
            "url": "http://evil.com/mcp",
            "category": "other",
        }))
    assert exc.value.status_code == 403


def test_invoke_refuses_malicious_stored_url(registry_module, monkeypatch):
    class Pool:
        async def fetchrow(self, *_args):
            return {"url": "http://169.254.169.254/latest/meta-data/iam"}

    async def fake_pool():
        return Pool()

    monkeypatch.setattr(registry_module, "_get_pool", fake_pool)
    with pytest.raises(HTTPException) as exc:
        _run(registry_module.invoke("evil", "list", {}))
    assert exc.value.status_code == 403
    assert "mcp_host_not_allowlisted" in str(exc.value.detail)
    assert "blocked" in str(exc.value.detail)


def test_mcp_registry_allows_operator_configured_private_cidr(registry_module, monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_CIDRS", "10.42.0.0/16")
    registry_module._validate_mcp_url("http://10.42.5.10:8201/mcp")
