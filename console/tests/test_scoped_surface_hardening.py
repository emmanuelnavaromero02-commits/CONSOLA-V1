from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

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


@pytest.fixture(autouse=True)
def _clear_scoped_read_cache():
    console_main._SCOPED_READ_CACHE.clear()
    console_main._SCOPED_READ_CACHE_LOCKS.clear()
    yield
    console_main._SCOPED_READ_CACHE.clear()
    console_main._SCOPED_READ_CACHE_LOCKS.clear()


def test_bronze_query_rewrites_logical_raw_paths_to_scoped_s3(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "modecissions-lakehouse-783792")
    sql = "select * from read_parquet('raw/sap_successfactors/PerPerson') limit 50"

    rewritten = console_main._rewrite_bronze_logical_paths(sql, USER)

    assert "raw/sap_successfactors/PerPerson')" not in rewritten
    assert "read_parquet(read_parquet" not in rewritten
    assert "read_parquet('s3://modecissions-lakehouse-783792/" in rewritten
    assert "hive_partitioning=true, union_by_name=true" in rewritten
    assert (
        "s3://modecissions-lakehouse-783792/raw/sap_successfactors/PerPerson/"
        "tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/"
        "workspace_id=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4/**/*.parquet"
    ) in rewritten


def test_infers_bronze_sources_from_packaged_s3_reader():
    sql = (
        "select * from read_parquet("
        "'s3://{bucket}/raw/sap_successfactors/Candidate/**/*.parquet', "
        "hive_partitioning=true, union_by_name=true)"
    )

    assert console_main._infer_bronze_sources_from_sql(sql) == [
        "raw/sap_successfactors/Candidate"
    ]


@pytest.mark.asyncio
async def test_bronze_query_endpoint_sends_scoped_s3_to_refinement(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "modecissions-lakehouse-783792")
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"rows": [{"personIdExternal": "1"}]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, _url, json=None, **_kwargs):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeClient)

    result = await console_main.api_bronze_query(
        {"sql": "select * from read_parquet('raw/sap_successfactors/PerPerson') limit 20"},
        USER,
    )

    sql = captured["payload"]["args"]["sql"]
    assert result == {"rows": [{"personIdExternal": "1"}]}
    assert "read_parquet('raw/sap_successfactors/PerPerson')" not in sql
    assert (
        "s3://modecissions-lakehouse-783792/raw/sap_successfactors/PerPerson/"
        "tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/"
        "workspace_id=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4/**/*.parquet"
    ) in sql
    assert captured["payload"]["args"]["user_context"]["tenant_id"] == USER["active_tenant_id"]
    assert captured["payload"]["args"]["user_context"]["workspace_id"] == USER["active_workspace_id"]


@pytest.mark.asyncio
async def test_bronze_query_endpoint_infers_sources_from_packaged_silver_sql(monkeypatch):
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"schema": [], "data": []}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, _url, json=None, **_kwargs):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeClient)
    sql = (
        "select * from read_parquet("
        "'s3://{bucket}/raw/sap_successfactors/Candidate/**/*.parquet', "
        "hive_partitioning=true, union_by_name=true)"
    )

    await console_main.api_bronze_query({"sql": sql, "limit": 50, "sources": []}, USER)

    assert captured["payload"]["args"]["sources"] == [
        "raw/sap_successfactors/Candidate"
    ]


