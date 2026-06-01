from __future__ import annotations

import importlib
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SALESFORCE_ROOT = REPO / "cartridges" / "salesforce"


def _load_vault_client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minio")
    monkeypatch.setenv("MINIO_SECRET_KEY", "secret")
    sys.path[:] = [
        p
        for p in sys.path
        if not any(marker in p for marker in ("/cartridges/", "/console", "/vault", "/workspace", "/mcp-infra", "/refinement"))
    ]
    sys.path.insert(0, str(SALESFORCE_ROOT))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    return importlib.import_module("app.core.vault_client")


def test_salesforce_vault_client_rejects_generic_cartridge_key_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", "shared")
    monkeypatch.delenv("INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE", raising=False)
    client = _load_vault_client(monkeypatch)

    assert client._auth_options("salesforce") == []


def test_salesforce_vault_client_uses_dedicated_console_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", "shared")
    monkeypatch.setenv("INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE", "dedicated")
    client = _load_vault_client(monkeypatch)

    assert client._auth_options("salesforce") == [("dedicated", "cartridge-salesforce")]
