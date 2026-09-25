import os
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
os.environ["APP_ENV"] = "test"
os.environ["INTERNAL_API_KEY"] = "x" * 64
sys.path.insert(0, str(REPO / "console"))

from app.main import (  # noqa: E402
    _app_payload_cartridge_candidates,
    _filter_apps_payload_to_ready_datasets,
    _filter_apps_payload_to_scoped_connections,
)


def test_apps_gallery_filters_to_active_successfactors_connection():
    payload = {
        "apps": [
            {"name": "sap_successfactors_workforce_overview", "title": "Workforce Overview"},
            {"name": "sap_successfactors_talent_health", "title": "Talent Health"},
            {"name": "replicon_margin_dashboard", "title": "Replicon"},
            {"name": "hubspot_pipeline_dashboard", "title": "HubSpot"},
        ]
    }

    assert _app_payload_cartridge_candidates(payload) == {
        "hubspot",
        "replicon",
        "sap_successfactors",
    }

    scoped = _filter_apps_payload_to_scoped_connections(payload, {"sap_successfactors"})

    assert [app["name"] for app in scoped["apps"]] == [
        "sap_successfactors_workforce_overview",
        "sap_successfactors_talent_health",
    ]
    assert scoped["active_scoped_cartridges"] == ["sap_successfactors"]
    assert scoped["apps_scope"]["mode"] == "active_connections"
    assert scoped["apps_scope"]["hidden_unconfigured_count"] == 2


def test_apps_gallery_does_not_fall_back_to_global_catalog_without_active_connections():
    payload = {
        "result": [
            {"name": "sap_successfactors_workforce_overview", "title": "Workforce Overview"},
            {"name": "salesforce_sales_dashboard", "title": "Salesforce"},
        ]
    }

    scoped = _filter_apps_payload_to_scoped_connections(payload, set())

    assert scoped["apps"] == []
    assert scoped["result"] == []
    assert scoped["active_scoped_cartridges"] == []
    assert scoped["apps_scope"] == {
        "mode": "no_active_connections",
        "hidden_unconfigured_count": 2,
        "message": "No hay apps configuradas para conexiones activas del workspace.",
    }


def test_apps_gallery_is_only_a_control_room_entrypoint():
    src = (REPO / "console-next/src/components/apps/AppsGallery.tsx").read_text(encoding="utf-8")

    assert "Analitica del workspace" in src
    assert "Revisa indicadores, agentes y decisiones" in src
    assert "/control-room#apps" in src
    assert "listApps({ includeUnready: true })" not in src
    assert "Apps publicadas" not in src
    assert "Ver en Control Room" not in src
    assert "Ver dentro de Control Room" not in src
    assert "No hay aplicaciones instaladas para este workspace." not in src
    assert "No hay aplicaciones configuradas para las conexiones activas del workspace." not in src
    assert "No hay aplicaciones publicadas todavía." not in src


@pytest.mark.asyncio
async def test_apps_include_unready_can_use_installed_cartridges(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("INTERNAL_API_KEY", "x" * 64)

    from app import main as console_main

    class FakeConn:
        async def fetch(self, sql, tenant_id, workspace_id, candidates):
            assert "cartridge_installations" in sql
            assert tenant_id == "11111111-1111-1111-1111-111111111111"
            assert workspace_id == "22222222-2222-2222-2222-222222222222"
            assert set(candidates) == {"sap_successfactors", "salesforce"}
            return [{"cartridge_id": "sap_successfactors"}]

    class FakeScopedDb:
        async def __aenter__(self):
            return FakeConn(), None, None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_pool():
        return object()

    def fake_scoped_db(pool, user):
        assert user["active_tenant_id"] == "11111111-1111-1111-1111-111111111111"
        assert user["active_workspace_id"] == "22222222-2222-2222-2222-222222222222"
        return FakeScopedDb()

    user = {
        "role": "workspace_admin",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "allowed_cartridges": ["sap_successfactors"],
    }

    monkeypatch.setattr(console_main, "_get_db_pool", fake_pool)
    monkeypatch.setattr(console_main, "scoped_db_for_user", fake_scoped_db)

    installed = await console_main._installed_scoped_app_cartridges(
        user,
        {"sap_successfactors", "salesforce"},
    )

    assert installed == {"sap_successfactors"}


def test_apps_gallery_filters_to_gold_ready_datasets():
    payload = {
        "apps": [
            {"name": "pipeline_forecast_dashboard", "datasets_used": ["forecast_mensual"]},
            {"name": "replicon_margin_dashboard", "datasets_used": ["pnl_mensual"]},
            {"name": "empty_shell", "datasets_used": []},
        ]
    }

    scoped = _filter_apps_payload_to_ready_datasets(payload, {"forecast_mensual"})

    assert [app["name"] for app in scoped["apps"]] == ["pipeline_forecast_dashboard"]
    assert scoped["apps"][0]["data_status"] == "ready"
    assert scoped["apps_readiness"]["hidden_unready_count"] == 2
    assert scoped["apps_readiness"]["unavailable_datasets"] == ["pnl_mensual"]


def test_control_room_can_show_unready_apps_with_status():
    payload = {
        "apps": [
            {"name": "sap_successfactors_talent_health", "datasets_used": ["talent_health"]},
            {"name": "sap_successfactors_shell", "datasets_used": []},
        ]
    }

    scoped = _filter_apps_payload_to_ready_datasets(
        payload,
        set(),
        include_unready=True,
    )

    assert [app["name"] for app in scoped["apps"]] == [
        "sap_successfactors_talent_health",
        "sap_successfactors_shell",
    ]
    assert scoped["apps"][0]["data_status"] == "unready"
    assert scoped["apps"][0]["unavailable_datasets"] == ["talent_health"]
    assert scoped["apps"][1]["data_status"] == "dataset_metadata_missing"
    assert scoped["apps_readiness"]["hidden_unready_count"] == 0


def test_apps_gallery_marks_readiness_unchecked_without_hiding_twice():
    payload = {"apps": [{"name": "pipeline_forecast_dashboard", "datasets_used": ["forecast_mensual"]}]}

    scoped = _filter_apps_payload_to_ready_datasets(payload, None, mode="gold_unreachable")

    assert scoped["apps"] == payload["apps"]
    assert scoped["apps_readiness"]["mode"] == "gold_unreachable"
    assert scoped["apps_readiness"]["hidden_unready_count"] == 0
