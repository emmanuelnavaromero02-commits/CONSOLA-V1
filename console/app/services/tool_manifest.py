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
# for safety. Explicit READ tools listed here unlock auto-execution for pure
# inspection/query actions so the copilot can actually answer platform
# questions without asking for approval to "look".
READ_ONLY_TOOLS = {
    # mcp-infra airflow read-only
    "airflow_list_dags", "airflow_get_run_status", "airflow_get_task_logs",
    "airflow_list_task_instances", "airflow_list_dag_runs",

    # monitoring deeplinks/read-only console viewers
    "view_job", "view_jobs", "view_schema", "view_dataset", "view_datasets",
    "view_semantic", "view_pipeline",

    # postgres read-only
    "postgres_list_schemas", "postgres_list_tables",
    "postgres_get_table_schema", "postgres_get_sample",
    "postgres_describe_table", "postgres_select",

    # MinIO/lakehouse read-only browsing and samples
    "minio_list_objects", "minio_get_parquet_schema", "minio_get_sample_rows",
    "minio_list_cartridge_specs", "minio_read_spec",

    # RAG read-only
    "search_rag", "list_rag_sources",

    # Studio app/catalog read-only
    "list_apps", "get_app_details", "get_app_html",
    "get_data_catalog", "describe_source", "describe_silver",
    "list_sources", "preview_source", "get_source_partitions",
    "generate_transform", "preview_transform", "list_datasets",
    "list_datasets_with_schemas", "query_dataset", "get_lineage",
    "superset_list_databases", "superset_list_datasets",

    # agent catalog read-only
    "agent_list", "agent_get",

    # pipeline metadata read-only
    "watermark_get",

    # cartridge read-only (replicon + SAP same pattern)
    "list_entities", "get_entity_logs", "get_schema", "preview",
    "get_run_status", "list_kbs", "get_watermarks",
    "cartridge_get_semantic", "cartridge_search_term",
    "cartridge_get_manifest", "cartridge_list_entities",
    "cartridge_get_schema", "cartridge_preview",
    "cartridge_get_run_logs", "cartridge_get_job_status",
    "cartridge_list_jobs", "cartridge_list_kbs",
    "generate_dag_code",
    "autopilot_build_cartridge",
    "cartridge_self_check",
    "get_goal_run_status",
    "introspect_source",
    "validate_dag_code",

    # infra catalog read-only
    "list_cartridges",

    # Vault returns masked values for get/list tools.
    "vault_list_connections", "vault_get_connection", "vault_list_secrets",
}

DESTRUCTIVE_TOOLS = {
    "airflow_delete_dag", "airflow_create_dag", "airflow_set_variable",
    "postgres_execute_query",  # arbitrary write
    "postgres_execute_ddl",
    "agent_delete",
    "delete_app", "delete_dataset", "delete_entity", "vault_delete_connection",
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
