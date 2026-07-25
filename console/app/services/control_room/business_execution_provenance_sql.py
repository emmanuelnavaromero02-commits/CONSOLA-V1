from __future__ import annotations

from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
)


def _executed_metadata_sql(base_expression: str) -> str:
    provenance_path = f"'{{{DECISION_PROVENANCE_KEY}}}'"
    return f"""
jsonb_set(
    ({base_expression}),
    {provenance_path},
    COALESCE(
        ({base_expression}) #> {provenance_path},
        '{{}}'::jsonb
    ) || '{{"stage":"{WorkflowStage.EXECUTED.value}",'
         '"reason":"explicit_execution"}}'::jsonb,
    true
)
""".strip()


EXECUTED_METADATA_ARG3_SQL = _executed_metadata_sql(
    "COALESCE(metadata, '{}'::jsonb) || $3::jsonb"
)
EXECUTED_METADATA_ARG4_SQL = _executed_metadata_sql(
    "COALESCE(metadata, '{}'::jsonb) || $4::jsonb"
)
EXECUTED_METADATA_STATUS_SQL = _executed_metadata_sql(
    "COALESCE(metadata, '{}'::jsonb) " '|| \'{"execution_status":"executed"}\'::jsonb'
)


__all__ = (
    "EXECUTED_METADATA_ARG3_SQL",
    "EXECUTED_METADATA_ARG4_SQL",
    "EXECUTED_METADATA_STATUS_SQL",
    "WorkflowStage",
)
