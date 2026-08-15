from __future__ import annotations

import json
import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _signed_ctx(tenant_id: str, workspace_id: str) -> dict:
    from app.core import request_context

    return request_context._sign_security_context(
        {
            "trusted": True,
            "source": "console",
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
        }
    )


def _stub_reveal(monkeypatch, vault_client, credentials_by_tenant: dict, calls: list) -> None:
    def fake_get(url, headers=None, timeout=None):
        headers = dict(headers or {})
        raw_ctx = headers.get("x-security-context") or ""
        tenant_id = str(json.loads(raw_ctx).get("tenant_id") or "") if raw_ctx else ""
        calls.append({"url": url, "tenant_id": tenant_id, "headers": headers})
        return _FakeResponse(credentials_by_tenant.get(tenant_id, {}))

    monkeypatch.setattr(vault_client.requests, "get", fake_get)


def test_extraction_resolves_vault_credentials_per_tenant(monkeypatch):
    from app.core import hubspot_client as client_module
    from app.core import vault_client
    from app.services import extraction_service

    monkeypatch.setattr(vault_client, "_CONNECTION_CACHE", {})
    monkeypatch.setattr(client_module.settings, "use_demo_data", False)
    credentials = {
        "tenant-a": {"base_url": "https://api.hubapi.test/a", "auth_method": "bearer_token", "token": "token-tenant-a"},
        "tenant-b": {"base_url": "https://api.hubapi.test/b", "auth_method": "bearer_token", "token": "token-tenant-b"},
    }
    reveal_calls: list[dict] = []
    _stub_reveal(monkeypatch, vault_client, credentials, reveal_calls)

    used_clients: list = []

    def fake_fetch_page(self, config, after):
        used_clients.append(self)
        return [], None

    monkeypatch.setattr(client_module.HubSpotClient, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(extraction_service, "create_run", lambda **_kwargs: "run-1")
    monkeypatch.setattr(extraction_service, "finish_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "fail_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "write_parquet_and_upload", lambda **_kwargs: "s3://bronze/path")
    monkeypatch.setattr(extraction_service, "get_watermark", lambda _entity: None)
    monkeypatch.setattr(extraction_service, "update_watermark", lambda **_kwargs: None)

    for tenant_id, workspace_id in (("tenant-a", "ws-a"), ("tenant-b", "ws-b")):
        ctx = _signed_ctx(tenant_id, workspace_id)
        result = extraction_service.run_entity(
            {"entity": "contacts", "mode": "full", "security_context": ctx}
        )
        assert result["status"] == "success"

    assert used_clients[0]._auth_connection["token"] == "token-tenant-a"
    assert used_clients[1]._auth_connection["token"] == "token-tenant-b"
    assert [call["tenant_id"] for call in reveal_calls] == ["tenant-a", "tenant-b"]
    cache_scopes = {key[1].split("/")[0] for key in vault_client._CONNECTION_CACHE}
    assert cache_scopes == {"tenant_id=tenant-a", "tenant_id=tenant-b"}
