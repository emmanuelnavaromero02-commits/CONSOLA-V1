from __future__ import annotations

import json
import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")

DEFAULT_CREDENTIALS = {
    "base_url": "https://replicon-default.example.test",
    "auth_method": "bearer_token",
    "token": "token-platform-default",
}


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
        {"trusted": True, "source": "console", "tenant_id": tenant_id, "workspace_id": workspace_id}
    )


def _stub_reveal(monkeypatch, vault_client, credentials_by_tenant: dict, calls: list) -> None:
    def fake_get(url, headers=None, timeout=None):
        headers = dict(headers or {})
        raw_ctx = headers.get("x-security-context")
        assert raw_ctx is None or isinstance(raw_ctx, str), f"header must be str, got {type(raw_ctx)}"
        tenant_id = str(json.loads(raw_ctx).get("tenant_id") or "") if raw_ctx else ""
        calls.append({"url": url, "tenant_id": tenant_id, "raw_context": raw_ctx, "headers": headers})
        return _FakeResponse(credentials_by_tenant.get(tenant_id, DEFAULT_CREDENTIALS))

    monkeypatch.setattr(vault_client.requests, "get", fake_get)


def _stub_extraction_collaborators(monkeypatch, extraction_service, client_module, used_clients: list):
    def fake_extract_table(self, entity):
        used_clients.append(self)
        return []

    monkeypatch.setattr(client_module.RepliconClient, "extract_table", fake_extract_table)
    monkeypatch.setattr(extraction_service, "create_run", lambda **_kwargs: "run-1")
    monkeypatch.setattr(extraction_service, "finish_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "fail_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "write_parquet_and_upload", lambda **_kwargs: "s3://bronze/path")
    monkeypatch.setattr(extraction_service, "get_watermark", lambda _entity: None)
    monkeypatch.setattr(extraction_service, "update_watermark", lambda **_kwargs: None)


def test_scoped_extraction_resolves_tenant_credentials_without_type_errors(monkeypatch):
    from app.core import replicon_client as client_module
    from app.core import vault_client
    from app.services import extraction_service

    monkeypatch.setattr(vault_client, "_CONNECTION_CACHE", {})
    credentials = {
        "tenant-a": {"base_url": "https://replicon-a.example.test", "auth_method": "bearer_token", "token": "token-tenant-a"},
        "tenant-b": {"base_url": "https://replicon-b.example.test", "auth_method": "bearer_token", "token": "token-tenant-b"},
    }
    reveal_calls: list[dict] = []
    _stub_reveal(monkeypatch, vault_client, credentials, reveal_calls)
    used_clients: list = []
    _stub_extraction_collaborators(monkeypatch, extraction_service, client_module, used_clients)

    for tenant_id, workspace_id in (("tenant-a", "ws-a"), ("tenant-b", "ws-b")):
        ctx = _signed_ctx(tenant_id, workspace_id)
        result = extraction_service.run_entity(
            {"entity": "TimeEntry", "mode": "full", "security_context": ctx}
        )
        assert result["status"] == "success", result

    assert used_clients[0]._auth_connection["token"] == "token-tenant-a"
    assert used_clients[1]._auth_connection["token"] == "token-tenant-b"
    assert [call["tenant_id"] for call in reveal_calls] == ["tenant-a", "tenant-b"]
    for call in reveal_calls:
        assert isinstance(call["raw_context"], str)
        assert json.loads(call["raw_context"])["trusted"] is True
    assert len(vault_client._CONNECTION_CACHE) == 2


def test_extraction_without_context_still_works(monkeypatch):
    from app.core import replicon_client as client_module
    from app.core import vault_client
    from app.services import extraction_service

    monkeypatch.setattr(vault_client, "_CONNECTION_CACHE", {})
    reveal_calls: list[dict] = []
    _stub_reveal(monkeypatch, vault_client, {}, reveal_calls)
    used_clients: list = []
    _stub_extraction_collaborators(monkeypatch, extraction_service, client_module, used_clients)

    result = extraction_service.run_entity({"entity": "TimeEntry", "mode": "full"})

    assert result["status"] == "success", result
    assert used_clients[0]._auth_connection["token"] == "token-platform-default"
    assert len(reveal_calls) >= 1
    assert all(call["raw_context"] is None for call in reveal_calls)
    assert all("x-security-context" not in call["headers"] for call in reveal_calls)
