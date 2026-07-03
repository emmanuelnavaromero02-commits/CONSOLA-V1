from app.domains.apps.payloads import (
    app_cartridge_id,
    app_datasets_from_payload,
    app_declared_datasets,
    app_payload_cartridge_candidates,
    apps_from_payload,
    filter_apps_payload_to_scoped_connections,
    user_with_apps_scope,
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
