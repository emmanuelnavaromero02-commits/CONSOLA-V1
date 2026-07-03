from __future__ import annotations

from app.services.mcp_payloads import mcp_payload


def test_mcp_payload_includes_security_context_when_user_is_present():
    payload = mcp_payload(
        "list_datasets",
        {"layer": "gold"},
        {
            "role": "tenant_admin",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "allowed_cartridges": ["sap_successfactors"],
        },
    )

    assert payload["tool"] == "list_datasets"
    assert payload["args"] == {"layer": "gold"}
    assert payload["security_context"]["tenant_id"] == "tenant-1"
    assert payload["security_context"]["workspace_id"] == "workspace-1"
    assert payload["security_context"]["allowed_cartridges"] == ["sap_successfactors"]


def test_mcp_payload_omits_security_context_without_user():
    assert mcp_payload("health", {}) == {"tool": "health", "args": {}}
