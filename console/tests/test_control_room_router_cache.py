from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.routers import control_room


USER = {
    "id": 7,
    "email": "emmanuelnavaromero02@gmail.com",
    "role": "super_admin",
    "tenant_id": "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
    "active_tenant_id": "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
    "workspace_id": "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4",
    "active_workspace_id": "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4",
    "allowed_cartridges": ["sap_successfactors"],
}


@pytest.fixture(autouse=True)
def _clear_control_room_cache():
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    yield
    control_room._CONTROL_ROOM_READ_CACHE.clear()


@pytest.mark.asyncio
async def test_control_room_dashboard_cache_is_scoped_by_workspace(monkeypatch):
    monkeypatch.setenv("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "60")
    calls: list[str] = []

    async def fake_dashboard(user):
        workspace_id = user.get("active_workspace_id") or user.get("workspace_id")
        calls.append(workspace_id)
        return {"workspace_id": workspace_id, "items": []}

    monkeypatch.setattr(control_room.control_room_service, "dashboard", fake_dashboard)

    first = await control_room.control_room_dashboard(USER)
    second = await control_room.control_room_dashboard(USER)
    other = await control_room.control_room_dashboard({
        **USER,
        "workspace_id": "00000000-0000-0000-0000-000000000002",
        "active_workspace_id": "00000000-0000-0000-0000-000000000002",
    })

    assert first == second
    assert other != first
    assert calls == [
        USER["active_workspace_id"],
        "00000000-0000-0000-0000-000000000002",
    ]


@pytest.mark.asyncio
async def test_control_room_gold_kpis_cache_reuses_same_scope(monkeypatch):
    monkeypatch.setenv("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "60")
    fetch = AsyncMock(return_value={"headcount": 1288})
    monkeypatch.setattr(control_room.control_room_service, "sap_successfactors_gold_kpis", fetch)

    first = await control_room.control_room_sap_successfactors_gold_kpis(USER)
    second = await control_room.control_room_sap_successfactors_gold_kpis(USER)

    assert first == second == {"headcount": 1288}
    assert fetch.await_count == 1
