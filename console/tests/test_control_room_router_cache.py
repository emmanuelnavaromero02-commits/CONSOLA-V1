from __future__ import annotations

import asyncio
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
    control_room._CONTROL_ROOM_READ_CACHE_LOCKS.clear()
    yield
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    control_room._CONTROL_ROOM_READ_CACHE_LOCKS.clear()


@pytest.mark.asyncio
async def test_control_room_dashboard_cache_is_scoped_by_workspace(monkeypatch):
    monkeypatch.setenv("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "60")
    calls: list[str] = []

    async def fake_dashboard(user):
        workspace_id = user.get("active_workspace_id") or user.get("workspace_id")
        calls.append(workspace_id)
        return {
            "items": [
                {
                    "id": "dashboard",
                    "kind": "scope",
                    "count": 1 if workspace_id == USER["active_workspace_id"] else 2,
                }
            ]
        }

    monkeypatch.setattr(control_room.control_room_service, "dashboard", fake_dashboard)

    first = await control_room.control_room_dashboard(USER)
    second = await control_room.control_room_dashboard(USER)
    other = await control_room.control_room_dashboard(
        {
            **USER,
            "workspace_id": "00000000-0000-0000-0000-000000000002",
            "active_workspace_id": "00000000-0000-0000-0000-000000000002",
        }
    )

    assert first.model_dump() == second.model_dump()
    assert first.items[0].count == 1
    assert other.items[0].count == 2
    assert calls == [
        USER["active_workspace_id"],
        "00000000-0000-0000-0000-000000000002",
    ]


@pytest.mark.asyncio
async def test_control_room_gold_kpis_cache_reuses_same_scope(monkeypatch):
    monkeypatch.setenv("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "60")
    fetch = AsyncMock(return_value={"widgets": [{"id": "headcount", "value": 1288}]})
    monkeypatch.setattr(
        control_room.control_room_service, "sap_successfactors_gold_kpis", fetch
    )

    first = await control_room.control_room_sap_successfactors_gold_kpis(USER)
    second = await control_room.control_room_sap_successfactors_gold_kpis(USER)

    assert first.model_dump() == second.model_dump()
    assert first.widgets[0].id == "headcount"
    assert first.widgets[0].value == 1288
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_control_room_talent_kpis_cache_reuses_same_scope(monkeypatch):
    monkeypatch.setenv("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "60")
    fetch = AsyncMock(return_value={"profile": {"industry": "technology"}})
    monkeypatch.setattr(
        control_room.control_room_service, "sap_successfactors_talent_kpis", fetch
    )

    first = await control_room.control_room_sap_successfactors_talent_kpis(USER)
    second = await control_room.control_room_sap_successfactors_talent_kpis(USER)

    assert first.model_dump() == second.model_dump()
    assert first.profile.industry == "technology"
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_control_room_talent_9box_box_cache_is_scoped_by_box(monkeypatch):
    monkeypatch.setenv("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "60")
    fetch = AsyncMock(side_effect=lambda _user, box_id: {"box": {"box_id": box_id}})
    monkeypatch.setattr(
        control_room.control_room_service, "sap_successfactors_talent_9box_box", fetch
    )

    first = await control_room.control_room_sap_successfactors_talent_9box_box(
        "core", USER
    )
    second = await control_room.control_room_sap_successfactors_talent_9box_box(
        "core", USER
    )
    other = await control_room.control_room_sap_successfactors_talent_9box_box(
        "estrella", USER
    )

    assert first.model_dump() == second.model_dump()
    assert first.box.box_id == "core"
    assert other.box.box_id == "estrella"
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_control_room_dashboard_cache_singleflights_concurrent_cold_reads(
    monkeypatch,
):
    monkeypatch.setenv("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "60")
    fetch = AsyncMock(return_value={"items": [{"id": "sf", "kind": "signal"}]})

    async def slow_dashboard(user):
        await asyncio.sleep(0.01)
        return await fetch(user)

    monkeypatch.setattr(control_room.control_room_service, "dashboard", slow_dashboard)

    results = await asyncio.gather(
        *(control_room.control_room_dashboard(USER) for _ in range(8))
    )

    assert all(result.model_dump() == results[0].model_dump() for result in results)
    assert all(result.items[0].id == "sf" for result in results)
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_control_room_write_invalidates_cached_dashboard(monkeypatch):
    monkeypatch.setenv("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "60")
    dashboard = AsyncMock(
        side_effect=[
            {"items": [{"id": "item-1", "kind": "signal", "status": "before"}]},
            {"items": [{"id": "item-1", "kind": "signal", "status": "after"}]},
        ]
    )
    reopen = AsyncMock(
        return_value={"reopened": True, "item": {"id": "item-1", "decision_id": None}}
    )
    monkeypatch.setattr(control_room.control_room_service, "dashboard", dashboard)
    monkeypatch.setattr(control_room.control_room_service, "reopen_item", reopen)

    class _Req:
        client = None
        headers = {}

    cached = await control_room.control_room_dashboard(USER)
    assert cached.items[0].status == "before"

    await control_room.control_room_reopen_item(
        "item-1",
        _Req(),
        {"reason": "reset stale dashboard state"},
        USER,
    )

    fresh = await control_room.control_room_dashboard(USER)
    assert fresh.items[0].status == "after"
    assert dashboard.await_count == 2
