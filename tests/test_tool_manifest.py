"""Sprint v1.41.0 — tool manifest classification + endpoint auth."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def manifest_module(monkeypatch):
    """Load console's app.services.tool_manifest with a clean sys.path."""
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


def test_classify_postgres_execute_is_destructive(manifest_module):
    """postgres_execute_query lets the caller run arbitrary SQL writes —
    must NOT be auto-executable by the future copilot router."""
    res = manifest_module.classify_tool("postgres_execute_query")
    assert res["risk_level"] == "destructive"


def test_build_manifest_aggregates_servers(manifest_module, monkeypatch):
    """build_manifest should iterate every registered server and classify
    each tool returned by list_tools(server_id)."""
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
    # airflow_list_dags is in READ_ONLY_TOOLS
    infra_tool = result["servers"]["infra"][0]
    assert infra_tool["risk_level"] == "read"
    assert infra_tool["requires_approval"] is False
    # query_kb is not classified → default write
    replicon_tool = result["servers"]["replicon"][0]
    assert replicon_tool["risk_level"] == "write"


def test_build_manifest_tolerates_list_tools_failure(manifest_module, monkeypatch):
    """If one server's list_tools raises, the manifest should still surface
    the server (with an empty tools list) instead of failing entirely."""
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