@pytest.mark.asyncio
async def test_dataset_save_infers_sources_from_packaged_silver_sql(monkeypatch):
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"saved": True}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, _url, json=None, **_kwargs):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeClient)
    sql = (
        "select * from read_parquet("
        "'s3://{bucket}/raw/sap_successfactors/Candidate/**/*.parquet', "
        "hive_partitioning=true, union_by_name=true)"
    )

    result = await console_main.api_dataset_save(
        {
            "name": "sap_successfactors_candidate_latest",
            "layer": "silver",
            "sql": sql,
            "cartridge": "sap_successfactors",
            "sources": [],
        },
        USER,
    )

    assert result == {"saved": True}
    assert captured["payload"]["args"]["sources"] == [
        "raw/sap_successfactors/Candidate"
    ]


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
async def test_workspace_app_proxy_redirects_inactive_cartridge_deep_links(monkeypatch):
    async def fake_active(_user, _candidates=None):
        return {"sap_successfactors"}

    monkeypatch.setattr(console_main, "_active_scoped_connection_cartridges", fake_active)
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/apps/sap_hcm_people_quality_dashboard",
        "headers": [],
    })
    request.state.user = USER

    response = await console_main._proxy_workspace_app(request, "sap_hcm_people_quality_dashboard")

    assert response.status_code == 303
    assert response.headers["location"] == "/apps-gallery"


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
async def test_pipeline_rejects_inactive_cartridge_when_scoped_connection_exists(monkeypatch):
    async def fake_active(_user, _candidates=None):
        return {"sap_successfactors"}

    monkeypatch.setattr(console_main, "_active_scoped_connection_cartridges", fake_active)

    with pytest.raises(HTTPException) as exc:
        await console_main.api_pipeline("replicon", USER)

    assert exc.value.status_code == 403
    assert "active" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_semantic_rejects_inactive_cartridge_when_scoped_connection_exists(monkeypatch):
    async def fake_active(_user, _candidates=None):
        return {"sap_successfactors"}

    monkeypatch.setattr(console_main, "_active_scoped_connection_cartridges", fake_active)

    with pytest.raises(HTTPException) as exc:
        await console_main.api_semantic("replicon", USER)

    assert exc.value.status_code == 403
    assert "active" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_catalog_defaults_to_active_scoped_cartridge(monkeypatch):
    async def fake_active(_user, _candidates=None):
        return {"sap_successfactors"}

    captured: dict = {}

    async def fake_refinement(tool, args, user=None, **_kwargs):
        captured["tool"] = tool
        captured["args"] = args
        captured["user"] = user
        return {"datasets": []}

    monkeypatch.setattr(console_main, "_active_scoped_connection_cartridges", fake_active)
    monkeypatch.setattr(console_main, "_refinement_invoke", fake_refinement)

    result = await console_main.api_catalog_get(layer="gold", user=USER)

    assert result == {"datasets": []}
    assert captured["tool"] == "get_data_catalog"
    assert captured["args"]["cartridge"] == "sap_successfactors"
    assert captured["user"] is USER


@pytest.mark.asyncio
async def test_semantic_enrich_uses_direct_catalog_tools(monkeypatch):
    async def fake_active(_user, _candidates=None):
        return {"sap_successfactors"}

    calls: list[tuple[str, dict]] = []

    async def fake_refinement(tool, args, user=None, **_kwargs):
        calls.append((tool, args))
        if tool == "get_data_catalog":
            return {
                "datasets": {
                    "sap_successfactors_talent_operational_features": {
                        "layer": "gold",
                        "cartridge": "sap_successfactors",
                        "columns": [
                            {"name": "employee_count", "description": ""},
                            {"name": "workspace_id", "description": "Workspace"},
                        ],
                    }
                }
            }
        if tool == "upsert_catalog_entries":
            return {"updated": len(args["entries"])}
        raise AssertionError(f"unexpected tool {tool}")

    monkeypatch.setattr(console_main, "_active_scoped_connection_cartridges", fake_active)
    monkeypatch.setattr(console_main, "_refinement_invoke", fake_refinement)

    result = await console_main.api_semantic_enrich(
        {"cartridge": "sap_successfactors", "limit": 10},
        user=USER,
    )

    assert result["approval_required"] is False
    assert result["enriched"] == 1
    assert [tool for tool, _args in calls] == [
        "get_data_catalog",
        "upsert_catalog_entries",
    ]
    entry = calls[1][1]["entries"][0]
    assert entry["dataset"] == "sap_successfactors_talent_operational_features"
    assert entry["column_name"] == "employee_count"
    assert entry["is_metric"] is True


