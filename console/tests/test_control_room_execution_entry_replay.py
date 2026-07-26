from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.services.control_room.business_execution_entry import (
    prepare_execution_entry,
)


USER = {
    "id": 7,
    "email": "owner@example.com",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
    "_effective_permissions": ["control_room.write", "control_room.execute"],
}
ITEM = {
    "id": "item-1",
    "kind": "anomaly",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "decision_id": 42,
    "status": "approved",
    "execution_status": "executed",
    "source_dataset": "gold_people",
    "metric_type": "count",
    "observed_value": 1,
    "population_count": 10,
    "observation_date": "2026-07-20",
}
TEMPLATE = {"template_id": "create_followup_task"}


def _runner(conn: object) -> tuple[AsyncMock, object]:
    async def run(work):
        return await work(conn)

    return AsyncMock(side_effect=run), conn


@pytest.mark.asyncio
async def test_exact_completed_retry_replays_without_writes() -> None:
    run_replay_scoped, _conn = _runner(object())
    run_scoped = AsyncMock(side_effect=AssertionError("write scope reached"))
    ensure = AsyncMock()
    record = AsyncMock()
    response = Mock(return_value={"idempotent": True})
    with patch(
        "app.services.control_room.business_execution_entry.matching_action_replay",
        AsyncMock(
            return_value=(
                "cr-action:v1:exact",
                {"id": 9, "status": "completed", "execution_result": {}},
            )
        ),
    ) as replay:
        result = await prepare_execution_entry(
            run_scoped=run_scoped,
            run_replay_scoped=run_replay_scoped,
            ensure_item_row=ensure,
            record_execute_block=record,
            response_for_reservation=response,
            user=USER,
            item=ITEM,
            template=TEMPLATE,
            payload={},
            confirmed=True,
            ip=None,
            user_agent=None,
        )

    assert result == {"idempotent": True}
    assert replay.await_args.kwargs["workspace_id"] == "workspace-a"
    run_scoped.assert_not_awaited()
    ensure.assert_not_awaited()
    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfirmed_retry_keeps_existing_block_path() -> None:
    run_scoped, _conn = _runner(object())
    ensure = AsyncMock()
    record = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_execution_entry.matching_action_replay",
            AsyncMock(),
        ) as replay,
        pytest.raises(HTTPException) as exc,
    ):
        await prepare_execution_entry(
            run_scoped=run_scoped,
            run_replay_scoped=run_scoped,
            ensure_item_row=ensure,
            record_execute_block=record,
            response_for_reservation=Mock(),
            user=USER,
            item=ITEM,
            template=TEMPLATE,
            payload={},
            confirmed=False,
            ip=None,
            user_agent=None,
        )

    assert exc.value.status_code == 409
    replay.assert_not_awaited()
    ensure.assert_awaited_once()
    record.assert_awaited_once()


@pytest.mark.asyncio
async def test_nonmatching_retry_never_returns_unrelated_receipt() -> None:
    run_scoped, _conn = _runner(object())
    ensure = AsyncMock()
    record = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_execution_entry.matching_action_replay",
            AsyncMock(return_value=None),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await prepare_execution_entry(
            run_scoped=run_scoped,
            run_replay_scoped=run_scoped,
            ensure_item_row=ensure,
            record_execute_block=record,
            response_for_reservation=Mock(),
            user=USER,
            item=ITEM,
            template=TEMPLATE,
            payload={},
            confirmed=True,
            ip=None,
            user_agent=None,
        )

    assert exc.value.status_code == 409
    ensure.assert_awaited_once()
    record.assert_awaited_once()
