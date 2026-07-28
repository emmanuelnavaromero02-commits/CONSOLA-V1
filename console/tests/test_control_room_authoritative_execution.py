from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
)
from app.services.control_room.business_authoritative_execution import (
    acquire_authoritative_action_reservation,
    require_authoritative_binding_current,
    revalidate_authoritative_action,
)
from app.services.control_room.business_explicit_action_binding import (
    ACTION_BINDING_TTL_SECONDS,
    attach_explicit_action_binding,
)
from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
)
from control_room_surface_fixtures import OPERATOR, business_item


ISSUED_AT = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
TEMPLATE_ID = "request_owner_review"


def _item() -> dict:
    return attach_explicit_action_binding(
        business_item(
            decision_id=42,
            status="approved",
            execution_status="dry_run_validated",
            metadata={
                "connection": {
                    "base_url": "https://hcm.example.invalid",
                    "writeback_path": "/review/a",
                }
            },
        ),
        template_id=TEMPLATE_ID,
        clock=lambda: ISSUED_AT,
    )


def _binding_id(item: dict) -> str:
    return str(item["metadata"]["explicit_action_bindings"][0]["binding_id"])


def _payload(item: dict, template: dict) -> dict:
    connection = dict(item.get("metadata", {}).get("connection") or {})
    return {
        "template_id": template["template_id"],
        "entity_id": item.get("entity_id"),
        "writeback_path": connection.get("writeback_path"),
    }


