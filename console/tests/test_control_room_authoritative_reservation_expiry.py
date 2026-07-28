from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.control_room.business_authoritative_execution import (
    acquire_authoritative_action_reservation,
)
from app.services.control_room.business_explicit_action_binding import (
    ACTION_BINDING_TTL_SECONDS,
    attach_explicit_action_binding,
)
from control_room_surface_fixtures import OPERATOR, business_item


ISSUED_AT = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
TEMPLATE_ID = "request_owner_review"


@pytest.mark.asyncio
async def test_expiry_immediately_before_reservation_insert_fails_closed() -> None:
    now = ISSUED_AT
    item = attach_explicit_action_binding(
        business_item(
            decision_id=42,
            status="approved",
            execution_status="dry_run_validated",
        ),
        template_id=TEMPLATE_ID,
        clock=lambda: ISSUED_AT,
    )
    binding_id = item["metadata"]["explicit_action_bindings"][0]["binding_id"]
    payload = {"template_id": TEMPLATE_ID, "entity_id": item.get("entity_id")}
    insert = AsyncMock()

    async def reserve(*_args, reservation_guard, **_kwargs):
        nonlocal now
        now = ISSUED_AT + timedelta(seconds=ACTION_BINDING_TTL_SECONDS)
        reservation_guard()
        await insert()

    with (
        patch(
            "app.services.control_room.business_authoritative_execution."
            "lock_authoritative_business_item",
            AsyncMock(return_value=item),
        ),
        patch(
            "app.services.control_room.business_authoritative_execution."
            "acquire_guarded_action_reservation",
            side_effect=reserve,
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await acquire_authoritative_action_reservation(
            object(),
            user=OPERATOR,
            expected_item=item,
            expected_payload=payload,
            template_id=TEMPLATE_ID,
            binding_id=str(binding_id),
            adapter_name="external",
            operation="execute",
            provided_key=None,
            item_builder=dict,
            payload_builder=lambda current, template: {
                "template_id": template["template_id"],
                "entity_id": current.get("entity_id"),
            },
            clock=lambda: now,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_business_state_changed"
    insert.assert_not_awaited()
