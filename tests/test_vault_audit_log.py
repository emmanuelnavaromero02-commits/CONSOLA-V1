"""Sprint v1.32 — Vault plaintext reads must be audited."""
from __future__ import annotations

import importlib
import hashlib
import hmac
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


LEGACY = "legacy_internal_key_with_more_than_thirty_two_characters"
CONSOLE_KEY = "console_to_vault_key_with_more_than_thirty_two_characters"
SIGNING_KEY = "s" * 64
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
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", "8sXi-0kBYU5DJ5dY7CCRkW7XHJsXxLPmO6r9OYx-3a4=")
    return importlib.import_module("app.main")


def _signed(ctx: dict) -> str:
    signed = {**ctx, "_signed_at": 1, "_signature_version": "hmac-sha256-v1"}
    payload = {key: value for key, value in signed.items() if key not in {"_signature", "_signed_at", "_signature_version"}}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signed["_signature"] = hmac.new(SIGNING_KEY.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return json.dumps(signed)


def _headers(ctx: dict) -> dict[str, str]:
    return {
        "x-api-key": CONSOLE_KEY,
        "x-internal-service": "console",
        "x-security-context": _signed(ctx),
    }


def test_get_secret_writes_audit_log(monkeypatch):
    main = _load_vault_main(monkeypatch)
    audit_calls: list[tuple] = []
    monkeypatch.setattr(main, "_db_get", lambda scope, cartridge, key, ctx=None: {"value": "plain-secret"})
    monkeypatch.setattr(main, "_db_audit_access", lambda *args: audit_calls.append(args))

    resp = TestClient(main.app).get(
        "/secrets/platform/API_TOKEN",
        headers=_headers(
            {
                "trusted": True,
                "source": "console",
                "role": "admin",
                "allowed_cartridges": ["*"],
            }
        ),
    )

    assert resp.status_code == 200
    assert resp.json() == {"value": "plain-secret"}
    assert audit_calls[0][:4] == ("console", "platform", "API_TOKEN", "read")


def test_get_connection_writes_audit_log(monkeypatch):
    main = _load_vault_main(monkeypatch)
    audit_calls: list[tuple] = []
    monkeypatch.setattr(
        main,
        "_db_get",
        lambda scope, cartridge, key, ctx=None: {"base_url": "https://api.example", "token": "plain-token"},
    )
    monkeypatch.setattr(main, "_db_audit_access", lambda *args: audit_calls.append(args))

    resp = TestClient(main.app).get(
        "/connections/replicon/default",
        headers=_headers(
            {
                "trusted": True,
                "source": "console",
                "role": "admin",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "workspace_id": "22222222-2222-2222-2222-222222222222",
                "allowed_cartridges": ["replicon"],
            }
        ),
    )

    assert resp.status_code == 200
    assert resp.json()["token"] == "plain-token"
    assert audit_calls[0][:4] == ("console", "connections", "replicon/default", "read")


def test_vault_access_log_migration_exists():
    sql = (Path(__file__).resolve().parents[1] / "infra/init/32_vault_audit_log.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS vault_access_log" in sql
    assert "caller_service" in sql
    assert "GRANT SELECT, INSERT ON vault_access_log TO omega_vault" in sql
