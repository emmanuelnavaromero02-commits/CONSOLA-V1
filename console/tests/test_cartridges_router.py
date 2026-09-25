from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest
from fastapi import HTTPException

from app.routers import cartridges


class _FakeAsyncClient:
    last_instance = None

    def __init__(self, response, **client_kwargs):
        self._response = response
        self.client_kwargs = client_kwargs
        _FakeAsyncClient.last_instance = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return self._response

    async def put(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return self._response

    async def delete(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return self._response


def _request():
    return SimpleNamespace(state=SimpleNamespace(user={
        "id": 1,
        "email": "admin@example.com",
        "role": "admin",
        "allowed_cartridges": ["replicon"],
    }))


def _workspace_request_without_banxico():
    return SimpleNamespace(state=SimpleNamespace(user={
        "id": 1,
        "email": "admin@example.com",
        "role": "admin",
        "active_tenant_id": "tenant-a",
        "active_workspace_id": "workspace-a",
        "allowed_cartridges": ["sap_successfactors"],
    }))


@pytest.mark.asyncio
async def test_run_entity_maps_upstream_500_to_honest_dependency_error(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "security_context_signing_key_distinct_64_chars_router")
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE", "console_to_cartridge_key")
    response = SimpleNamespace(status_code=500, text='{"detail":"internal error"}')
    monkeypatch.setattr(cartridges.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(response, **kwargs))
    monkeypatch.setattr(cartridges.audit_service, "record_event", AsyncMock())

    with pytest.raises(HTTPException) as exc:
        await cartridges.run_entity("replicon", "Department", _request())

    assert exc.value.status_code == 424
    assert exc.value.detail["error"] == "cartridge_not_ready"
    assert exc.value.detail["upstream_status"] == 500
    cartridges.audit_service.record_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_entity_forwards_scope_and_signed_context(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "security_context_signing_key_distinct_64_chars_router")
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE", "console_to_cartridge_key")
    response = SimpleNamespace(status_code=200, json=lambda: {"status": "success"})
    monkeypatch.setattr(cartridges.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(response, **kwargs))
    monkeypatch.setattr(cartridges.audit_service, "record_event", AsyncMock())

    request = SimpleNamespace(state=SimpleNamespace(user={
        "id": 1,
        "email": "admin@example.com",
        "role": "admin",
        "active_tenant_id": "tenant-a",
        "active_workspace_id": "workspace-a",
        "allowed_cartridges": ["banxico"],
    }))

    await cartridges.run_entity("banxico", "series_observations", request, conn_id="default")

    body = _FakeAsyncClient.last_instance.kwargs["json"]
    forwarded_ctx = json.loads(_FakeAsyncClient.last_instance.client_kwargs["headers"]["X-Security-Context"])
    assert body["tenant_id"] == "tenant-a"
    assert body["workspace_id"] == "workspace-a"
    assert body["security_context"]["tenant_id"] == "tenant-a"
    assert forwarded_ctx["workspace_id"] == "workspace-a"
    assert _FakeAsyncClient.last_instance.kwargs["params"] == {"conn_id": "default"}


@pytest.mark.asyncio
async def test_test_connection_forwards_selected_conn_id(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "security_context_signing_key_distinct_64_chars_router")
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE", "console_to_cartridge_key")
    response = SimpleNamespace(
        is_success=True,
        status_code=200,
        headers={"content-type": "application/json"},
        json=lambda: {"status": "ok", "message": "ok"},
    )
    monkeypatch.setattr(cartridges.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(response, **kwargs))
    monkeypatch.setattr(cartridges.audit_service, "record_event", AsyncMock())

    request = SimpleNamespace(state=SimpleNamespace(user={
        "id": 1,
        "email": "admin@example.com",
        "role": "admin",
        "active_tenant_id": "11111111-1111-4111-8111-111111111111",
        "active_workspace_id": "22222222-2222-4222-8222-222222222222",
        "allowed_cartridges": ["sap_successfactors"],
    }))

    result = await cartridges.test_connection("sap_successfactors", request, conn_id="tenant_sf")

    assert result["ok"] is True
    assert _FakeAsyncClient.last_instance.url.endswith("/skills/test_connection")
    assert _FakeAsyncClient.last_instance.kwargs["params"] == {"conn_id": "tenant_sf"}
    forwarded_ctx = json.loads(_FakeAsyncClient.last_instance.client_kwargs["headers"]["X-Security-Context"])
    assert forwarded_ctx["trusted"] is True
    assert forwarded_ctx["tenant_id"] == "11111111-1111-4111-8111-111111111111"
    assert forwarded_ctx["workspace_id"] == "22222222-2222-4222-8222-222222222222"
    assert forwarded_ctx["_signature"]
    cartridges.audit_service.record_event.assert_awaited_once()
    assert cartridges.audit_service.record_event.await_args.kwargs["metadata"]["conn_id"] == "tenant_sf"


@pytest.mark.asyncio
async def test_banxico_credentials_bootstrap_can_save_without_workspace_cartridge(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "security_context_signing_key_distinct_64_chars_router")
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_VAULT", "console_to_vault_key")
    response = SimpleNamespace(is_success=True, status_code=200, json=lambda: {"saved": True})
    monkeypatch.setattr(cartridges.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(response, **kwargs))
    monkeypatch.setattr(cartridges.audit_service, "record_event", AsyncMock())

    result = await cartridges.save_credentials(
        "banxico",
        {"auth_method": "bmx_token", "token": "secret-banxico-token"},
        _workspace_request_without_banxico(),
    )

    assert result == {"ok": True, "encrypted_count": 2, "conn_id": "default"}
    assert _FakeAsyncClient.last_instance.url.endswith("/connections/banxico/default")
    forwarded_ctx = json.loads(_FakeAsyncClient.last_instance.client_kwargs["headers"]["x-security-context"])
    assert forwarded_ctx["tenant_id"] == "tenant-a"
    assert forwarded_ctx["workspace_id"] == "workspace-a"
    assert "banxico" in forwarded_ctx["allowed_cartridges"]
    assert _FakeAsyncClient.last_instance.kwargs["json"]["token"] == "secret-banxico-token"
    audit_kwargs = cartridges.audit_service.record_event.await_args.kwargs
    assert audit_kwargs["metadata"]["masked_values"]["token"] == "***"
    assert "secret-banxico-token" not in json.dumps(audit_kwargs)


@pytest.mark.asyncio
async def test_non_bootstrap_credentials_still_respect_workspace_cartridge_scope(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "security_context_signing_key_distinct_64_chars_router")
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_VAULT", "console_to_vault_key")

    with pytest.raises(HTTPException) as exc:
        await cartridges.save_credentials(
            "hubspot",
            {"auth_method": "bearer_token", "token": "secret-token"},
            _workspace_request_without_banxico(),
        )

    assert exc.value.status_code == 403
