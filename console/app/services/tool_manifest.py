from __future__ import annotations

from typing import Any

from app.services import mcp_registry


READ_ONLY_TOOLS = {
    "airflow_list_dags", "airflow_get_run_status", "airflow_get_task_logs",
    "airflow_list_task_instances", "airflow_list_dag_runs",

    "view_job", "view_jobs", "view_schema", "view_dataset", "view_datasets",
    "view_semantic", "view_pipeline",

    "postgres_list_schemas", "postgres_list_tables",
    "postgres_get_table_schema", "postgres_get_sample",
    "postgres_describe_table", "postgres_select",

    "minio_list_objects", "minio_get_parquet_schema", "minio_get_sample_rows",
    "minio_list_cartridge_specs", "minio_read_spec",

    "search_rag", "list_rag_sources",

    "list_apps", "get_app_details", "get_app_html",
    "get_data_catalog", "describe_source", "describe_silver",
    "list_sources", "preview_source", "get_source_partitions",
    "generate_transform", "preview_transform", "list_datasets",
    "list_datasets_with_schemas", "query_dataset", "get_lineage",
    "superset_list_databases", "superset_list_datasets",

    "agent_list", "agent_get",

    "calibration__bayesian_state",
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
    "control_room__finance_kpis_read",
    "control_room__operations_kpis_read",
    "control_room__risk_kpis_read",
    "control_room__agent_memory_read",
    "market_context_read",

    "watermark_get",

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

    "list_cartridges",

    "vault_list_connections", "vault_get_connection", "vault_list_secrets",
}

DESTRUCTIVE_TOOLS = {
    "airflow_delete_dag", "airflow_create_dag", "airflow_set_variable",
    "postgres_execute_query",
    "postgres_execute_ddl",
    "agent_delete",
    "delete_app", "delete_dataset", "delete_entity", "vault_delete_connection",
}

ADVISORY_WRITE_TOOLS = {
    "control_room__raise_alert",
    "control_room__raise_analysis_alert",
    "simulation__monte_carlo_run",
    "decision__orchestrate",
    "wisdom_bits__run",
    "control_room__agent_memory_write",
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
    if tool_name in ADVISORY_WRITE_TOOLS:
        return {
            "risk_level": "write",
            "requires_approval": False,
            "freshness_minutes": None,
        }
    return {
        "risk_level": "write",
        "requires_approval": True,
        "freshness_minutes": None,
    }


def requires_approval(tool_name: str) -> bool:
    if tool_name in READ_ONLY_TOOLS:
        return False
    if tool_name in ADVISORY_WRITE_TOOLS:
        return False
    if tool_name in DESTRUCTIVE_TOOLS:
        return True
    return True


async def build_manifest() -> dict[str, Any]:
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
