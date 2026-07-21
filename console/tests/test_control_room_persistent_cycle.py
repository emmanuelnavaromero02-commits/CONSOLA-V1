from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_execution_precondition import dry_run_metadata
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
    "active_workspace_id": "workspace-A",
    "allowed_cartridges": ["replicon"],
    "_effective_permissions": [
        "control_room.read",
        "control_room.write",
        "control_room.execute",
    ],
}


def _successful_command_tag(query: object, *_args: object) -> str:
    sql = " ".join(str(query).split()).upper()
    for command in ("INSERT", "UPDATE", "DELETE"):
        if sql.startswith(command) or f" {command} " in f" {sql} ":
            return f"{command} 0 1" if command == "INSERT" else f"{command} 1"
    return "SELECT 1"


def _enable_successful_writes(mock_pool: AsyncMock) -> None:
    mock_pool.execute = AsyncMock(side_effect=_successful_command_tag)


def _item(**overrides):
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
        "status": "decision_created",
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
        )
    )
    return control_room_service._with_omega({**base, **overrides})  # noqa: SLF001


def _legacy_execution(
    item,
    *,
    row_id: int,
    mode: str,
    status: str,
    template_id: str = "create_followup_task",
):
    return {
        "id": row_id,
        "workspace_id": "workspace-A",
        "item_id": item["id"],
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


def _action_run_row(
    item, *, row_id: int = 501, template_id: str = "create_followup_task"
):
    return {
        "id": row_id,
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "item_id": item["id"],
        "decision_id": 42,
        "legacy_execution_id": 101,
        "action_type": template_id,
        "adapter_name": "internal_followup_task",
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
        "metadata": dry_run_metadata(item, template_id=template_id),
        "created_at": datetime(2026, 6, 12, 10, 0, 0),
        "updated_at": datetime(2026, 6, 12, 10, 0, 1),
        "completed_at": datetime(2026, 6, 12, 10, 0, 1),
    }


def _persisted_item_row(item: dict) -> dict:
    metadata = {
        key: item[key]
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
    metadata.update(
        {
            CURRENT_ELIGIBILITY_FINGERPRINT_KEY: business_observation_fingerprint(item),
            ELIGIBILITY_POLICY_VERSION_KEY: ELIGIBILITY_POLICY_VERSION,
            DECISION_PROVENANCE_KEY: workflow_eligibility_provenance(
                item,
                stage=WorkflowStage.DECISION_CREATED,
                workspace_id=item["workspace_id"],
                decision_id=item["decision_id"],
            ),
        }
    )
    return {
        "tenant_id": item["tenant_id"],
        "workspace_id": item["workspace_id"],
        "owner_user_id": item["owner_user_id"],
        "item_id": item["id"],
        "cartridge_id": item["cartridge"],
        "source_dataset": item["source_dataset"],
        "item_kind": item["kind"],
        "entity_id": item["entity_id"],
        "status": item["status"],
        "decision_id": item["decision_id"],
        "selected_option_id": item.get("selected_option_id"),
        "execution_status": item["execution_status"],
        "metadata": metadata,
    }


def _guarded_fetchrows(item: dict, rows, *, has_dry_run: bool = True):
    iterator = iter(rows)
    reservation: dict = {}

    def fetchrow(query, *_args):
        sql = " ".join(str(query).split())
        if "FROM control_room_items" in sql and "FOR UPDATE" in sql:
            return _persisted_item_row(item)
        if "mode = 'dry_run'" in sql and "status = 'dry_run_completed'" in sql:
            return _action_run_row(item) if has_dry_run else None
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
            return {"id": item["decision_id"]}
        if "INSERT INTO decision_actions" in sql:
            return {
                "id": 301,
                "decision_id": item["decision_id"],
                "action_text": "Seguimiento operativo Control Room",
                "note": "ok",
                "actor": "ops@example.com",
                "ts": datetime(2026, 6, 12, 10, 2, 0),
            }
        if "INSERT INTO control_room_action_executions" in sql:
            return _legacy_execution(
                item,
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
                "signal_id": item["id"],
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


@pytest.mark.asyncio
async def test_control_room_persistent_cycle_records_action_run_outcome_lesson_and_audit():
    item = _item()
    action_row = {
        "id": 301,
        "decision_id": 42,
        "action_text": "Seguimiento operativo Control Room",
        "note": "ok",
        "actor": "ops@example.com",
        "ts": datetime(2026, 6, 12, 10, 2, 0),
    }
    outcome_row = {
        "id": 701,
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "signal_id": item["id"],
        "option_id": "remediate",
        "action_taken": "create_followup_task",
        "predicted_value": 12000,
        "actual_value": 9500,
        "prediction_error": -2500,
        "outcome_summary": "Seguimiento redujo el riesgo",
        "learned_rule": "Cuando WIP sube, abrir seguimiento financiero semanal.",
        "metadata": {"source": "control_room", "reported_by": "ops@example.com"},
        "created_at": datetime(2026, 6, 12, 10, 4, 0),
    }
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchval = AsyncMock(side_effect=[501, 502])
    mock_pool.fetchrow = AsyncMock(
        side_effect=_guarded_fetchrows(
            item,
            [
                _legacy_execution(item, row_id=101, mode="dry_run", status="validated"),
                None,
                _action_run_row(item),
                None,
                {"id": 42},
                action_row,
                _legacy_execution(
                    item, row_id=201, mode="execute_live", status="executed"
                ),
                outcome_row,
                {"id": 9001},
            ],
        )
    )

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        dry_run = await control_room_service.action_dry_run(
            item["id"],
            USER,
            template_id="create_followup_task",
        )
        item_after_dry_run = control_room_service._with_omega(
            {**item, "execution_status": "dry_run_validated"}
        )  # noqa: SLF001
        item["execution_status"] = "dry_run_validated"
        control_room_service._item_for_mutation.return_value = item_after_dry_run
        executed = await control_room_service.execute_item(
            item["id"],
            USER,
            template_id="create_followup_task",
            confirm_execute=True,
            idempotency_key="cycle-1",
        )
        outcome = await control_room_service.record_item_outcome(
            item["id"],
            {
                "action_taken": "create_followup_task",
                "option_id": "remediate",
                "actual_value": 9500,
                "predicted_value": 12000,
                "outcome_summary": "Seguimiento redujo el riesgo",
                "learned_rule": "Cuando WIP sube, abrir seguimiento financiero semanal.",
                "action_run_id": executed["action_run"]["id"],
            },
            USER,
        )

    assert dry_run["action_run"]["status"] == "dry_run_completed"
    assert dry_run["result"]["validated"] is True
    assert executed["action_run"]["status"] == "completed"
    assert executed["result"]["target"] == "decision_actions"
    assert outcome["recorded"] is True
    assert outcome["lesson_recorded"] is True
    assert outcome["outcome"]["id"] == 701

    fetchval_sql = "\n".join(
        str(call.args[0]) for call in mock_pool.fetchval.call_args_list
    )
    execute_sql = "\n".join(
        str(call.args[0]) for call in mock_pool.execute.call_args_list
    )
    fetchrow_sql = "\n".join(
        str(call.args[0]) for call in mock_pool.fetchrow.call_args_list
    )
    assert "INSERT INTO action_runs" in fetchval_sql
    assert "INSERT INTO action_run_events" in execute_sql
    assert "INSERT INTO decision_actions" in fetchrow_sql
    assert "INSERT INTO prediction_outcomes" in fetchrow_sql
    assert "INSERT INTO control_room_lessons" in execute_sql
    assert "INSERT INTO audit_events" in execute_sql
    assert "set_config('app.tenant_id'" in execute_sql

    audit_actions = [call.kwargs["action"] for call in audit_event.await_args_list]
    assert "control_room.action.dry_run" in audit_actions
    assert "control_room.outcome.record" in audit_actions


@pytest.mark.asyncio
async def test_dry_run_fails_closed_when_action_run_cannot_persist():
    item = _item()
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchrow.side_effect = _guarded_fetchrows(
        item,
        [_legacy_execution(item, row_id=101, mode="dry_run", status="validated")],
    )
    mock_pool.fetchval.side_effect = RuntimeError("action_runs unavailable")

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        with pytest.raises(RuntimeError, match="action_runs unavailable"):
            await control_room_service.action_dry_run(
                item["id"],
                USER,
                template_id="create_followup_task",
            )

    execute_sql = "\n".join(
        str(call.args[0]) for call in mock_pool.execute.call_args_list
    )
    assert "SET execution_status = $1" not in execute_sql
    audit_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_blocks_when_visual_dry_run_has_no_persistent_action_run():
    item = _item(execution_status="dry_run_validated")
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 901
    mock_pool.fetchrow = AsyncMock(
        side_effect=_guarded_fetchrows(
            item,
            [
                None,
                None,
                _legacy_execution(
                    item, row_id=333, mode="execute_live", status="blocked"
                ),
            ],
            has_dry_run=False,
        )
    )

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.execute_item(
                item["id"],
                USER,
                template_id="create_followup_task",
                confirm_execute=True,
            )

    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "code": "matching_dry_run_required",
        "message": "a matching successful dry-run is required before execution",
    }
    fetchval_sql = "\n".join(
        str(call.args[0]) for call in mock_pool.fetchval.call_args_list
    )
    assert "INSERT INTO action_runs" not in fetchval_sql
    audit_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_block_preserves_successful_dry_run_status():
    item = _item(execution_status="dry_run_validated")
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 902
    mock_pool.fetchrow.side_effect = _guarded_fetchrows(
        item,
        [_legacy_execution(item, row_id=334, mode="execute_live", status="blocked")],
    )

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.execute_item(
                item["id"],
                USER,
                template_id="create_followup_task",
                confirm_execute=False,
            )

    assert exc.value.status_code == 409
    assert (
        audit_event.await_args.kwargs["metadata"]["reason"]
        == "explicit_confirmation_required"
    )
    status_updates = [
        call
        for call in mock_pool.execute.call_args_list
        if "SET execution_status = $1" in str(call.args[0])
    ]
    assert not any(call.args[1] == "blocked" for call in status_updates)
