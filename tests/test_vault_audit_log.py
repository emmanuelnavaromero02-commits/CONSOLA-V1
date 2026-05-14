"""Sprint v1.32 — Vault plaintext reads must be audited."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


LEGACY = "legacy_internal_key_with_more_than_thirty_two_characters"
CONSOLE_KEY = "console_to_vault_key_with_more_than_thirty_two_characters"
SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def clean_app_modules_after():
    yield
    _purge_app_modules()


def _load_vault_main(monkeypatch):
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "vault"))
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_VAULT", CONSOLE_KEY)
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", "8sXi-0kBYU5DJ5dY7CCRkW7XHJsXxLPmO6r9OYx-3a4=")
    return importlib.import_module("app.main")


def _headers() -> dict[str, str]:
    return {"x-api-key": CONSOLE_KEY, "x-internal-service": "console"}


def test_get_secret_writes_audit_log(monkeypatch):
    main = _load_vault_main(monkeypatch)
    audit_calls: list[tuple] = []
    monkeypatch.setattr(main, "_db_get", lambda scope, cartridge, key: {"value": "plain-secret"})
    monkeypatch.setattr(main, "_db_audit_access", lambda *args: audit_calls.append(args))

    resp = TestClient(main.app).get("/secrets/platform/API_TOKEN", headers=_headers())

    assert resp.status_code == 200
    assert resp.json() == {"value": "plain-secret"}
    assert audit_calls == [("console", "platform", "API_TOKEN", "read")]


def test_get_connection_writes_audit_log(monkeypatch):
    main = _load_vault_main(monkeypatch)
    audit_calls: list[tuple] = []
    monkeypatch.setattr(
        main,
        "_db_get",
        lambda scope, cartridge, key: {"base_url": "https://api.example", "token": "plain-token"},
    )
    monkeypatch.setattr(main, "_db_audit_access", lambda *args: audit_calls.append(args))

    resp = TestClient(main.app).get("/connections/replicon/default", headers=_headers())

    assert resp.status_code == 200
    assert resp.json()["token"] == "plain-token"
    assert audit_calls == [("console", "connections", "replicon/default", "read")]


def test_vault_access_log_migration_exists():
    sql = (Path(__file__).resolve().parents[1] / "infra/init/32_vault_audit_log.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS vault_access_log" in sql
    assert "caller_service" in sql
    assert "GRANT SELECT, INSERT ON vault_access_log TO omega_vault" in sql
