"""
Sprint v1.41.0 — Unified tool manifest service.

Consolidates tools from all registered MCP servers (mcp-infra,
sap_hcm, sap_s4hana, sap_successfactors, replicon) into a single
catalog with extended metadata for the copilot router (v1.42+):
  - risk_level: read | write | destructive
  - requires_approval: bool
  - freshness_minutes: how stale data can be before warning user
"""
from __future__ import annotations

from typing import Any

from app.services import mcp_registry


# Static classification overrides per tool name.
# Default: every tool not listed is treated as 'write' + requires_approval=True
# for safety. Explicit READ tools listed here unlock auto-execution in the
# future copilot.
READ_ONLY_TOOLS = {
    # mcp-infra airflow read-only
    "airflow_list_dags", "airflow_get_run_status", "airflow_get_task_logs",
    "airflow_list_task_instances", "airflow_list_dag_runs",
    # postgres read-only
    "postgres_list_tables", "postgres_describe_table", "postgres_select",
    # cartridge read-only (replicon + SAP same pattern)
    "list_entities", "get_schema", "preview", "get_run_status",
    "list_kbs", "get_watermarks",
}

DESTRUCTIVE_TOOLS = {
    "airflow_delete_dag", "airflow_create_dag", "airflow_set_variable",
    "postgres_execute_query",  # arbitrary write
}

DEFAULT_FRESHNESS_MINUTES = 60


def classify_tool(tool_name: str) -> dict[str, Any]:
    if tool_name in READ_ONLY_TOOLS:
        return {
            "risk_level": "read",
            "requires_approval": False,
            "freshness_minutes": DEFAULT_FRESHNESS_MINUTES,
        }
    if tool_name in DESTRUCTIVE_TOOLS:
        return {
            "risk_level": "destructive",
            "requires_approval": True,
            "freshness_minutes": None,
        }
    return {
        "risk_level": "write",
        "requires_approval": True,
        "freshness_minutes": None,
    }


def requires_approval(tool_name: str) -> bool:
    """Return whether a tool must be gated before execution.

    The default is intentionally conservative: any unclassified write-style
    tool requires explicit user approval until the manifest marks it read-only.
    """
    if tool_name in READ_ONLY_TOOLS:
        return False
    if tool_name in DESTRUCTIVE_TOOLS:
        return True
    return True


async def build_manifest() -> dict[str, Any]:
    """Aggregate tools from all registered MCP servers + classify them."""
    servers = await mcp_registry.list_servers()
    tools_by_server: dict[str, list[dict[str, Any]]] = {}
    for srv in servers:
        srv_id = srv.get("id") or srv.get("server_id") or srv.get("name")
        if not srv_id:
            continue
        try:
            tools = await mcp_registry.list_tools(srv_id)
        except Exception:
            tools = []
        classified = []
        for t in tools:
            name = t.get("name", "")
            meta = classify_tool(name)
            classified.append({
                "name": name,
                "description": t.get("description", ""),
                "input_schema": t.get("input_schema") or t.get("inputSchema", {}),
                "server": srv_id,
                **meta,
            })
        tools_by_server[srv_id] = classified
    return {
        "version": "1.0",
        "servers": tools_by_server,
        "tool_count_total": sum(len(v) for v in tools_by_server.values()),
    }
