from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.control_room_action_requests import ControlRoomExecuteRequest
from app.services import control_room_service
from app.services.control_room.business_explicit_action_binding import (
    verified_explicit_action_bindings,
)
from control_room_runtime_evidence_fixture import bind_runtime_row_evidence
from control_room_surface_fixtures import OPERATOR, action_item


def _binding_id(item: dict) -> str:
    return str(item["metadata"]["explicit_action_bindings"][0]["binding_id"])


def test_binding_is_bound_to_current_observation_and_scope():
    item = action_item()
    assert len(verified_explicit_action_bindings(item)) == 1

    changed = deepcopy(item)
    changed["observed_value"] = 3
    bind_runtime_row_evidence(
        changed,
        locator_field="entity_id",
        observed_at=str(changed["detected_at"]),
    )
    assert verified_explicit_action_bindings(changed) == ()

    changed_scope = deepcopy(item)
    changed_scope["workspace_id"] = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert verified_explicit_action_bindings(changed_scope) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "updates", "kwargs"),
    (
        ("action_preview", {}, {}),
        ("action_dry_run", {"status": "decision_created"}, {}),
        (
            "execute_item",
            {"status": "approved", "execution_status": "dry_run_validated"},
            {"confirm_execute": True},
        ),
    ),
)
async def test_commands_revalidate_exact_binding_before_database(
    method: str,
    updates: dict,
    kwargs: dict,
):
    item = action_item(**updates)
    pool = AsyncMock(side_effect=AssertionError("database reached"))
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=item),
        ),
        patch.object(control_room_service.auth, "pool", pool),
    ):
        with pytest.raises(HTTPException) as exc:
            await getattr(control_room_service, method)(
                str(item["id"]),
                OPERATOR,
                template_id="create_followup_task",
                binding_id=_binding_id(item),
                **kwargs,
            )

    assert exc.value.status_code == 404
    pool.assert_not_awaited()


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (" key-1 ", "key-1"),
        ("x" * 128, "x" * 128),
        (f"  {'x' * 128}  ", "x" * 128),
    ),
)
def test_idempotency_key_is_normalized_before_length_validation(value, expected):
    request = ControlRoomExecuteRequest(
        template_id="request_owner_review",
        binding_id="a" * 64,
        idempotency_key=value,
    )
    assert request.idempotency_key == expected


@pytest.mark.parametrize("value", ("   ", "x\n", "x\x7f", "x\u0085", "x" * 129))
def test_idempotency_key_rejects_empty_controls_and_overflow(value: str):
    with pytest.raises(ValidationError):
        ControlRoomExecuteRequest(
            template_id="request_owner_review",
            binding_id="a" * 64,
            idempotency_key=value,
        )
