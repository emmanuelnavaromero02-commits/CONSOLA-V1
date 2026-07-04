from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.apps.service import (
    apps_payload_visible_and_ready,
    delete_refinement_app_payload,
    load_refinement_apps_payload,
    refinement_app_html,
)


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


@pytest.mark.asyncio
async def test_load_refinement_apps_payload_lists_apps_from_refinement():
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"apps": [{"name": "talent"}]}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    payload = await load_refinement_apps_payload(
        user={"id": 1},
        http_client_factory=FakeClient,
        headers_factory=lambda service: {"x-service": service},
        mcp_payload=lambda tool, args, user: {
            "tool": tool,
            "args": args,
            "user": user,
        },
        refinement_url="http://refinement",
        upstream_error_detail=lambda _response, fallback: fallback,
    )

    assert payload == {"apps": [{"name": "talent"}]}
    assert captured["client_kwargs"]["headers"] == {"x-service": "REFINEMENT"}
    assert captured["url"] == "http://refinement/mcp/invoke"
    assert captured["json"]["tool"] == "list_apps"
    assert captured["json"]["args"] == {}


@pytest.mark.asyncio
async def test_load_refinement_apps_payload_maps_upstream_error():
    class FakeResponse:
        status_code = 503

        def json(self):
            return {"error": "down"}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, _url, json):
            return FakeResponse()

    with pytest.raises(HTTPException) as exc:
        await load_refinement_apps_payload(
            user={"id": 1},
            http_client_factory=FakeClient,
            headers_factory=lambda _service: {},
            mcp_payload=lambda tool, args, user: {
                "tool": tool,
                "args": args,
                "user": user,
            },
            refinement_url="http://refinement",
            upstream_error_detail=lambda _response, fallback: f"{fallback}: down",
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "Apps service unavailable: down"


@pytest.mark.asyncio
async def test_refinement_app_html_loads_html_from_refinement():
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"result": {"html": "<main>ok</main>", "datasets": ["gold_ready"]}}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    html, app = await refinement_app_html(
        name="talent_app",
        user={"id": 1},
        validate_dataset_name=lambda name: captured.setdefault("validated", name),
        http_client_factory=FakeClient,
        headers_factory=lambda service: {"x-service": service},
        mcp_payload=lambda tool, args, user: {
            "tool": tool,
            "args": args,
            "user": user,
        },
        refinement_url="http://refinement",
        upstream_error_detail=lambda _response, fallback: fallback,
    )

    assert html == "<main>ok</main>"
    assert app["datasets"] == ["gold_ready"]
    assert captured["validated"] == "talent_app"
    assert captured["client_kwargs"]["headers"] == {"x-service": "REFINEMENT"}
    assert captured["url"] == "http://refinement/mcp/invoke"
    assert captured["json"]["tool"] == "get_app_html"
    assert captured["json"]["args"] == {"name": "talent_app"}


@pytest.mark.asyncio
async def test_refinement_app_html_maps_empty_payload_to_not_found():
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"result": {"html": ""}}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, _url, json):
            return FakeResponse()

    with pytest.raises(HTTPException) as exc:
        await refinement_app_html(
            name="empty_app",
            user={"id": 1},
            validate_dataset_name=lambda _name: None,
            http_client_factory=FakeClient,
            headers_factory=lambda _service: {},
            mcp_payload=lambda tool, args, user: {
                "tool": tool,
                "args": args,
                "user": user,
            },
            refinement_url="http://refinement",
            upstream_error_detail=lambda _response, fallback: fallback,
        )

    assert exc.value.status_code == 404
    assert "no HTML content" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_delete_refinement_app_payload_deletes_via_refinement():
    captured = {}

    class FakeResponse:
        def json(self):
            return {"result": {"deleted": True, "name": "talent_app"}}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    result = await delete_refinement_app_payload(
        name="talent_app",
        user={"id": 1},
        http_client_factory=FakeClient,
        headers_factory=lambda service: {"x-service": service},
        mcp_payload=lambda tool, args, user: {
            "tool": tool,
            "args": args,
            "user": user,
        },
        refinement_url="http://refinement",
    )

    assert result == {"deleted": True, "name": "talent_app"}
    assert captured["client_kwargs"]["headers"] == {"x-service": "REFINEMENT"}
    assert captured["url"] == "http://refinement/mcp/invoke"
    assert captured["json"]["tool"] == "delete_app"
    assert captured["json"]["args"] == {"name": "talent_app"}


@pytest.mark.asyncio
async def test_delete_refinement_app_payload_maps_missing_app_to_not_found():
    class FakeResponse:
        def json(self):
            return {"error": "missing"}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, _url, json):
            return FakeResponse()

    with pytest.raises(HTTPException) as exc:
        await delete_refinement_app_payload(
            name="missing_app",
            user={"id": 1},
            http_client_factory=FakeClient,
            headers_factory=lambda _service: {},
            mcp_payload=lambda tool, args, user: {
                "tool": tool,
                "args": args,
                "user": user,
            },
            refinement_url="http://refinement",
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "missing"
