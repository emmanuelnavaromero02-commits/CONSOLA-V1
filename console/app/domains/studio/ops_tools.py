from __future__ import annotations

from typing import Any


def build_studio_ops_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "rename_entity",
            "description": (
                "Rename an entity within a cartridge. "
                "Updates entity_config, entity_watermarks, pipeline_runs and silver_lineage atomically. "
                "Bronze files in MinIO keep their original path (historical data). "
                "Use this when the user asks to rename an entity."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {
                        "type": "string",
                        "description": "Cartridge ID, e.g. 'replicon'",
                    },
                    "old_name": {
                        "type": "string",
                        "description": "Current entity name",
                    },
                    "new_name": {"type": "string", "description": "New entity name"},
                },
                "required": ["cartridge_id", "old_name", "new_name"],
            },
        },
        {
            "name": "list_entities",
            "description": (
                "List all entities registered in a cartridge, including their mode, dag_id, "
                "trigger_type, and last pipeline run status."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                },
                "required": ["cartridge_id"],
            },
        },
        {
            "name": "get_entity_logs",
            "description": (
                "Fetch the Airflow task logs for the most recent run of a specific entity. "
                "Use this when a DAG run failed and the user wants to diagnose the error. "
                "Returns the error message from pipeline_runs plus the full Airflow task log."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                    "entity": {"type": "string"},
                },
                "required": ["cartridge_id", "entity"],
            },
        },
        {
            "name": "delete_entity",
            "description": (
                "Delete an entity from a cartridge. "
                "Removes it from entity_config and clears its watermarks. "
                "Pipeline run history is preserved for auditing. "
                "Use this when the user explicitly asks to delete or remove an entity."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {
                        "type": "string",
                        "description": "Cartridge ID, e.g. 'replicon'",
                    },
                    "entity": {
                        "type": "string",
                        "description": "Entity name to delete",
                    },
                },
                "required": ["cartridge_id", "entity"],
            },
        },
        {
            "name": "update_entity",
            "description": (
                "Update one or more fields of an entity: display_name, mode (full|incremental), "
                "dag_id, trigger_type (manual|scheduled), cron_expression, description, enabled."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "cartridge_id": {"type": "string"},
                    "entity": {"type": "string"},
                    "display_name": {"type": "string"},
                    "mode": {"type": "string", "enum": ["full", "incremental"]},
                    "dag_id": {"type": "string"},
                    "connection_id": {"type": "string"},
                    "trigger_type": {"type": "string", "enum": ["manual", "scheduled"]},
                    "cron_expression": {"type": "string"},
                    "description": {"type": "string"},
                    "enabled": {"type": "boolean"},
                },
                "required": ["cartridge_id", "entity"],
            },
        },
    ]
