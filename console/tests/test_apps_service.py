from __future__ import annotations

import pytest

from app.domains.apps.service import apps_payload_visible_and_ready


def _payload():
    return {
        "apps": [
            {
                "name": "sap_successfactors_talent",
                "cartridge": "sap_successfactors",
                "datasets_used": ["talent_operational_features"],
            },
            {
                "name": "hubspot_forecast",
                "cartridge": "hubspot",
                "datasets_used": ["forecast_mensual"],
            },
        ]
    }


@pytest.mark.asyncio
async def test_apps_payload_visible_and_ready_filters_active_and_ready_apps():
    async def _load(_user):
        return _payload()

    async def _active(_user, candidates):
        assert candidates == {"sap_successfactors", "hubspot"}
        return {"sap_successfactors"}

    async def _installed(_user, _candidates):
        raise AssertionError("installed fallback should not run with active cartridges")

    async def _ready(_user, scoped_payload):
        assert scoped_payload["active_scoped_cartridges"] == ["sap_successfactors"]
        return {"talent_operational_features"}, "checked"

    result = await apps_payload_visible_and_ready(
        {"id": 1},
        load_apps_payload=_load,
        require_cartridge_visible=lambda _user, _cartridge: None,
        active_scoped_connection_cartridges=_active,
        installed_scoped_app_cartridges=_installed,
        gold_ready_datasets_for_apps=_ready,
    )

    assert [app["name"] for app in result["apps"]] == ["sap_successfactors_talent"]
    assert result["apps_scope"]["mode"] == "active_connections"
    assert result["apps_readiness"]["mode"] == "gold_ready"


@pytest.mark.asyncio
async def test_apps_payload_visible_and_ready_uses_installed_fallback_for_unready_view():
    async def _load(_user):
        return _payload()

    async def _active(_user, _candidates):
        return set()

    async def _installed(_user, candidates):
        assert candidates == {"sap_successfactors", "hubspot"}
        return {"hubspot"}

    async def _ready(_user, _scoped_payload):
        return set(), "checked"

    result = await apps_payload_visible_and_ready(
        {"id": 1},
        include_unready=True,
        load_apps_payload=_load,
        require_cartridge_visible=lambda _user, _cartridge: None,
        active_scoped_connection_cartridges=_active,
        installed_scoped_app_cartridges=_installed,
        gold_ready_datasets_for_apps=_ready,
    )

    assert [app["name"] for app in result["apps"]] == ["hubspot_forecast"]
    assert result["apps"][0]["data_status"] == "unready"
    assert result["apps_scope"]["mode"] == "installed_cartridges"


@pytest.mark.asyncio
async def test_apps_payload_visible_and_ready_scopes_requested_cartridge():
    requested: list[str] = []

    async def _load(_user):
        return _payload()

    async def _active(_user, candidates):
        assert candidates == {"hubspot"}
        return {"hubspot"}

    async def _installed(_user, _candidates):
        return set()

    async def _ready(_user, _scoped_payload):
        return {"forecast_mensual"}, "checked"

    result = await apps_payload_visible_and_ready(
        {"id": 1},
        cartridge="hubspot",
        load_apps_payload=_load,
        require_cartridge_visible=lambda _user, cartridge: requested.append(cartridge),
        active_scoped_connection_cartridges=_active,
        installed_scoped_app_cartridges=_installed,
        gold_ready_datasets_for_apps=_ready,
    )

    assert requested == ["hubspot"]
    assert [app["name"] for app in result["apps"]] == ["hubspot_forecast"]
