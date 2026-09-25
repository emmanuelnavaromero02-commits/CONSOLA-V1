from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def manifest_module(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    _SIBLINGS = ("/cartridges/", "/console", "/refinement", "/vault", "/workspace", "/mcp-infra")
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    import importlib
    return importlib.import_module("app.services.tool_manifest")


def test_classify_tool_read(manifest_module):
    res = manifest_module.classify_tool("airflow_list_dags")
    assert res["risk_level"] == "read"
    assert res["requires_approval"] is False
    assert res["freshness_minutes"] == 60


def test_classify_tool_destructive(manifest_module):
    res = manifest_module.classify_tool("airflow_delete_dag")
    assert res["risk_level"] == "destructive"
    assert res["requires_approval"] is True


def test_classify_tool_default_write(manifest_module):
    res = manifest_module.classify_tool("some_unknown_tool")
    assert res["risk_level"] == "write"
    assert res["requires_approval"] is True
    assert res["freshness_minutes"] is None


def test_requires_approval_conservative_defaults(manifest_module):
    assert manifest_module.requires_approval("airflow_list_dags") is False
    assert manifest_module.requires_approval("airflow_delete_dag") is True
    assert manifest_module.requires_approval("some_unknown_tool") is True


def test_list_cartridges_is_read_only(manifest_module):
    res = manifest_module.classify_tool("list_cartridges")
    assert res["risk_level"] == "read"
    assert res["requires_approval"] is False
    assert manifest_module.requires_approval("list_cartridges") is False


def test_market_context_read_is_read_only(manifest_module):
    res = manifest_module.classify_tool("market_context_read")
    assert res["risk_level"] == "read"
    assert res["requires_approval"] is False
    assert manifest_module.requires_approval("market_context_read") is False


def test_classify_postgres_execute_is_destructive(manifest_module):
    res = manifest_module.classify_tool("postgres_execute_query")
    assert res["risk_level"] == "destructive"


def test_airflow_mutating_tools_are_destructive(manifest_module):
    for name in (
        "airflow_create_dag",
        "airflow_delete_dag",
        "airflow_set_variable",
        "postgres_execute_query",
        "postgres_execute_ddl",
    ):
        res = manifest_module.classify_tool(name)
        assert res["risk_level"] == "destructive", name
        assert res["requires_approval"] is True, name


def test_studio_goal_run_mutating_tools_are_not_read_only(manifest_module):
    for name in ("create_goal_run", "plan_goal_run", "execute_goal_run"):
        res = manifest_module.classify_tool(name)
        assert res["risk_level"] in {"write", "destructive"}, name
        assert res["requires_approval"] is True, name

    status = manifest_module.classify_tool("get_goal_run_status")
    assert status["risk_level"] == "read"
    assert status["requires_approval"] is False


def test_build_manifest_aggregates_servers(manifest_module, monkeypatch):
    import asyncio

    fake_servers = [
        {"id": "infra", "name": "MCP Infra"},
        {"id": "replicon", "name": "Replicon"},
    ]
    tools_by_server = {
        "infra": [{"name": "airflow_list_dags", "description": "d", "input_schema": {}}],
        "replicon": [{"name": "query_kb", "description": "d2", "input_schema": {}}],
    }

    async def fake_list_servers():
        return fake_servers

    async def fake_list_tools(server_id):
        return tools_by_server.get(server_id, [])

    monkeypatch.setattr(manifest_module.mcp_registry, "list_servers", fake_list_servers)
    monkeypatch.setattr(manifest_module.mcp_registry, "list_tools", fake_list_tools)

    result = asyncio.new_event_loop().run_until_complete(manifest_module.build_manifest())
    assert result["version"] == "1.0"
    assert set(result["servers"].keys()) == {"infra", "replicon"}
    assert result["tool_count_total"] == 2
    infra_tool = result["servers"]["infra"][0]
    assert infra_tool["risk_level"] == "read"
    assert infra_tool["requires_approval"] is False
    replicon_tool = result["servers"]["replicon"][0]
    assert replicon_tool["risk_level"] == "write"
    assert replicon_tool["requires_approval"] is True


def test_sql_backed_kb_tools_are_not_read_only(manifest_module):
    for name in ("query_kb", "cartridge_query_kb"):
        res = manifest_module.classify_tool(name)
        assert res["risk_level"] == "write"
        assert res["requires_approval"] is True


def test_dag_get_source_is_not_read_only_because_it_caches(manifest_module):
    res = manifest_module.classify_tool("dag_get_source")
    assert res["risk_level"] == "write"
    assert res["requires_approval"] is True


def test_build_manifest_tolerates_list_tools_failure(manifest_module, monkeypatch):
    import asyncio

    async def fake_list_servers():
        return [{"id": "infra"}, {"id": "broken"}]

    async def fake_list_tools(server_id):
        if server_id == "broken":
            raise RuntimeError("unreachable")
        return [{"name": "list_entities"}]

    monkeypatch.setattr(manifest_module.mcp_registry, "list_servers", fake_list_servers)
    monkeypatch.setattr(manifest_module.mcp_registry, "list_tools", fake_list_tools)

    result = asyncio.new_event_loop().run_until_complete(manifest_module.build_manifest())
    assert result["servers"]["broken"] == []
    assert result["servers"]["infra"][0]["risk_level"] == "read"
