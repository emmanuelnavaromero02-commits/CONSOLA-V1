from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from console.tests.test_control_room_persistent_cycle_helpers import (
    USER,
    action_run_row as _action_run_row,
    enable_successful_writes as _enable_successful_writes,
    guarded_fetchrows as _guarded_fetchrows,
    item as _item,
    legacy_execution as _legacy_execution,
)


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
