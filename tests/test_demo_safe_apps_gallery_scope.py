import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)
sys.path.insert(0, str(REPO / "console"))

from app.main import _app_payload_cartridge_candidates, _filter_apps_payload_to_scoped_connections  # noqa: E402



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


def test_apps_gallery_empty_state_mentions_configured_connections():
    src = (REPO / "console-next/src/components/apps/AppsGallery.tsx").read_text(encoding="utf-8")

    assert "No hay aplicaciones configuradas para las conexiones activas del workspace." in src
    assert "No hay aplicaciones publicadas todavía." not in src
