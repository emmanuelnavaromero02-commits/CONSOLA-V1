from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import app.main as console_main
from app.services import control_room_service


USER = {
    "id": 7,
    "email": "emmanuelnavaromero02@gmail.com",
    "role": "super_admin",
    "tenant_id": "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
    "active_tenant_id": "b95f4d58-c9c8-4fd5-8d07-ddde294c7d78",
    "workspace_id": "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4",
    "active_workspace_id": "a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4",
    "allowed_cartridges": [
        "hubspot",
        "replicon",
        "salesforce",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
    ],
}


def test_bronze_query_rewrites_logical_raw_paths_to_scoped_s3(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "modecissions-lakehouse-783792")
    sql = "select * from read_parquet('raw/sap_successfactors/PerPerson') limit 50"

    rewritten = console_main._rewrite_bronze_logical_paths(sql, USER)

    assert "raw/sap_successfactors/PerPerson')" not in rewritten
    assert (
        "s3://modecissions-lakehouse-783792/raw/sap_successfactors/PerPerson/"
        "tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/"
        "workspace_id=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4/**/*.parquet"
    ) in rewritten


@pytest.mark.asyncio
async def test_apps_list_filters_to_active_scoped_vault_cartridges(monkeypatch):
    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse({
                "apps": [
                    {"name": "sap_hcm_people_quality_dashboard", "cartridge": "sap_hcm"},
                    {"name": "sap_successfactors_workforce_overview", "cartridge": "sap_successfactors"},
                    {"name": "salesforce_pipeline", "cartridge": "salesforce"},
                ]
            })

        async def get(self, url, **_kwargs):
            if str(url).endswith("/connections/sap_successfactors"):
                return FakeResponse({"connections": [{"conn_id": "femsa_sf", "auth_method": "saml_bearer_assertion"}]})
            return FakeResponse({"connections": []})

    monkeypatch.setattr(console_main, "_get_db_pool", AsyncMock(side_effect=AssertionError("vault_entries must not be read by Console")))
    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeClient)

    result = await console_main.api_apps(USER)

    assert result["apps"] == [
        {"name": "sap_successfactors_workforce_overview", "cartridge": "sap_successfactors"}
    ]
    assert result["active_scoped_cartridges"] == ["sap_successfactors"]


@pytest.mark.asyncio
async def test_apps_list_resolves_scope_from_membership_when_user_is_unscoped(monkeypatch):
    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse({
                "apps": [
                    {"name": "sap_hcm_people_quality_dashboard", "cartridge": "sap_hcm"},
                    {"name": "sap_successfactors_workforce_overview", "cartridge": "sap_successfactors"},
                    {"name": "salesforce_pipeline", "cartridge": "salesforce"},
                ]
            })

        async def get(self, url, **_kwargs):
            if str(url).endswith("/connections/sap_successfactors"):
                return FakeResponse({"connections": [{"conn_id": "femsa_sf"}]})
            return FakeResponse({"connections": []})

    monkeypatch.setattr(console_main, "_get_db_pool", AsyncMock(side_effect=AssertionError("vault_entries must not be read by Console")))
    monkeypatch.setattr(
        console_main,
        "_workspace_memberships",
        AsyncMock(return_value=[
            {
                "workspace_id": USER["active_workspace_id"],
                "tenant_id": USER["active_tenant_id"],
                "workspace_role": "workspace_admin",
            }
        ]),
    )
    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeClient)

    unscoped_super_admin = {
        "id": USER["id"],
        "email": USER["email"],
        "role": "super_admin",
        "allowed_cartridges": USER["allowed_cartridges"],
    }
    result = await console_main.api_apps(unscoped_super_admin)

    assert result["apps"] == [
        {"name": "sap_successfactors_workforce_overview", "cartridge": "sap_successfactors"}
    ]
    assert result["active_scoped_cartridges"] == ["sap_successfactors"]


