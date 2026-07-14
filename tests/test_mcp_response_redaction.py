from __future__ import annotations

import importlib
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _load_mcp_main(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    monkeypatch.setenv("PG_PASSWORD", "test-password")
    monkeypatch.setenv("INTERNAL_API_KEY", "x" * 32)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "y" * 32)
    monkeypatch.setenv("AIRFLOW_USER", "admin")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow-pass")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "superset-pass")
    monkeypatch.syspath_prepend(str(REPO / "mcp-infra"))
    return importlib.import_module("app.main")


def test_mcp_tool_result_redacts_cartridge_error_and_run_logs(monkeypatch):
    mcp_main = _load_mcp_main(monkeypatch)
    payload = {
        "error": (
            "HubSpot upstream failed Authorization: Bearer hubspot-live-token "
            "sap_password=sap-secret salesforce_client_secret=sf-secret"
        ),
        "run_logs": [
            {
                "step": "extract",
                "error_message": "refresh_token=refresh-secret vault_value=vault-secret",
            }
        ],
        "nested": {"INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA": "pair-key"},
    }

    redacted = mcp_main._redact_tool_result(payload)
    rendered = repr(redacted)

    for secret in (
        "hubspot-live-token",
        "sap-secret",
        "sf-secret",
        "refresh-secret",
        "vault-secret",
        "pair-key",
    ):
        assert secret not in rendered
    assert "Bearer ***REDACTED***" in rendered
    assert "***REDACTED***" in rendered


def test_mcp_tool_result_preserves_public_ids_and_hashes(monkeypatch):
    mcp_main = _load_mcp_main(monkeypatch)
    simulation_id = "mc-" + "a" * 32
    orchestration_id = "orch-" + "b" * 64
    payload_hash = "c" * 64
    raw_hex = "d" * 40

    redacted = mcp_main._redact_tool_result(
        {
            "simulation_id": simulation_id,
            "source_id": simulation_id,
            "orchestration_id": orchestration_id,
            "payload_hash": payload_hash,
            "message": f"opaque value {raw_hex}",
            "token": simulation_id,
        }
    )

    assert redacted["simulation_id"] == simulation_id
    assert redacted["source_id"] == simulation_id
    assert redacted["orchestration_id"] == orchestration_id
    assert redacted["payload_hash"] == payload_hash
    assert raw_hex not in redacted["message"]
    assert redacted["token"] == "***REDACTED***"