@pytest.mark.asyncio
async def test_semantic_enrich_skips_when_catalog_has_no_missing_descriptions(monkeypatch):
    async def fake_active(_user, _candidates=None):
        return {"sap_successfactors"}

    calls: list[str] = []

    async def fake_refinement(tool, args, user=None, **_kwargs):
        calls.append(tool)
        return {
            "datasets": {
                "gold_ready": {
                    "layer": "gold",
                    "cartridge": "sap_successfactors",
                    "columns": [{"name": "employee_count", "description": "Total"}],
                }
            }
        }

    monkeypatch.setattr(console_main, "_active_scoped_connection_cartridges", fake_active)
    monkeypatch.setattr(console_main, "_refinement_invoke", fake_refinement)

    result = await console_main.api_semantic_enrich({"cartridge": "sap_successfactors"}, user=USER)

    assert result["enriched"] == 0
    assert result["approval_required"] is False
    assert calls == ["get_data_catalog"]


@pytest.mark.asyncio
async def test_catalog_cache_is_scoped_by_workspace(monkeypatch):
    console_main._SCOPED_READ_CACHE.clear()
    monkeypatch.setenv("OMEGA_SCOPED_READ_CACHE_TTL_SECONDS", "60")

    async def fake_active(_user, _candidates=None):
        return {"sap_successfactors"}

    calls: list[tuple[str | None, dict]] = []

    async def fake_refinement(_tool, args, user=None, **_kwargs):
        calls.append((user.get("active_workspace_id") or user.get("workspace_id"), dict(args)))
        return {"datasets": [{"workspace_id": calls[-1][0]}]}

    monkeypatch.setattr(console_main, "_active_scoped_connection_cartridges", fake_active)
    monkeypatch.setattr(console_main, "_refinement_invoke", fake_refinement)

    first = await console_main.api_catalog_get(layer="gold", user=USER)
    second = await console_main.api_catalog_get(layer="gold", user=USER)

    other_user = {
        **USER,
        "workspace_id": "00000000-0000-0000-0000-000000000002",
        "active_workspace_id": "00000000-0000-0000-0000-000000000002",
    }
    third = await console_main.api_catalog_get(layer="gold", user=other_user)

    assert first == second
    assert third != first
    assert len(calls) == 2
    assert calls[0][0] == USER["active_workspace_id"]
    assert calls[1][0] == other_user["active_workspace_id"]


@pytest.mark.asyncio
async def test_catalog_cache_singleflights_concurrent_cold_reads(monkeypatch):
    console_main._SCOPED_READ_CACHE.clear()
    console_main._SCOPED_READ_CACHE_LOCKS.clear()
    monkeypatch.setenv("OMEGA_SCOPED_READ_CACHE_TTL_SECONDS", "60")

    async def fake_active(_user, _candidates=None):
        return {"sap_successfactors"}

    calls: list[dict] = []

    async def fake_refinement(_tool, args, user=None, **_kwargs):
        await asyncio.sleep(0.01)
        calls.append(dict(args))
        return {"datasets": [{"name": "sap_successfactors_employee_360"}]}

    monkeypatch.setattr(console_main, "_active_scoped_connection_cartridges", fake_active)
    monkeypatch.setattr(console_main, "_refinement_invoke", fake_refinement)

    results = await asyncio.gather(*(console_main.api_catalog_get(layer="gold", user=USER) for _ in range(8)))

    assert results == [{"datasets": [{"name": "sap_successfactors_employee_360"}]}] * 8
    assert calls == [{"layer": "gold", "cartridge": "sap_successfactors"}]


@pytest.mark.asyncio
async def test_sources_cache_is_scoped_by_workspace(monkeypatch):
    console_main._SCOPED_READ_CACHE.clear()
    monkeypatch.setenv("OMEGA_SCOPED_READ_CACHE_TTL_SECONDS", "60")
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, _url, *, json):
            ctx = json.get("security_context") or {}
            workspace_id = ctx.get("workspace_id")
            calls.append(workspace_id)
            return FakeResponse({"sources": [f"raw/sap_successfactors/{workspace_id}"]})

    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeClient)

    first = await console_main.api_sources(USER)
    second = await console_main.api_sources(USER)
    other_user = {
        **USER,
        "workspace_id": "00000000-0000-0000-0000-000000000002",
        "active_workspace_id": "00000000-0000-0000-0000-000000000002",
    }
    third = await console_main.api_sources(other_user)

    assert first == second
    assert third != first
    assert calls == [
        USER["active_workspace_id"],
        other_user["active_workspace_id"],
    ]


