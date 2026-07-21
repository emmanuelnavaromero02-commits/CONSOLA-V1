from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.routers import control_room
from app.services import permissions


BASE_USER = {
    "id": 17,
    "email": "analyst@example.com",
    "role": "user",
    "workspace_role": "workspace_admin",
    "tenant_id": "tenant-a",
    "active_tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "active_workspace_id": "workspace-a",
    "allowed_cartridges": ["sap_successfactors"],
}


@pytest.fixture(autouse=True)
def _ttl_cache(monkeypatch):
    monkeypatch.setenv("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "60")
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    control_room._CONTROL_ROOM_READ_CACHE_LOCKS.clear()
    yield
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    control_room._CONTROL_ROOM_READ_CACHE_LOCKS.clear()


def _user(**changes) -> dict:
    user = deepcopy(BASE_USER)
    user.update(changes)
    return user


def _dashboard_loader(monkeypatch) -> AsyncMock:
    async def response(user):
        return {
            "user_id": user["id"],
            "tenant_id": user["active_tenant_id"],
            "workspace_id": user["active_workspace_id"],
            "workspace_role": user["workspace_role"],
            "cartridges": list(user["allowed_cartridges"]),
        }

    loader = AsyncMock(side_effect=response)
    monkeypatch.setattr(control_room.control_room_service, "dashboard", loader)
    return loader


@pytest.mark.asyncio
async def test_downgraded_user_cannot_receive_workspace_admin_cache(monkeypatch):
    loader = _dashboard_loader(monkeypatch)
    admin = _user(workspace_role="workspace_admin")
    analyst = _user(workspace_role="analyst")

    admin_result = await control_room.control_room_dashboard(admin)
    analyst_result = await control_room.control_room_dashboard(analyst)

    assert admin_result["workspace_role"] == "workspace_admin"
    assert analyst_result["workspace_role"] == "analyst"
    assert loader.await_count == 2


@pytest.mark.asyncio
async def test_effective_permission_change_uses_a_distinct_cache_key(monkeypatch):
    loader = _dashboard_loader(monkeypatch)
    user = _user(workspace_role="analyst")
    original = set(permissions.ROLE_PERMISSIONS["analyst"])

    first = await control_room.control_room_dashboard(user)
    monkeypatch.setitem(
        permissions.ROLE_PERMISSIONS,
        "analyst",
        original | {"control_room.write"},
    )
    second = await control_room.control_room_dashboard(user)

    assert first == second
    assert loader.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "expected_field"),
    [
        ({"id": 18}, "user_id"),
        (
            {"tenant_id": "tenant-b", "active_tenant_id": "tenant-b"},
            "tenant_id",
        ),
        (
            {
                "workspace_id": "workspace-b",
                "active_workspace_id": "workspace-b",
            },
            "workspace_id",
        ),
        ({"allowed_cartridges": ["replicon"]}, "cartridges"),
    ],
)
async def test_authorization_scopes_never_share_cache(
    monkeypatch,
    changes,
    expected_field,
):
    loader = _dashboard_loader(monkeypatch)

    first = await control_room.control_room_dashboard(_user())
    second = await control_room.control_room_dashboard(_user(**changes))

    assert first[expected_field] != second[expected_field]
    assert loader.await_count == 2


@pytest.mark.asyncio
async def test_access_revision_change_uses_a_distinct_cache_key(monkeypatch):
    loader = _dashboard_loader(monkeypatch)

    first = await control_room.control_room_dashboard(_user(access_revision="41"))
    second = await control_room.control_room_dashboard(_user(access_revision="42"))

    assert first == second
    assert loader.await_count == 2


@pytest.mark.asyncio
async def test_inflight_downgrade_never_caches_admin_payload_for_analyst(
    monkeypatch,
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def response(user):
        role_at_authorization = user["workspace_role"]
        started.set()
        await release.wait()
        return {"workspace_role": role_at_authorization}

    loader = AsyncMock(side_effect=response)
    monkeypatch.setattr(control_room.control_room_service, "dashboard", loader)
    user = _user(workspace_role="workspace_admin")

    admin_request = asyncio.create_task(control_room.control_room_dashboard(user))
    await started.wait()
    user["workspace_role"] = "analyst"
    release.set()

    assert await admin_request == {"workspace_role": "workspace_admin"}
    assert await control_room.control_room_dashboard(user) == {
        "workspace_role": "analyst"
    }
    assert loader.await_count == 2
