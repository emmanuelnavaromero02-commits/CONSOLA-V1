from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_key import (
    legacy_effective_action_key_v1,
)
from app.services.control_room.business_action_reservation import (
    acquire_guarded_action_reservation,
)
from app.services.control_room.business_legacy_reservation_guard import (
    require_no_legacy_action_reservation,
)
from console.tests.test_control_room_action_replay import _item, _user


@pytest.mark.asyncio
async def test_legacy_reservation_is_quarantined_by_deployed_v1_key():
    db = AsyncMock()
    db.fetchrow.return_value = {"id": 17}

    with pytest.raises(HTTPException) as exc:
        await require_no_legacy_action_reservation(
            db,
            workspace_id="workspace-a",
            item=_item(),
            template_id="create_followup_task",
            operation="execute",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == (
        "legacy_action_reservation_requires_reconciliation"
    )
    assert db.fetchrow.await_args.args[1:] == (
        "workspace-a",
        legacy_effective_action_key_v1(
            workspace_id="workspace-a",
            item=_item(),
            template_id="create_followup_task",
            operation="execute",
        ),
    )


@pytest.mark.asyncio
async def test_legacy_reservation_blocks_new_reservation_after_authorization():
    persist = AsyncMock()
    reserve = AsyncMock()
    legacy_guard = AsyncMock(
        side_effect=HTTPException(
            409, {"code": "legacy_action_reservation_requires_reconciliation"}
        )
    )
    with (
        patch(
            "app.services.control_room.business_action_reservation.require_approved_execution",
            AsyncMock(return_value={"item_id": "item-1"}),
        ),
        patch(
            "app.services.control_room.business_action_reservation.require_no_legacy_action_reservation",
            legacy_guard,
        ),
        patch(
            "app.services.control_room.business_action_reservation.acquire_action_reservation",
            reserve,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await acquire_guarded_action_reservation(
                object(),
                user=_user(),
                item=_item(),
                template_id="create_followup_task",
                adapter_name="internal_followup_task",
                operation="execute",
                persist_item=persist,
            )

    assert exc.value.status_code == 409
    persist.assert_awaited_once()
    legacy_guard.assert_awaited_once()
    reserve.assert_not_awaited()
