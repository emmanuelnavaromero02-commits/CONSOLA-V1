from __future__ import annotations

import pytest

from app.services import assistant_tool_gate


USER = {
    "id": "u1",
    "role": "viewer",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


@pytest.mark.asyncio
async def test_assistant_gate_denies_destructive_unknown_and_permissionless(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    async def invoke(server: str, tool: str, args: dict, *, user=None):
        calls.append((server, tool, args))
        return {"ok": True}

    monkeypatch.setattr(assistant_tool_gate.mcp_registry, "invoke", invoke)
    catalog = {
        "mcp-infra__airflow_list_dags": {
            "server_id": "mcp-infra",
            "tool": "airflow_list_dags",
            "input_schema": {"type": "object", "properties": {}},
            "risk_level": "read",
            "requires_approval": False,
        },
        "mcp-infra__postgres_execute_query": {
            "server_id": "mcp-infra",
            "tool": "postgres_execute_query",
            "input_schema": {"type": "object", "properties": {}},
            "risk_level": "destructive",
            "requires_approval": True,
        },
        "mcp-infra__brand_new_tool": {
            "server_id": "mcp-infra",
            "tool": "brand_new_tool",
            "input_schema": {"type": "object", "properties": {}},
            "risk_level": "write",
            "requires_approval": True,
        },
    }

    destructive = await assistant_tool_gate.invoke(
        "mcp-infra", "postgres_execute_query", {"sql": "DROP TABLE x"}, USER, catalog
    )
    unknown = await assistant_tool_gate.invoke("mcp-infra", "brand_new_tool", {}, USER, catalog)
    permissionless = await assistant_tool_gate.invoke(
        "mcp-infra", "airflow_list_dags", {}, {"role": "workspace_user"}, catalog
    )

    assert destructive["error"] == assistant_tool_gate.DENIED_ERROR
    assert unknown["error"] == assistant_tool_gate.DENIED_ERROR
    assert permissionless["error"] == assistant_tool_gate.DENIED_ERROR
    assert calls == []

    safe = await assistant_tool_gate.invoke("mcp-infra", "airflow_list_dags", {}, USER, catalog)
    assert safe == {"ok": True}
    assert calls == [("mcp-infra", "airflow_list_dags", {})]


@pytest.mark.asyncio
async def test_assistant_build_tools_exposes_only_allowlisted_read_tools(monkeypatch):
    async def list_servers():
        return [
            {
                "id": "mcp-infra",
                "name": "MCP Infra",
                "healthy": True,
                "tools": [
                    {"name": "airflow_list_dags", "description": "read", "input_schema": {}},
                    {"name": "airflow_delete_dag", "description": "delete", "input_schema": {}},
                    {"name": "unknown_tool", "description": "unknown", "input_schema": {}},
                ],
            }
        ]

    monkeypatch.setattr(assistant_tool_gate.mcp_registry, "list_servers", list_servers)

    tools, server_map, catalog = await assistant_tool_gate.build_tools(USER)

    assert [tool["name"] for tool in tools] == ["mcp-infra__airflow_list_dags"]
    assert server_map == {"mcp-infra__airflow_list_dags": "mcp-infra"}
    assert set(catalog) == {"mcp-infra__airflow_list_dags"}
