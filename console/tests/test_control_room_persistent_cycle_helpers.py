from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock

from app.services import control_room_service
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_explicit_action_binding import (
    attach_explicit_action_binding,
)
from app.services.control_room.business_execution_precondition import dry_run_metadata
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    ELIGIBILITY_POLICY_VERSION_KEY,
    WorkflowStage,
    business_observation_fingerprint,
    workflow_eligibility_provenance,
)

USER = {
    "id": 7,
    "email": "ops@example.com",
    "tenant_id": "tenant-A",
    "role": "admin",
    "active_workspace_id": "workspace-A",
    "allowed_cartridges": ["replicon"],
    "_effective_permissions": [
        "control_room.read",
        "control_room.write",
        "control_room.execute",
    ],
}


def successful_command_tag(query: object, *_args: object) -> str:
    sql = " ".join(str(query).split()).upper()
    for command in ("INSERT", "UPDATE", "DELETE"):
        if sql.startswith(command) or f" {command} " in f" {sql} ":
            return f"{command} 0 1" if command == "INSERT" else f"{command} 1"
    return "SELECT 1"


def enable_successful_writes(mock_pool: AsyncMock) -> None:
    mock_pool.execute = AsyncMock(side_effect=successful_command_tag)


def item(**overrides):
    base = {
        "id": "signal-replicon-low-margin-1",
        "kind": "anomaly",
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "owner_user_id": 7,
        "cartridge": "replicon",
        "source_system": "replicon",
        "domain": "Operacion",
        "source_dataset": "gold_pnl_mensual",
        "title": "Margen bajo",
        "description": "Margen bajo en proyecto beta",
        "severity": "high",
        "status": "approved",
        "decision_id": 42,
        "execution_status": "not_started",
        "data_status": "ready",
        "metric_type": "amount",
        "observed_value": 12000,
        "population_count": 1,
        "detected_at": "2026-06-12T10:00:00Z",
        "entity_kind": "project",
        "entity_id": "P-100",
        "entity_label": "Proyecto Beta",
        "anomaly_type": "low_margin",
        "root_cause": "WIP alto sin facturacion asociada",
        "recommendation": "Crear seguimiento operativo y monitorear margen",
        "details": {"margin_pct": 8.5, "revenue_usd": 100000},
        "impact_estimate": 12000,
        "omega": {
            "options": [{"id": "remediate"}, {"id": "monitor"}],
            "lessons": {"rules": []},
        },
    }
    base.update(
        runtime_row_evidence_fields(
            source_dataset="gold_pnl_mensual",
            source_system="replicon",
            cartridge="replicon",
            tenant_id="tenant-A",
            workspace_id="workspace-A",
            source_row={"project_id": "P-100", "observed_value": 12000},
            locator_field="project_id",
            observed_at="2026-06-12T10:00:00Z",
            business_observation=base,
        )
    )
    projected = control_room_service._with_omega({**base, **overrides})  # noqa: SLF001
    return attach_explicit_action_binding(
        projected,
        template_id="create_followup_task",
    )


def binding_id(value: dict, template_id: str = "create_followup_task") -> str:
    bindings = value["metadata"]["explicit_action_bindings"]
    return str(
        next(binding for binding in bindings if binding["template_id"] == template_id)[
            "binding_id"
        ]
    )


def legacy_execution(
    value,
    *,
    row_id: int,
    mode: str,
    status: str,
    template_id: str = "create_followup_task",
):
    return {
        "id": row_id,
        "workspace_id": "workspace-A",
        "item_id": value["id"],
        "template_id": template_id,
        "mode": mode,
        "status": status,
        "payload": {},
        "result": {},
        "error": None,
        "actor_email": "ops@example.com",
        "created_at": datetime(2026, 6, 12, 10, 0, 0),
        "completed_at": datetime(2026, 6, 12, 10, 0, 1),
    }


def action_run_row(
    value, *, row_id: int = 501, template_id: str = "create_followup_task"
):
    return {
        "id": row_id,
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "item_id": value["id"],
        "decision_id": 42,
        "legacy_execution_id": 101,
        "action_type": template_id,
        "adapter_name": "internal_followup_task",
        "mode": "dry_run",
        "status": "dry_run_completed",
        "risk_level": "low",
        "requires_approval": True,
        "approval_status": "implicit_internal_beta",
        "idempotency_key": f"dry_run:{value['id']}:{template_id}:42",
        "actor_id": 7,
        "actor_email": "ops@example.com",
        "input": {},
        "dry_run_result": {"ok": True, "validated": True},
        "execution_result": {},
        "side_effect": {},
        "error_code": None,
        "error_message": None,
        "metadata": dry_run_metadata(value, template_id=template_id),
        "created_at": datetime(2026, 6, 12, 10, 0, 0),
        "updated_at": datetime(2026, 6, 12, 10, 0, 1),
        "completed_at": datetime(2026, 6, 12, 10, 0, 1),
    }


