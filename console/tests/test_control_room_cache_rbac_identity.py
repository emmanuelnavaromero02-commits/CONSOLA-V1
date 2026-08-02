from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.routers import control_room
from app.services import permissions
from app.services.control_room import authorization_cache
from app.services.control_room.cache_identity import authorization_cache_identity


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
    monkeypatch.setattr(
        authorization_cache,
        "publication_epoch",
        AsyncMock(return_value="publication-head-1"),
    )
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
        return {"period": user["workspace_role"]}

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

    assert admin_result.period == "workspace_admin"
    assert analyst_result.period == "analyst"
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
        ({"id": 18}, "id"),
        (
            {"tenant_id": "tenant-b", "active_tenant_id": "tenant-b"},
            "active_tenant_id",
        ),
        (
            {
                "workspace_id": "workspace-b",
                "active_workspace_id": "workspace-b",
            },
            "active_workspace_id",
        ),
        ({"allowed_cartridges": ["replicon"]}, "allowed_cartridges"),
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

    assert first == second
    first_user = loader.await_args_list[0].args[0]
    second_user = loader.await_args_list[1].args[0]
    assert first_user[expected_field] != second_user[expected_field]
    assert loader.await_count == 2


@pytest.mark.asyncio
async def test_access_revision_change_uses_a_distinct_cache_key(monkeypatch):
    loader = _dashboard_loader(monkeypatch)

    first = await control_room.control_room_dashboard(_user(access_revision="41"))
    second = await control_room.control_room_dashboard(_user(access_revision="42"))

    assert first == second
    assert loader.await_count == 2


@pytest.mark.asyncio
async def test_publication_head_change_uses_a_distinct_cache_key(monkeypatch):
    epoch = AsyncMock(side_effect=["publication-head-1", "publication-head-2"])
    monkeypatch.setattr(authorization_cache, "publication_epoch", epoch)
    loader = _dashboard_loader(monkeypatch)

    await control_room.control_room_dashboard(_user())
    await control_room.control_room_dashboard(_user())

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
        return {"period": role_at_authorization}

    loader = AsyncMock(side_effect=response)
    monkeypatch.setattr(control_room.control_room_service, "dashboard", loader)
    user = _user(workspace_role="workspace_admin")

    admin_request = asyncio.create_task(control_room.control_room_dashboard(user))
    await started.wait()
    user["workspace_role"] = "analyst"
    release.set()

    assert (await admin_request).period == "workspace_admin"
    assert (await control_room.control_room_dashboard(user)).period == "analyst"
    assert loader.await_count == 2


def test_authorization_identity_is_complete_ordered_and_stable():
    first = _user(
        role="admin",
        workspace_role="admin",
        allowed_cartridges=["sec_edgar", "banxico", "sec_edgar"],
        _effective_permissions=[
            "datasets.read",
            "control_room.write",
            "datasets.read",
        ],
        access_revision={"workspace": 9, "tenant": 3},
    )
    second = _user(
        role="admin",
        workspace_role="workspace_admin",
        allowed_cartridges=["banxico", "sec_edgar"],
        _effective_permissions=["control_room.write", "datasets.read"],
        access_revision={"tenant": 3, "workspace": 9},
    )

    first_identity = authorization_cache_identity(first)
    second_identity = authorization_cache_identity(second)

    assert first_identity == second_identity
    assert first_identity.tenant_id == "tenant-a"
    assert first_identity.workspace_id == "workspace-a"
    assert first_identity.user_id == "17"
    assert first_identity.global_role == "admin"
    assert first_identity.workspace_role == "workspace_admin"
    assert first_identity.effective_permissions == (
        "control_room.write",
        "datasets.read",
    )
    assert first_identity.allowed_cartridges == ("banxico", "sec_edgar")
    assert first_identity.access_revisions == (
        ("access_revision", '{"tenant":3,"workspace":9}'),
    )


@pytest.mark.asyncio
async def test_global_role_permission_and_cartridge_mutations_miss_cache(monkeypatch):
    loader = _dashboard_loader(monkeypatch)
    user = _user(
        role="user",
        workspace_role="analyst",
        _effective_permissions=["datasets.read"],
    )

    await control_room.control_room_dashboard(user)
    user["role"] = "admin"
    await control_room.control_room_dashboard(user)
    user["_effective_permissions"] = ["control_room.write", "datasets.read"]
    await control_room.control_room_dashboard(user)
    user["allowed_cartridges"] = ["banxico", "sap_successfactors"]
    await control_room.control_room_dashboard(user)

    assert loader.await_count == 4


@pytest.mark.asyncio
async def test_active_ttl_reuses_then_expires_authorized_payload(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(
        authorization_cache.time,
        "monotonic",
        lambda: clock["now"],
    )
    loader = AsyncMock(
        side_effect=[
            {"meta": {"version": "1"}},
            {"meta": {"version": "2"}},
        ]
    )
    monkeypatch.setattr(control_room.control_room_service, "dashboard", loader)

    first = await control_room.control_room_dashboard(_user())
    assert first.meta.version == "1"
    clock["now"] = 159.0
    reused = await control_room.control_room_dashboard(_user())
    assert reused.model_dump() == first.model_dump()
    clock["now"] = 161.0
    expired = await control_room.control_room_dashboard(_user())
    assert expired.meta.version == "2"
    assert loader.await_count == 2
