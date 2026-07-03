from app.domains.apps.payloads import (
    app_cartridge_id,
    app_datasets_from_payload,
    app_declared_datasets,
    app_payload_cartridge_candidates,
    apps_from_payload,
    filter_apps_payload_to_ready_datasets,
    filter_apps_payload_to_scoped_connections,
    user_with_apps_scope,
)
from app.domains.apps.embed import (
    app_embed_csp,
    app_embed_wrapper_html,
    datasets_from_app_html,
)


def test_apps_payload_helpers_normalize_and_scope_apps():
    payload = {
        "result": [
            {"name": "sap_successfactors_workforce_overview"},
            {"name": "replicon_margin_dashboard"},
            {"name": "unknown"},
            "ignored",
        ]
    }

    assert [app.get("name") for app in apps_from_payload(payload)] == [
        "sap_successfactors_workforce_overview",
        "replicon_margin_dashboard",
        "unknown",
    ]
    assert app_payload_cartridge_candidates(payload) == {
        "replicon",
        "sap_successfactors",
    }
    scoped = filter_apps_payload_to_scoped_connections(payload, {"sap_successfactors"})
    assert [app.get("name") for app in scoped["apps"]] == [
        "sap_successfactors_workforce_overview"
    ]
    assert scoped["apps_scope"]["hidden_unconfigured_count"] == 3


def test_apps_payload_helpers_resolve_cartridge_scope_and_datasets():
    assert app_cartridge_id({"connector_id": "hubspot"}) == "hubspot"
    assert app_cartridge_id({"name": "sap_hcm_headcount"}) == "sap_hcm"
    assert app_declared_datasets(
        {"datasets_used": ["valid_name", "bad-name"], "dataset": "also_valid"}
    ) == {"also_valid", "valid_name"}
    assert app_datasets_from_payload(
        {"apps": [{"datasets": ["one"]}, {"dataset": "two"}]}
    ) == {"one", "two"}
    scoped_user = user_with_apps_scope(
        {"id": "u1"},
        "tenant-a",
        "workspace-a",
    )
    assert scoped_user["active_tenant_id"] == "tenant-a"
    assert scoped_user["active_workspace_id"] == "workspace-a"


def test_apps_payload_helpers_filter_by_ready_datasets():
    payload = {
        "apps": [
            {"name": "forecast", "datasets_used": ["forecast_mensual"]},
            {"name": "margin", "datasets_used": ["pnl_mensual"]},
            {"name": "shell"},
        ]
    }

    ready = filter_apps_payload_to_ready_datasets(payload, {"forecast_mensual"})
    assert [app["name"] for app in ready["apps"]] == ["forecast"]
    assert ready["apps"][0]["data_status"] == "ready"
    assert ready["apps_readiness"]["unavailable_datasets"] == ["pnl_mensual"]

    unready = filter_apps_payload_to_ready_datasets(
        payload,
        {"forecast_mensual"},
        include_unready=True,
    )
    assert [app["data_status"] for app in unready["apps"]] == [
        "ready",
        "unready",
        "dataset_metadata_missing",
    ]

    unchecked = filter_apps_payload_to_ready_datasets(
        payload,
        None,
        mode="gold_unreachable",
    )
    assert unchecked["apps"] == payload["apps"]
    assert unchecked["apps_readiness"]["mode"] == "gold_unreachable"


def test_app_embed_helpers_extract_and_guard_declared_datasets():
    assert datasets_from_app_html(
        """
        fetch('/api/data/valid_one')
        fetch('/api/data/also_valid?limit=10')
        fetch('/api/data/bad-name')
        """
    ) == ["also_valid", "valid_one"]

    html = app_embed_wrapper_html(
        "risk_board",
        ["valid_one", "bad-name", "also_valid"],
        "nonce-123",
    )
    assert 'sandbox="allow-scripts"' in html
    assert "omega-app-fetch" in html
    assert "omega-app-fetch-result" in html
    assert '"/api/data/"' in html
    assert '"also_valid", "valid_one"' in html
    assert "bad-name" not in html
    assert "dataset ${dataset || \"(empty)\"} not declared by app" in html
    assert "script-src 'self' 'nonce-nonce-123'" in app_embed_csp("nonce-123")
