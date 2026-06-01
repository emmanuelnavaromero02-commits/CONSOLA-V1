from __future__ import annotations

import os
import importlib
import sys
import types
from pathlib import Path

from starlette.requests import Request


sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))
os.environ.setdefault("INTERNAL_API_KEY", "legacy-internal-key-valid-for-unit-tests-aaaaaaaa")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSOLE_ROOT = REPO_ROOT / "console"


def _console_main():
    os.environ["INTERNAL_API_KEY"] = "legacy-internal-key-valid-for-unit-tests-aaaaaaaa"
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path[:] = [
        p
        for p in sys.path
        if not any(sibling in p for sibling in ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra"))
    ]
    if str(CONSOLE_ROOT) not in sys.path:
        sys.path.insert(0, str(CONSOLE_ROOT))
    return importlib.import_module("app.main")


def _request(path: str, service: str, key: str, method: str = "GET") -> Request:
    headers = [
        (b"host", b"testserver"),
        (b"x-internal-service", service.encode()),
        (b"x-api-key", key.encode()),
    ]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers,
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )


def test_hubspot_reveal_rejects_generic_cartridge_key_in_production(monkeypatch):
    console_main = _console_main()
    shared = "shared-cartridge-key-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    hubspot = "hubspot-dedicated-key-yyyyyyyyyyyyyyyyyyyyyyyy"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", shared)
    monkeypatch.setenv("INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE", hubspot)

    path = "/api/vault/connections/hubspot/default/reveal"

    assert console_main._is_cartridge_vault_reveal_request(
        _request(path, "cartridge-hubspot", shared)
    ) is False
    assert console_main._is_cartridge_vault_reveal_request(
        _request(path, "cartridge-hubspot", hubspot)
    ) is True


def test_vault_reveal_dedicated_key_cannot_spoof_sibling_cartridge(monkeypatch):
    console_main = _console_main()
    replicon = "replicon-dedicated-key-xxxxxxxxxxxxxxxxxxxxxxx"
    hubspot = "hubspot-dedicated-key-yyyyyyyyyyyyyyyyyyyyyyyy"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_REPLICON_TO_CONSOLE", replicon)
    monkeypatch.setenv("INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE", hubspot)

    assert console_main._is_cartridge_vault_reveal_request(
        _request("/api/vault/connections/hubspot/default/reveal", "cartridge-hubspot", replicon)
    ) is False
    assert console_main._is_cartridge_vault_reveal_request(
        _request("/api/vault/connections/replicon/default/reveal", "cartridge-replicon", replicon)
    ) is True


def test_salesforce_reveal_requires_salesforce_dedicated_key(monkeypatch):
    console_main = _console_main()
    shared = "shared-cartridge-key-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    salesforce = "salesforce-dedicated-key-yyyyyyyyyyyyyyyyyyyy"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", shared)
    monkeypatch.setenv("INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE", salesforce)

    path = "/api/vault/connections/salesforce/default/reveal"

    assert console_main._is_cartridge_vault_reveal_request(
        _request(path, "cartridge-salesforce", shared)
    ) is False
    assert console_main._is_cartridge_vault_reveal_request(
        _request(path, "cartridge-salesforce", salesforce)
    ) is True
