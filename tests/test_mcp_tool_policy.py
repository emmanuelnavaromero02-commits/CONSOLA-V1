from __future__ import annotations

import importlib
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _load_tool_manifest():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path.insert(0, str(REPO / "console"))
    try:
        return importlib.import_module("app.services.tool_manifest")
    finally:
        try:
            sys.path.remove(str(REPO / "console"))
        except ValueError:
            pass


def test_mcp_mutating_tools_require_approval():
    manifest = _load_tool_manifest()
    for name in (
        "airflow_create_dag",
        "airflow_delete_dag",
        "airflow_set_variable",
        "postgres_execute_ddl",
        "vault_delete_connection",
        "agent_delete",
        "create_goal_run",
        "plan_goal_run",
        "execute_goal_run",
    ):
        meta = manifest.classify_tool(name)
        assert meta["risk_level"] in {"write", "destructive"}, name
        assert meta["requires_approval"] is True, name


def test_airflow_rce_like_tools_have_second_gate():
    source = (REPO / "mcp-infra/app/tools/airflow.py").read_text(encoding="utf-8")
    for tool in ("airflow_create_dag", "airflow_delete_dag", "airflow_set_variable"):
        body = source.split(f"async def {tool}", 1)[1].split("@tool(", 1)[0]
        assert "_is_development()" in body, tool
        assert "_rce_tools_explicitly_enabled()" in body, tool
        assert "ALLOW_RCE_TOOLS" in body, tool


def test_scheduled_agents_do_not_get_destructive_manifest_bypass():
    source = (REPO / "console/app/services/tool_manifest.py").read_text(encoding="utf-8")
    read_only_block = source.split("READ_ONLY_TOOLS = {", 1)[1].split("}", 1)[0]
    for name in ("create_goal_run", "plan_goal_run", "execute_goal_run", "agent_delete"):
        assert f'"{name}"' not in read_only_block


def test_legacy_control_room_alert_tools_are_not_approval_free():
    manifest = _load_tool_manifest()
    for name in (
        "control_room__raise_alert",
        "control_room__raise_analysis_alert",
    ):
        meta = manifest.classify_tool(name)
        assert meta["risk_level"] == "write"
        assert meta["requires_approval"] is True

    tool_source = (REPO / "mcp-infra/app/tools/control_room.py").read_text(
        encoding="utf-8"
    )
    main_source = (REPO / "mcp-infra/app/main.py").read_text(encoding="utf-8")
    for name in (
        "control_room__raise_alert",
        "control_room__raise_analysis_alert",
    ):
        assert f'name="{name}"' not in tool_source
        assert f'"{name}"' not in main_source


def test_control_room_read_tools_are_readonly_without_approval():
    manifest = _load_tool_manifest()
    for name in (
        "control_room__summary_read",
        "control_room__dashboard_read",
        "control_room__ops_summary_read",
        "control_room__alerts_read",
        "control_room__agents_ops_read",
        "control_room__sap_successfactors_gold_kpis_read",
        "control_room__talent_kpis_read",
        "control_room__talent_overview_read",
        "control_room__talent_9box_read",
        "control_room__talent_metadata_readiness_read",
        "control_room__decision_intelligence_runs_read",
    ):
        meta = manifest.classify_tool(name)
        assert meta["risk_level"] == "read", name
        assert meta["requires_approval"] is False, name


def test_market_context_read_is_readonly_without_approval():
    manifest = _load_tool_manifest()
    meta = manifest.classify_tool("market_context_read")

    assert meta["risk_level"] == "read"
    assert meta["requires_approval"] is False