@pytest.mark.asyncio
async def test_catalog_rejects_inactive_cartridge_when_scoped_connection_exists(monkeypatch):
    async def fake_active(_user, _candidates=None):
        return {"sap_successfactors"}

    monkeypatch.setattr(console_main, "_active_scoped_connection_cartridges", fake_active)

    with pytest.raises(HTTPException) as exc:
        await console_main.api_catalog_get(layer="gold", cartridge="replicon", user=USER)

    assert exc.value.status_code == 403
    assert "active" in str(exc.value.detail)


def test_explorer_allows_scoped_ancestors_but_rejects_foreign_objects():
    assert console_main._explorer_path_allowed("raw/sap_successfactors/", USER)
    assert console_main._explorer_path_allowed("raw/sap_successfactors/PerPerson/", USER)
    assert console_main._explorer_path_allowed(
        (
            "raw/sap_successfactors/PerPerson/"
            "tenant_id=b95f4d58-c9c8-4fd5-8d07-ddde294c7d78/"
            "workspace_id=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4/data.parquet"
        ),
        USER,
        object_access=True,
    )
    assert not console_main._explorer_path_allowed(
        (
            "raw/sap_successfactors/PerPerson/"
            "tenant_id=other/workspace_id=a2b1ced2-4d92-4bbe-8f9f-9a7cc88bb9f4/data.parquet"
        ),
        USER,
        object_access=True,
    )


@pytest.mark.asyncio
async def test_apps_filter_blocks_catalog_fallback_when_no_connections(monkeypatch):
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

    assert result["apps"] == []
    assert result["active_scoped_cartridges"] == []
    assert result["apps_scope"]["mode"] == "no_active_connections"


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
async def test_control_room_production_reports_known_non_ready_sources_without_fetching(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    async def fetcher(dataset: str, _user: dict | None, _limit: int):
        if dataset == "sap_successfactors_headcount_by_department":
            return [{"department_id": "HR", "department_name": "People", "headcount": 1288}]
        if dataset == "sap_successfactors_manager_hierarchy":
            return [{"manager_id": "M1", "direct_reports": 4}]
        if dataset == "sap_successfactors_org_structure":
            return [{"department_id": "HR", "department_name": "People"}]
        raise HTTPException(404, f"non-ready source should not be fetched: {dataset}")

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
    assert {
        "sap_successfactors_headcount_by_department",
        "sap_successfactors_manager_hierarchy",
        "sap_successfactors_org_structure",
        "sap_successfactors_employees_anomalies",
        "sap_successfactors_turnover_by_period",
        "sap_successfactors_recruitment_funnel",
        "sap_successfactors_recruitment_pipeline",
        "sap_successfactors_compensation_distribution",
    }.issubset(source_names)
    readiness_by_source = {source["dataset"]: source["data_readiness"] for source in result["sources"]}
    assert readiness_by_source["sap_successfactors_headcount_by_department"] == "ready"
    assert readiness_by_source["sap_successfactors_manager_hierarchy"] == "ready"
    assert readiness_by_source["sap_successfactors_org_structure"] == "ready"
    assert readiness_by_source["sap_successfactors_employees_anomalies"] == "partial"
    assert readiness_by_source["sap_successfactors_turnover_by_period"] == "partial"
    assert readiness_by_source["sap_successfactors_recruitment_funnel"] == "partial"
    assert readiness_by_source["sap_successfactors_recruitment_pipeline"] == "partial"
    assert readiness_by_source["sap_successfactors_compensation_distribution"] == "stub"
    assert any(item["kind"] == "source_state" for item in result["items"])
