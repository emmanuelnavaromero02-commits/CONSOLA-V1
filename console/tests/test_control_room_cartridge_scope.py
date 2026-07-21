from __future__ import annotations

import importlib
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import control_room as routes
from app.services import control_room_service
from app.services.control_room import business_runtime_projection


def _user(allowed: list[str]) -> dict:
    return {
        "id": 7,
        "role": "super_admin",
        "active_tenant_id": "tenant-a",
        "active_workspace_id": "workspace-a",
        "allowed_cartridges": allowed,
    }


def _business_item(item_id: str, cartridge: str) -> dict:
    return {
        "id": item_id,
        "kind": "intelligence_signal",
        "cartridge": cartridge,
        "source_dataset": "gold_metrics",
        "data_status": "ready",
        "observation_date": "2026-07-20",
        "metric_type": "count",
        "count": 1,
        "population_count": 1,
        "evidence_refs": [f"gold_metrics:{item_id}"],
    }


def test_empty_cartridge_allowlist_remains_fail_closed():
    assert control_room_service._allowed_from_user(_user([])) == set()


@pytest.mark.asyncio
async def test_persisted_agentops_projection_filters_unauthorized_cartridges(
    monkeypatch,
):
    rows = [
        _business_item("sf-signal", "sap_successfactors"),
        _business_item("sec-signal", "sec_edgar"),
        _business_item("platform-signal", "platform"),
    ]

    async def scoped(_pool, _user, work):
        return await work(object(), "tenant-a", "workspace-a")

    monkeypatch.setattr(
        business_runtime_projection,
        "fetch_eligible_persisted_items",
        AsyncMock(return_value=rows),
    )
    monkeypatch.setattr(business_runtime_projection, "run_with_db_scope", scoped)

    projected = await business_runtime_projection.persisted_business_projection(
        object(),
        _user(["sap_successfactors"]),
    )

    assert [row["id"] for row in projected] == ["sf-signal", "platform-signal"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name,function_name,route_name",
    [
        (
            "app.services.banxico_readiness",
            "banxico_readiness",
            "control_room_banxico_readiness",
        ),
        (
            "app.services.inegi_readiness",
            "inegi_readiness",
            "control_room_inegi_readiness",
        ),
        (
            "app.services.sec_edgar_readiness",
            "sec_edgar_readiness",
            "control_room_sec_edgar_readiness",
        ),
    ],
)
async def test_readiness_rejects_unauthorized_cartridge_before_query(
    monkeypatch,
    module_name,
    function_name,
    route_name,
):
    service_module = importlib.import_module(module_name)
    query = AsyncMock(return_value={"status": "ready"})
    monkeypatch.setattr(service_module, function_name, query)

    with pytest.raises(HTTPException) as exc:
        await getattr(routes, route_name)(_user(["sap_successfactors"]))

    assert exc.value.status_code == 403
    query.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name,function_name,view",
    [
        ("app.services.banxico_readiness", "banxico_readiness", "banxico_readiness"),
        ("app.services.inegi_readiness", "inegi_readiness", "inegi_readiness"),
        (
            "app.services.sec_edgar_readiness",
            "sec_edgar_readiness",
            "sec_edgar_readiness",
        ),
    ],
)
async def test_internal_readiness_rejects_unauthorized_cartridge_before_query(
    monkeypatch,
    module_name,
    function_name,
    view,
):
    service_module = importlib.import_module(module_name)
    query = AsyncMock(return_value={"status": "ready"})
    monkeypatch.setattr(service_module, function_name, query)
    routes._CONTROL_ROOM_READ_CACHE.clear()

    rejection = None
    try:
        await routes._control_room_internal_view(
            view,
            _user(["sap_successfactors"]),
            {},
        )
    except HTTPException as exc:
        rejection = exc

    query.assert_not_awaited()
    assert rejection is not None
    assert rejection.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name,function_name,view,cartridge",
    [
        (
            "app.services.banxico_readiness",
            "banxico_readiness",
            "banxico_readiness",
            "banxico",
        ),
        (
            "app.services.inegi_readiness",
            "inegi_readiness",
            "inegi_readiness",
            "inegi",
        ),
        (
            "app.services.sec_edgar_readiness",
            "sec_edgar_readiness",
            "sec_edgar_readiness",
            "sec_edgar",
        ),
    ],
)
async def test_internal_readiness_allows_authorized_cartridge(
    monkeypatch,
    module_name,
    function_name,
    view,
    cartridge,
):
    service_module = importlib.import_module(module_name)
    query = AsyncMock(return_value={"status": "ready"})
    monkeypatch.setattr(service_module, function_name, query)
    routes._CONTROL_ROOM_READ_CACHE.clear()

    result = await routes._control_room_internal_view(view, _user([cartridge]), {})

    assert result == {"status": "ready"}
    query.assert_awaited_once()
