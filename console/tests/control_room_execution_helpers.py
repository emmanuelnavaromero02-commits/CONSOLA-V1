from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

from app.services import control_room_service
from app.services.control_room.business_policy_metadata import business_policy_metadata
from app.services.control_room.business_explicit_action_binding import (
    attach_explicit_action_binding,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    business_observation_fingerprint,
    persistence_metadata,
    workflow_eligibility_provenance,
)


USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_workspace_id": "workspace-A",
    "tenant_id": "tenant-A",
    "role": "admin",
    "allowed_cartridges": ["sap_hcm", "sap_s4hana", "sap_successfactors", "replicon"],
    "_effective_permissions": [
        "control_room.read",
        "control_room.write",
        "control_room.execute",
    ],
}


def explicit_action(item: dict, *, template_id: str) -> tuple[dict, str]:
    bound = attach_explicit_action_binding(item, template_id=template_id)
    return bound, explicit_binding_id(bound, template_id=template_id)


def explicit_binding_id(item: dict, *, template_id: str) -> str:
    values = item["metadata"]["explicit_action_bindings"]
    binding = next(value for value in values if value["template_id"] == template_id)
    return str(binding["binding_id"])


def runtime_evidenced_item(item: dict) -> dict:
    return {
        **item,
        **runtime_row_evidence_fields(
            source_dataset=str(item["source_dataset"]),
            source_system=str(item["source_system"]),
            cartridge=str(item["cartridge"]),
            tenant_id=str(item["tenant_id"]),
            workspace_id=str(item["workspace_id"]),
            source_row={"item_id": item["id"], "value": item["observed_value"]},
            locator_field="item_id",
            observed_at=str(item["observation_date"]),
            business_observation=item,
        ),
    }


def successful_command_tag(query: object, *_args: object) -> str:
    sql = " ".join(str(query).split()).upper()
    for command in ("INSERT", "UPDATE", "DELETE"):
        if sql.startswith(command) or f" {command} " in f" {sql} ":
            return f"{command} 0 1" if command == "INSERT" else f"{command} 1"
    return "SELECT 1"


def enable_successful_writes(mock_pool: AsyncMock) -> None:
    mock_pool.execute = AsyncMock(side_effect=successful_command_tag)


def observed_anomaly_fields(item_id: str) -> dict:
    return {
        "data_status": "ready",
        "metric_type": "scalar",
        "observed_value": 1,
        "detected_at": "2026-06-07T00:00:00Z",
        "evidence_refs": [f"gold_business_observations:{item_id}"],
        "source_dataset": "gold_business_observations",
        "entity_id": item_id,
    }


def authoritative_item_row(
    item: dict,
    *,
    decision_id: int | None = None,
    stage: WorkflowStage | None = None,
    status: str | None = None,
    selected_option_id: str | None = None,
    execution_status: str | None = None,
) -> dict:
    resolved_decision = (
        decision_id if decision_id is not None else item.get("decision_id")
    )
    policy_item = {
        **item,
        "metadata": business_policy_metadata(item.get("metadata"), item),
    }
    resolved_status = status or item.get("status") or "open"
    metadata = persistence_metadata(policy_item)
    if resolved_decision is not None:
        metadata[DECISION_PROVENANCE_KEY] = workflow_eligibility_provenance(
            item,
            stage=stage
            or (
                WorkflowStage.APPROVED
                if resolved_status == "approved"
                else WorkflowStage.DECISION_CREATED
            ),
            workspace_id="workspace-A",
            decision_id=int(resolved_decision),
            option_id=selected_option_id or item.get("selected_option_id"),
        )
    return {
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "owner_user_id": 7,
        "item_id": item["id"],
        "cartridge_id": item.get("cartridge"),
        "domain": item.get("domain"),
        "source_dataset": item.get("source_dataset"),
        "item_kind": item.get("kind") or item.get("item_kind"),
        "title": item.get("title"),
        "severity": item.get("severity"),
        "status": resolved_status,
        "decision_id": resolved_decision,
        "entity_kind": item.get("entity_kind"),
        "entity_id": item.get("entity_id"),
        "entity_label": item.get("entity_label"),
        "anomaly_type": item.get("anomaly_type"),
        "metadata": metadata,
        "impact_estimate": item.get("impact_estimate"),
        "impact_currency": item.get("impact_currency"),
        "confidence": item.get("confidence"),
        "priority_score": item.get("priority_score"),
        "selected_option_id": (
            selected_option_id
            if selected_option_id is not None
            else item.get("selected_option_id")
        ),
        "execution_status": execution_status
        or item.get("execution_status")
        or "not_started",
        "first_seen_at": datetime(2026, 5, 20, 9, 0, 0),
        "last_seen_at": datetime(2026, 5, 20, 10, 0, 0),
        "resolved_at": None,
        "dismissed_at": None,
    }


def executed_item(item: dict, *, status: str = "approved") -> dict:
    return control_room_service._with_omega(  # noqa: SLF001
        {
            **item,
            "decision_id": 42,
            "status": status,
            "execution_status": "dry_run_validated",
        }
    )


def dry_run_action_run_row(
    item: dict, *, template_id: str = "create_followup_task"
) -> dict:
    adapter_name = (
        "internal_followup_task"
        if template_id == "create_followup_task"
        else template_id
    )
    return {
        "id": 44,
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "item_id": item["id"],
        "decision_id": item.get("decision_id") or 42,
        "legacy_execution_id": 22,
        "action_type": template_id,
        "adapter_name": adapter_name,
        "mode": "dry_run",
        "status": "dry_run_completed",
        "risk_level": "low",
        "requires_approval": True,
        "approval_status": "implicit_internal_beta",
        "idempotency_key": f"dry_run:{item['id']}:{template_id}:42",
        "actor_id": 7,
        "actor_email": "ops@example.com",
        "input": {},
        "dry_run_result": {"ok": True, "validated": True},
        "execution_result": {},
        "side_effect": {},
        "error_code": None,
        "error_message": None,
        "metadata": {},
        "created_at": datetime(2026, 5, 20, 10, 1, 0),
        "updated_at": datetime(2026, 5, 20, 10, 1, 1),
        "completed_at": datetime(2026, 5, 20, 10, 1, 1),
    }


def execution_row(
    item: dict, *, status: str = "executed", template_id: str = "create_followup_task"
) -> dict:
    return {
        "id": 33,
        "workspace_id": "workspace-A",
        "item_id": item["id"],
        "template_id": template_id,
        "mode": "execute_live",
        "status": status,
        "payload": {"idempotency_key": "idem-1"},
        "result": {
            "ok": True,
            "target": "decision_actions",
            "idempotency_key": "idem-1",
        },
        "error": None,
        "actor_email": "ops@example.com",
        "created_at": datetime(2026, 5, 20, 10, 2, 0),
        "completed_at": datetime(2026, 5, 20, 10, 2, 1),
    }
