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


def _request():
    return SimpleNamespace(state=SimpleNamespace(user={
        "id": 1,
        "email": "admin@example.com",
        "role": "admin",
        "allowed_cartridges": ["replicon"],
    }))


@pytest.mark.asyncio
async def test_run_entity_maps_upstream_500_to_honest_dependency_error(monkeypatch):
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
        "active_tenant_id": "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
        "active_workspace_id": "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4",
        "allowed_cartridges": ["sap_successfactors"],
    }))

    result = await cartridges.test_connection("sap_successfactors", request, conn_id="femsa_sf")

    assert result["ok"] is True
    assert _FakeAsyncClient.last_instance.url.endswith("/skills/test_connection")
    assert _FakeAsyncClient.last_instance.kwargs["params"] == {"conn_id": "femsa_sf"}
    forwarded_ctx = json.loads(_FakeAsyncClient.last_instance.client_kwargs["headers"]["X-Security-Context"])
    assert forwarded_ctx["trusted"] is True
    assert forwarded_ctx["tenant_id"] == "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78"
    assert forwarded_ctx["workspace_id"] == "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4"
    assert forwarded_ctx["_signature"]
    cartridges.audit_service.record_event.assert_awaited_once()
    assert cartridges.audit_service.record_event.await_args.kwargs["metadata"]["conn_id"] == "femsa_sf"