def persisted_item_row(value: dict) -> dict:
    metadata = {
        key: value[key]
        for key in (
            "data_status",
            "metric_type",
            "observed_value",
            "population_count",
            "detected_at",
            "evidence_refs",
            "source_system",
        )
    }
    stage = (
        WorkflowStage.APPROVED
        if value.get("status") == "approved"
        else WorkflowStage.DECISION_CREATED
    )
    metadata.update(
        {
            CURRENT_ELIGIBILITY_FINGERPRINT_KEY: business_observation_fingerprint(
                value
            ),
            ELIGIBILITY_POLICY_VERSION_KEY: ELIGIBILITY_POLICY_VERSION,
            DECISION_PROVENANCE_KEY: workflow_eligibility_provenance(
                value,
                stage=stage,
                workspace_id=value["workspace_id"],
                decision_id=value["decision_id"],
            ),
        }
    )
    return {
        "tenant_id": value["tenant_id"],
        "workspace_id": value["workspace_id"],
        "owner_user_id": value["owner_user_id"],
        "item_id": value["id"],
        "cartridge_id": value["cartridge"],
        "source_dataset": value["source_dataset"],
        "item_kind": value["kind"],
        "entity_id": value["entity_id"],
        "status": value["status"],
        "decision_id": value["decision_id"],
        "selected_option_id": value.get("selected_option_id"),
        "execution_status": value["execution_status"],
        "metadata": metadata,
    }


def guarded_fetchrows(value: dict, rows, *, has_dry_run: bool = True):
    iterator = iter(rows)
    reservation: dict = {}

    def fetchrow(query, *_args):
        sql = " ".join(str(query).split())
        if "FROM control_room_action_templates" in sql:
            template_id = str(_args[0])
            template = ACTION_TEMPLATES[template_id]
            return {
                "template_id": template_id,
                "cartridge_id": template["cartridge_id"],
                "label": template["label"],
                "requires_approval": template["requires_approval"],
            }
        if "FROM control_room_items" in sql and "FOR UPDATE" in sql:
            return persisted_item_row(value)
        if "mode = 'dry_run'" in sql and "status = 'dry_run_completed'" in sql:
            return action_run_row(value) if has_dry_run else None
        if "SELECT id" in sql and "FROM action_runs" in sql and "LIMIT 1" in sql:
            return None
        if "INSERT INTO action_runs" in sql and "'pending'" in sql:
            reservation.update(
                {
                    "id": 502,
                    "status": "pending",
                    "idempotency_key": _args[6],
                    "metadata": json.loads(_args[10]),
                }
            )
            return dict(reservation)
        if "FROM action_runs" in sql and "FOR UPDATE" in sql:
            return dict(reservation) if reservation else None
        if "UPDATE action_runs" in sql and "status = 'pending'" in sql:
            return {**reservation, "status": _args[3]}
        if "SELECT id FROM decisions" in sql:
            return {"id": value["decision_id"]}
        if "INSERT INTO decision_actions" in sql:
            return {
                "id": 301,
                "decision_id": value["decision_id"],
                "action_text": "Seguimiento operativo Control Room",
                "note": "ok",
                "actor": "ops@example.com",
                "ts": datetime(2026, 6, 12, 10, 2, 0),
            }
        if "INSERT INTO control_room_action_executions" in sql:
            return legacy_execution(
                value,
                row_id=201 if _args[4] == "execute_live" else 101,
                mode=_args[4],
                status=_args[5],
                template_id=_args[3],
            )
        if "INSERT INTO prediction_outcomes" in sql:
            return {
                "id": 701,
                "tenant_id": "tenant-A",
                "workspace_id": "workspace-A",
                "signal_id": value["id"],
                "option_id": "remediate",
                "action_taken": "create_followup_task",
                "predicted_value": 12000,
                "actual_value": 9500,
                "prediction_error": -2500,
                "outcome_summary": "Seguimiento redujo el riesgo",
                "learned_rule": "Cuando WIP sube, abrir seguimiento financiero semanal.",
                "metadata": {"source": "control_room"},
                "created_at": datetime(2026, 6, 12, 10, 4, 0),
            }
        return next(iterator)

    return fetchrow