@pytest.mark.asyncio
async def test_apps_list_global_super_admin_uses_vault_service_for_scoped_connections(monkeypatch):
    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse({
                "apps": [
                    {"name": "sap_hcm_people_quality_dashboard"},
                    {"name": "sap_successfactors_workforce_overview"},
                    {"name": "salesforce_pipeline"},
                ]
            })

        async def get(self, url, **_kwargs):
            if str(url).endswith("/connections/sap_successfactors"):
                return FakeResponse({"connections": [{"conn_id": "femsa_sf", "auth_method": "saml_bearer_assertion"}]})
            return FakeResponse({"connections": []})

    monkeypatch.setattr(console_main, "_get_db_pool", AsyncMock(side_effect=AssertionError("vault_entries must not be read by Console")))
    monkeypatch.setattr(console_main, "_workspace_memberships", AsyncMock(return_value=[]))
    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeClient)

    result = await console_main.api_apps({
        "id": USER["id"],
        "email": USER["email"],
        "role": "super_admin",
        "active_tenant_id": USER["active_tenant_id"],
        "active_workspace_id": USER["active_workspace_id"],
        "allowed_cartridges": USER["allowed_cartridges"],
    })

    assert result["apps"] == [{"name": "sap_successfactors_workforce_overview"}]
    assert result["active_scoped_cartridges"] == ["sap_successfactors"]


@pytest.mark.asyncio
async def test_apps_filter_preserves_catalog_fallback_when_no_connections(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"apps": [{"name": "sap_hcm_people_quality_dashboard", "cartridge": "sap_hcm"}]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(console_main, "_get_db_pool", AsyncMock(side_effect=AssertionError("vault_entries must not be read by Console")))
    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeClient)

    result = await console_main.api_apps(USER)

    assert result["apps"] == [{"name": "sap_hcm_people_quality_dashboard", "cartridge": "sap_hcm"}]
    assert "active_scoped_cartridges" not in result


@pytest.mark.asyncio
async def test_control_room_query_prefers_scoped_gold_fetcher(monkeypatch):
    from app.services.intelligence import gold_fetcher

    async def fake_gold_rows(dataset: str, user: dict | None, limit: int):
        assert dataset == "sap_successfactors_headcount_by_department"
        assert user is USER
        assert limit == 10
        return [{"department_id": "HR", "department_name": "People", "headcount": 1288}]

    class RefinementMustNotBeCalled:
        def __init__(self, **_kwargs):
            raise AssertionError("Control Room should use scoped Gold before Refinement")

    monkeypatch.setattr(gold_fetcher, "query_gold_dataset_rows", fake_gold_rows)
    monkeypatch.setattr(control_room_service.httpx, "AsyncClient", RefinementMustNotBeCalled)

    rows = await control_room_service.query_dataset_rows(
        "sap_successfactors_headcount_by_department",
        USER,
        10,
    )

    assert rows == [{"department_id": "HR", "department_name": "People", "headcount": 1288}]


@pytest.mark.asyncio
async def test_control_room_production_hides_known_non_ready_sources(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    async def fetcher(dataset: str, _user: dict | None, _limit: int):
        if dataset == "sap_successfactors_headcount_by_department":
            return [{"department_id": "HR", "department_name": "People", "headcount": 1288}]
        if dataset == "sap_successfactors_manager_hierarchy":
            return [{"manager_id": "M1", "direct_reports": 4}]
        if dataset == "sap_successfactors_org_structure":
            return [{"department_id": "HR", "department_name": "People"}]
        raise HTTPException(404, f"non-ready source should be hidden: {dataset}")

    mock_pool = AsyncMock()
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_installed_cartridges",
            new=AsyncMock(return_value=[
                {
                    "cartridge_id": "sap_successfactors",
                    "installation_status": "ready",
                    "connection_id": "femsa_sf",
                    "auth_method": "saml_bearer_assertion",
                },
            ]),
        ),
    ):
        result = await control_room_service.dashboard(USER, fetcher=fetcher)

    source_names = {source["dataset"] for source in result["sources"]}
    assert source_names == {
        "sap_successfactors_headcount_by_department",
        "sap_successfactors_manager_hierarchy",
        "sap_successfactors_org_structure",
    }
    assert not any(item["kind"] == "source_state" for item in result["items"])
