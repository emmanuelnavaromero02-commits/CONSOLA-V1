from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.audit_service import record_event


@pytest.mark.asyncio
async def test_record_event_uses_caller_owned_connection() -> None:
    connection = AsyncMock()
    connection.fetchval.side_effect = ["audit_events", True]
    pool_factory = AsyncMock()

    with patch("app.services.audit_service.auth.pool", pool_factory):
        await record_event(
            connection=connection,
            user_id=7,
            action="control_room.approve",
            resource_type="control_room_item",
            resource_id="item-1",
            status="success",
            critical=True,
        )

    pool_factory.assert_not_awaited()
    connection.execute.assert_awaited_once()
    assert "INSERT INTO audit_events" in connection.execute.await_args.args[0]


@pytest.mark.asyncio
async def test_critical_transactional_audit_failure_propagates() -> None:
    connection = AsyncMock()
    connection.fetchval.side_effect = ["audit_events", True]
    connection.execute.side_effect = RuntimeError("audit insert failed")

    with pytest.raises(RuntimeError, match="audit insert failed"):
        await record_event(
            connection=connection,
            user_id=7,
            action="control_room.approve",
            resource_type="control_room_item",
            resource_id="item-1",
            status="success",
            critical=True,
        )