def _reservation() -> ActionReservation:
    return ActionReservation(
        id=17,
        effective_key="server-key",
        state=ReservationState.ACQUIRED,
        row={
            "id": 17,
            "status": "pending",
            "metadata": {"reservation_lease_token": "lease-17"},
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ("entity_id", "writeback_path"))
async def test_changed_target_rejects_before_approval_or_reservation(
    changed_field: str,
) -> None:
    expected = _item()
    changed = deepcopy(expected)
    if changed_field == "entity_id":
        changed["entity_id"] = "employee-b"
    else:
        changed["metadata"]["connection"]["writeback_path"] = "/review/b"
    assert business_observation_fingerprint(
        expected
    ) == business_observation_fingerprint(changed)
    approve = AsyncMock()
    reserve = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_authoritative_execution.lock_authoritative_business_item",
            AsyncMock(return_value=changed),
        ),
        patch(
            "app.services.control_room.business_authoritative_execution.require_approved_execution",
            approve,
        ),
        patch(
            "app.services.control_room.business_authoritative_execution.acquire_guarded_action_reservation",
            reserve,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await acquire_authoritative_action_reservation(
                object(),
                user=OPERATOR,
                expected_item=expected,
                expected_payload=_payload(expected, {"template_id": TEMPLATE_ID}),
                template_id=TEMPLATE_ID,
                binding_id=_binding_id(expected),
                adapter_name="internal",
                operation="execute",
                provided_key=None,
                item_builder=dict,
                payload_builder=_payload,
                clock=lambda: ISSUED_AT,
            )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_business_state_changed"
    approve.assert_not_awaited()
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_locked_item_drives_payload_and_authority_audit() -> None:
    expected = _item()
    reservation = _reservation()
    reserve = AsyncMock(return_value=reservation)
    with (
        patch(
            "app.services.control_room.business_authoritative_execution.lock_authoritative_business_item",
            AsyncMock(return_value=expected),
        ),
        patch(
            "app.services.control_room.business_authoritative_execution.require_approved_execution",
            AsyncMock(),
        ),
        patch(
            "app.services.control_room.business_authoritative_execution.acquire_guarded_action_reservation",
            reserve,
        ),
    ):
        acquired = await acquire_authoritative_action_reservation(
            object(),
            user=OPERATOR,
            expected_item=expected,
            expected_payload=_payload(expected, {"template_id": TEMPLATE_ID}),
            template_id=TEMPLATE_ID,
            binding_id=_binding_id(expected),
            adapter_name="internal",
            operation="execute",
            provided_key=None,
            item_builder=dict,
            payload_builder=_payload,
            clock=lambda: ISSUED_AT,
        )

    assert acquired.reservation is reservation
    assert acquired.context.item["entity_id"] == expected["entity_id"]
    assert acquired.context.payload == _payload(expected, acquired.context.template)
    audit = acquired.context.authority_audit
    assert audit["binding_id"] == _binding_id(expected)
    assert set(audit) == {
        "version",
        "binding_id",
        "key_id",
        "issued_at",
        "expires_at",
        "observation_fingerprint",
        "execution_target_digest",
        "template_contract_digest",
        "input_payload_digest",
    }
    assert reserve.await_args.kwargs["item"] == acquired.context.item
    assert reserve.await_args.kwargs["input_payload"] == acquired.context.payload
    assert reserve.await_args.kwargs["authority_audit"] == audit
    assert "persist_item" not in reserve.await_args.kwargs
    with pytest.raises(HTTPException) as exc:
        require_authoritative_binding_current(
            acquired.context,
            OPERATOR,
            clock=lambda: ISSUED_AT + timedelta(seconds=ACTION_BINDING_TTL_SECONDS),
        )
    assert exc.value.detail["code"] == "item_business_state_changed"


@pytest.mark.asyncio
async def test_binding_expiring_at_exact_boundary_rejects_after_item_lock() -> None:
    expected = _item()
    now = ISSUED_AT

    async def lock_after_wait(*_args, **_kwargs):
        nonlocal now
        now = ISSUED_AT + timedelta(seconds=ACTION_BINDING_TTL_SECONDS)
        return expected

    lock = AsyncMock(side_effect=lock_after_wait)
    reserve = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_authoritative_execution.lock_authoritative_business_item",
            lock,
        ),
        patch(
            "app.services.control_room.business_authoritative_execution.acquire_guarded_action_reservation",
            reserve,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await acquire_authoritative_action_reservation(
                object(),
                user=OPERATOR,
                expected_item=expected,
                expected_payload=_payload(expected, {"template_id": TEMPLATE_ID}),
                template_id=TEMPLATE_ID,
                binding_id=_binding_id(expected),
                adapter_name="internal",
                operation="execute",
                provided_key=None,
                item_builder=dict,
                payload_builder=_payload,
                clock=lambda: now,
            )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_business_state_changed"
    assert lock.await_count == 2
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_remote_revalidation_rejects_new_target_before_reservation_lock() -> (
    None
):
    expected = _item()
    reservation = _reservation()
    reserve = AsyncMock(return_value=reservation)
    common = {
        "user": OPERATOR,
        "expected_item": expected,
        "expected_payload": _payload(expected, {"template_id": TEMPLATE_ID}),
        "template_id": TEMPLATE_ID,
        "binding_id": _binding_id(expected),
        "adapter_name": "external",
        "operation": "execute",
        "provided_key": None,
        "item_builder": dict,
        "payload_builder": _payload,
        "clock": lambda: ISSUED_AT,
    }
    with (
        patch(
            "app.services.control_room.business_authoritative_execution.lock_authoritative_business_item",
            AsyncMock(return_value=expected),
        ),
        patch(
            "app.services.control_room.business_authoritative_execution.require_approved_execution",
            AsyncMock(),
        ),
        patch(
            "app.services.control_room.business_authoritative_execution.acquire_guarded_action_reservation",
            reserve,
        ),
    ):
        acquired = await acquire_authoritative_action_reservation(object(), **common)

    changed = deepcopy(expected)
    changed["entity_id"] = "employee-b"
    lock_reservation = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_authoritative_execution.lock_authoritative_business_item",
            AsyncMock(return_value=changed),
        ),
        patch(
            "app.services.control_room.business_authoritative_execution.lock_pending_action_reservation",
            lock_reservation,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await revalidate_authoritative_action(
                object(),
                user=OPERATOR,
                expected=acquired.context,
                reservation=reservation,
                item_builder=dict,
                payload_builder=_payload,
                clock=lambda: ISSUED_AT,
            )

    assert exc.value.detail["code"] == "item_business_state_changed"
    lock_reservation.assert_not_awaited()
