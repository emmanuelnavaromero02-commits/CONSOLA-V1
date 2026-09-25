from __future__ import annotations

import os
import importlib
import json
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException
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


def _request(
    path: str,
    service: str,
    key: str,
    method: str = "GET",
    extra_headers: dict[str, str] | None = None,
) -> Request:
    headers = [
        (b"host", b"testserver"),
        (b"x-internal-service", service.encode()),
        (b"x-api-key", key.encode()),
    ]
    for name, value in (extra_headers or {}).items():
        headers.append((name.lower().encode(), value.encode()))
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


def test_scoped_cartridge_vault_reveal_uses_signed_security_context(monkeypatch):
    console_main = _console_main()
    key = "sap-successfactors-dedicated-key-yyyyyyyyyyyyyyy"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE", key)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "security_context_signing_key_distinct_64_chars_console")
    ctx = console_main.build_security_context({
        "id": 42,
        "email": "scoped@example.com",
        "role": "workspace_admin",
        "active_tenant_id": "11111111-1111-4111-8111-111111111111",
        "active_workspace_id": "22222222-2222-4222-8222-222222222222",
        "allowed_cartridges": ["sap_successfactors"],
    })

    user = console_main._cartridge_vault_reveal_user(
        _request(
            "/api/vault/connections/sap_successfactors/tenant_sf/reveal",
            "cartridge-sap_successfactors",
            key,
            extra_headers={"x-security-context": json.dumps(ctx)},
        )
    )

    assert user["role"] == console_main.ROLE_ADMIN
    assert user["active_tenant_id"] == "11111111-1111-4111-8111-111111111111"
    assert user["active_workspace_id"] == "22222222-2222-4222-8222-222222222222"
    assert user["allowed_cartridges"] == ["sap_successfactors"]


def test_scoped_cartridge_vault_reveal_rejects_unsigned_trusted_context(monkeypatch):
    console_main = _console_main()
    key = "sap-successfactors-dedicated-key-yyyyyyyyyyyyyyy"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE", key)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "security_context_signing_key_distinct_64_chars_console")
    unsigned = {
        "trusted": True,
        "source": "console",
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "workspace_id": "22222222-2222-4222-8222-222222222222",
        "allowed_cartridges": ["sap_successfactors"],
    }

    with pytest.raises(HTTPException) as exc:
        console_main._cartridge_vault_reveal_user(
            _request(
                "/api/vault/connections/sap_successfactors/tenant_sf/reveal",
                "cartridge-sap_successfactors",
                key,
                extra_headers={"x-security-context": json.dumps(unsigned)},
            )
        )

    assert exc.value.status_code == 403


def test_cartridge_vault_reveal_without_a_signed_context_is_refused(monkeypatch):
    console_main = _console_main()
    key = "sap-successfactors-dedicated-key-yyyyyyyyyyyyyyy"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE", key)

    with pytest.raises(HTTPException) as exc:
        console_main._cartridge_vault_reveal_user(
            _request(
                "/api/vault/connections/sap_successfactors/default/reveal",
                "cartridge-sap_successfactors",
                key,
            )
        )

    assert exc.value.status_code == 403


def test_vault_reveal_connection_records_critical_audit_event():
    for path in (
        CONSOLE_ROOT / "app" / "main.py",
        CONSOLE_ROOT / "app" / "routers" / "v1" / "vault.py",
    ):
        src = path.read_text(encoding="utf-8")
        section = src.split('connections/{cartridge}/{conn_id}/reveal"', 1)[1]
        section = section.split("api_vault_upsert_connection", 1)[0]
        assert "_audit.record_event" in section
        assert 'action="vault.connection.reveal"' in section
        assert 'resource_type="vault_connection"' in section
        assert "request_id" not in section or "request_id_var" in src
        assert "critical=True" in section


@pytest.mark.parametrize("source,accepted", [("airflow", True), ("cartridge-hubspot", False), ("workspace", False)])
def test_reveal_accepts_only_console_or_airflow_signed_contexts(monkeypatch, source, accepted):
    console_main = _console_main()
    key = "sap-successfactors-dedicated-key-yyyyyyyyyyyyyyy"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE", key)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "security_context_signing_key_distinct_64_chars_console")
    signed = console_main.build_security_context({
        "id": 42,
        "email": "scoped@example.com",
        "role": "workspace_admin",
        "active_tenant_id": "11111111-1111-4111-8111-111111111111",
        "active_workspace_id": "22222222-2222-4222-8222-222222222222",
        "allowed_cartridges": ["sap_successfactors"],
    })
    security_context = importlib.import_module("app.services.security_context")
    payload = {k: v for k, v in signed.items() if not k.startswith("_signature") and k != "_signed_at"}
    payload["source"] = source
    ctx = security_context.sign_security_context(payload)
    request = _request(
        "/api/vault/connections/sap_successfactors/tenant_sf/reveal",
        "cartridge-sap_successfactors",
        key,
        extra_headers={"x-security-context": json.dumps(ctx)},
    )
    if accepted:
        user = console_main._cartridge_vault_reveal_user(request)
        assert user["active_tenant_id"] == "11111111-1111-4111-8111-111111111111"
    else:
        with pytest.raises(HTTPException) as exc:
            console_main._cartridge_vault_reveal_user(request)
        assert exc.value.status_code == 403
