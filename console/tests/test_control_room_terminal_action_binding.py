from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from console.tests.control_room_execution_helpers import explicit_action
from console.tests.test_control_room_terminal_execution import (
    USER,
    _enable_successful_writes,
    _executed_item,
    _execution_fetchrow_router,
    finance_fetcher,
)


@pytest.mark.asyncio
async def test_execute_live_rejects_resolved_terminal_item(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    base_item = (
        await control_room_service._collect_items(  # noqa: SLF001
            USER,
            fetcher=finance_fetcher,
            include_source_state_items=True,
            persist=False,
            use_catalog=False,
        )
    )["items"][0]
    item = _executed_item(base_item, status="resolved")
    item, binding_id = explicit_action(item, template_id="create_followup_task")
    mock_pool = AsyncMock()
    _enable_successful_writes(mock_pool)
    mock_pool.fetchrow = AsyncMock(
        side_effect=_execution_fetchrow_router(item, execution_status="blocked")
    )
    mock_pool.fetch.return_value = []
    mock_pool.fetchval.return_value = 0

    with (
        patch.object(control_room_service.auth, "pool", return_value=mock_pool),
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=item),
        ),
        patch.object(
            control_room_service.audit_service,
            "record_event",
            new=AsyncMock(),
        ) as audit_event,
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.execute_item(
                item["id"],
                USER,
                template_id="create_followup_task",
                binding_id=binding_id,
                confirm_execute=True,
                idempotency_key="idem-1",
                fetcher=finance_fetcher,
            )

    assert exc.value.status_code == 409
    assert audit_event.await_args.kwargs["metadata"]["reason"] == "terminal_item"
