from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import cartridges


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url):
        return self._response


def _request():
    return SimpleNamespace(state=SimpleNamespace(user={"id": 1, "email": "admin@example.com"}))


@pytest.mark.asyncio
async def test_run_entity_maps_upstream_500_to_honest_dependency_error(monkeypatch):
    response = SimpleNamespace(status_code=500, text='{"detail":"internal error"}')
    monkeypatch.setattr(cartridges.httpx, "AsyncClient", lambda **_: _FakeAsyncClient(response))
    monkeypatch.setattr(cartridges.audit_service, "record_event", AsyncMock())

    with pytest.raises(HTTPException) as exc:
        await cartridges.run_entity("replicon", "Department", _request())

    assert exc.value.status_code == 424
    assert exc.value.detail["error"] == "cartridge_not_ready"
    assert exc.value.detail["upstream_status"] == 500
    cartridges.audit_service.record_event.assert_awaited_once()
