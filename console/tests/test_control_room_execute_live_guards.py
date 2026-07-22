from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from console.tests.control_room_execution_helpers import (
    enable_successful_writes,
    executed_item,
)
from console.tests.control_room_execution_router_helpers import (
    TransactionalPool,
    execution_fetchrow_router,
)
from console.tests.test_control_room_service import USER, finance_fetcher


async def _base_item() -> dict:
    items = (
        await control_room_service._collect_items(  # noqa: SLF001
            USER,
            fetcher=finance_fetcher,
            include_source_state_items=True,
            persist=False,
            use_catalog=False,
        )
    )["items"]
    return items[0]


@pytest.mark.asyncio
async def test_execute_live_requires_explicit_confirmation(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    item = executed_item(await _base_item())
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
                fetcher=finance_fetcher,
            )

    assert exc.value.status_code == 409
    assert "confirmation" in str(exc.value.detail)
    assert (
        audit_event.await_args.kwargs["metadata"]["reason"]
        == "explicit_confirmation_required"
    )


@pytest.mark.asyncio
async def test_execute_live_requires_dry_run_before_internal_writeback(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    item = control_room_service._with_omega(  # noqa: SLF001
        {
            **(await _base_item()),
            "decision_id": 42,
            "status": "decision_created",
            "execution_status": "preview_generated",
        }
    )
    mock_pool = AsyncMock()
    enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=execution_fetchrow_router(
            item, execution_status="blocked", dry_run_exists=False
        )
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
                confirm_execute=True,
                fetcher=finance_fetcher,
            )

    assert exc.value.status_code == 409
    assert audit_event.await_args.kwargs["metadata"]["reason"] == "dry_run_required"


@pytest.mark.asyncio
async def test_execute_live_rejects_decision_from_other_workspace(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    item = executed_item(await _base_item())
    mock_pool = AsyncMock()
    enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=execution_fetchrow_router(
            item, execution_status="blocked", decision_exists=False
        )
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
                confirm_execute=True,
                fetcher=finance_fetcher,
            )

    assert exc.value.status_code == 404
    assert (
        audit_event.await_args.kwargs["metadata"]["reason"]
        == "decision_workspace_mismatch"
    )


@pytest.mark.asyncio
async def test_execute_live_idempotency_lookup_failure_blocks_before_writeback(
    monkeypatch,
):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    item = executed_item(await _base_item())
    mock_pool = AsyncMock()
    enable_successful_writes(mock_pool)
    base_router = execution_fetchrow_router(item, execution_status="blocked")

    def failing_reservation(query, *args):
        if "INSERT INTO action_runs" in str(query):
            raise RuntimeError("lookup down")
        return base_router(query, *args)

    mock_pool.fetchrow = AsyncMock(side_effect=failing_reservation)
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
                confirm_execute=True,
                idempotency_key="idem-1",
                fetcher=finance_fetcher,
            )

    assert exc.value.status_code == 503
    audit_event.assert_not_awaited()
    assert not any(
        "INSERT INTO decision_actions" in c.args[0]
        for c in mock_pool.fetchrow.call_args_list
    )


@pytest.mark.asyncio
async def test_execute_live_audit_failure_aborts_internal_writeback(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    item = executed_item(await _base_item())
    action_row = {
        "id": 101,
        "decision_id": 42,
        "action_text": "Seguimiento operativo Control Room",
        "note": "ok",
        "actor": "ops@example.com",
        "ts": datetime(2026, 5, 20, 10, 2, 0),
    }
    pool = TransactionalPool(
        pool_fetchrow_side_effect=[],
        conn_fetchrow_side_effect=execution_fetchrow_router(
            item, action_row=action_row
        ),
    )

    with (
        patch.object(control_room_service.auth, "pool", return_value=pool),
        patch.object(
            control_room_service, "_item_for_mutation", new=AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service,
            "_record_writeback_audit_event",
            new=AsyncMock(side_effect=RuntimeError("audit failed")),
        ) as audit_event,
    ):
        with pytest.raises(RuntimeError, match="audit failed"):
            await control_room_service.execute_item(
                item["id"],
                USER,
                template_id="create_followup_task",
                confirm_execute=True,
                idempotency_key="idem-1",
                fetcher=finance_fetcher,
            )

    audit_event.assert_awaited_once()
    assert pool.conn.transaction_entered is True
    assert pool.conn.transaction_exited is True
    assert pool.conn.transaction_error is RuntimeError
