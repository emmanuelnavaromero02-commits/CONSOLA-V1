from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from console.tests.control_room_execution_helpers import (
    enable_successful_writes,
    executed_item,
    explicit_action,
    explicit_binding_id,
)
from console.tests.control_room_execution_router_helpers import (
    TransactionalPool,
    execution_fetchrow_router,
)
from console.tests.test_control_room_service import USER, finance_fetcher


async def _pnl_item() -> dict:
    items = (
        await control_room_service._collect_items(  # noqa: SLF001
            USER,
            fetcher=finance_fetcher,
            include_source_state_items=True,
            persist=False,
            use_catalog=False,
        )
    )["items"]
    item = executed_item(
        next(item for item in items if item["source_dataset"] == "pnl_mensual")
    )
    return explicit_action(item, template_id="create_followup_task")[0]


def _action_row() -> dict:
    return {
        "id": 101,
        "decision_id": 42,
        "action_text": "Seguimiento operativo Control Room",
        "note": "ok",
        "actor": "ops@example.com",
        "ts": datetime(2026, 5, 20, 10, 2, 0),
    }


@pytest.mark.asyncio
async def test_execute_live_supported_followup_writes_decision_action_and_audits(
    monkeypatch,
):
    monkeypatch.delenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", raising=False)
    item = await _pnl_item()
    mock_pool = AsyncMock()
    enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=execution_fetchrow_router(item, action_row=_action_row())
    )
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service, "_record_writeback_audit_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.execute_item(
            item["id"],
            USER,
            template_id="create_followup_task",
            binding_id=explicit_binding_id(item, template_id="create_followup_task"),
            confirm_execute=True,
            idempotency_key="idem-1",
            fetcher=finance_fetcher,
        )

    assert result["executed"] is True
    assert result["idempotent"] is False
    assert result["result"]["target"] == "decision_actions"
    assert result["payload"]["writeback"]["mode"] == "supervised_execution"
    assert result["payload"]["external_writeback_enabled"] is False
    assert result["decision_action"]["id"] == 101
    assert result["item"]["execution_status"] == "executed"
    assert any(
        "INSERT INTO decision_actions" in c.args[0]
        for c in mock_pool.fetchrow.call_args_list
    )
    assert any(
        "INSERT INTO action_runs" in c.args[0]
        for c in mock_pool.fetchrow.call_args_list
    )
    assert any("FOR UPDATE" in c.args[0] for c in mock_pool.fetchrow.call_args_list)
    assert any(
        "action_executed" in str(c.args) for c in mock_pool.execute.call_args_list
    )
    audit_event.assert_awaited_once()
    assert audit_event.await_args.kwargs["action"] == "control_room.action.execute"
    assert audit_event.await_args.kwargs["metadata"]["target"] == "decision_actions"


@pytest.mark.asyncio
async def test_execute_live_supported_followup_uses_transaction_and_lock(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    item = await _pnl_item()
    pool = TransactionalPool(
        pool_fetchrow_side_effect=[],
        conn_fetchrow_side_effect=execution_fetchrow_router(
            item, action_row=_action_row()
        ),
    )

    with (
        patch.object(control_room_service.auth, "pool", return_value=pool),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
    ):
        result = await control_room_service.execute_item(
            item["id"],
            USER,
            template_id="create_followup_task",
            binding_id=explicit_binding_id(item, template_id="create_followup_task"),
            confirm_execute=True,
            idempotency_key="idem-1",
            fetcher=finance_fetcher,
        )

    assert result["executed"] is True
    assert pool.conn.acquired is True
    assert pool.conn.released is True
    assert pool.conn.transaction_entered is True
    assert pool.conn.transaction_exited is True
    assert pool.conn.transaction_error is None
    assert any(
        "INSERT INTO action_runs" in c.args[0]
        for c in pool.conn.fetchrow.call_args_list
    )
    assert any("FOR UPDATE" in c.args[0] for c in pool.conn.fetchrow.call_args_list)
    assert any(
        "INSERT INTO audit_events" in c.args[0]
        for c in pool.conn.execute.call_args_list
    )


@pytest.mark.asyncio
async def test_execute_live_supported_followup_is_idempotent(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    item = await _pnl_item()
    mock_pool = AsyncMock()
    enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=execution_fetchrow_router(item, existing_reservation=True)
    )
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ) as audit_event,
    ):
        result = await control_room_service.execute_item(
            item["id"],
            USER,
            template_id="create_followup_task",
            binding_id=explicit_binding_id(item, template_id="create_followup_task"),
            confirm_execute=True,
            idempotency_key="idem-1",
            fetcher=finance_fetcher,
        )

    assert result["executed"] is True
    assert result["idempotent"] is True
    assert not any(
        "INSERT INTO decision_actions" in c.args[0]
        for c in mock_pool.fetchrow.call_args_list
    )
    audit_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_replay_does_not_require_template_to_remain_enabled(
    monkeypatch,
):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    item = {**(await _pnl_item()), "execution_status": "executed"}
    route = execution_fetchrow_router(item, existing_reservation=True)
    catalog_queries: list[str] = []

    def disabled_catalog(query, *args):
        sql = " ".join(str(query).split()).upper()
        if "FROM CONTROL_ROOM_ACTION_TEMPLATES" in sql:
            catalog_queries.append(sql)
            return None
        return route(query, *args)

    mock_pool = AsyncMock()
    enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(side_effect=disabled_catalog)
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=item),
        ),
    ):
        result = await control_room_service.execute_item(
            item["id"],
            USER,
            template_id="create_followup_task",
            binding_id=explicit_binding_id(item, template_id="create_followup_task"),
            confirm_execute=True,
            idempotency_key="idem-1",
            fetcher=finance_fetcher,
        )

    assert result["idempotent"] is True
    assert catalog_queries == []


@pytest.mark.asyncio
async def test_execute_live_idempotent_replay_still_requires_confirmation(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    item = await _pnl_item()
    mock_pool = AsyncMock()
    enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=execution_fetchrow_router(item, execution_status="blocked")
    )
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

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
                binding_id=explicit_binding_id(
                    item, template_id="create_followup_task"
                ),
                idempotency_key="idem-1",
                fetcher=finance_fetcher,
            )

    assert exc.value.status_code == 409
    assert (
        audit_event.await_args.kwargs["metadata"]["reason"]
        == "explicit_confirmation_required"
    )
