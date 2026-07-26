from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.services.control_room.business_action_key import effective_action_key
from app.services.control_room.business_action_replay import (
    action_reservation_contract,
    matching_action_replay,
)
from app.services.control_room.business_execution_precondition import dry_run_contract
from control_room_surface_fixtures import business_item


def _item(entity_id: str) -> dict:
    return business_item(
        entity_id=entity_id,
        decision_id=42,
        status="approved",
        execution_status="dry_run_validated",
    )


def _authorization() -> dict:
    return {
        "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "workspace_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "effective_permissions": ["control_room.write", "control_room.execute"],
    }


def test_target_digest_changes_dry_run_reservation_and_effective_key():
    item_a = _item("employee-a")
    item_b = _item("employee-b")
    dry_a = dry_run_contract(item_a, template_id="create_followup_task")
    dry_b = dry_run_contract(item_b, template_id="create_followup_task")
    reservation_a = action_reservation_contract(
        workspace_id=str(item_a["workspace_id"]),
        item=item_a,
        template_id="create_followup_task",
        operation="execute",
        authorization_contract=_authorization(),
    )
    reservation_b = action_reservation_contract(
        workspace_id=str(item_b["workspace_id"]),
        item=item_b,
        template_id="create_followup_task",
        operation="execute",
        authorization_contract=_authorization(),
    )

    assert dry_a["execution_target_digest"] != dry_b["execution_target_digest"]
    assert (
        reservation_a["execution_target_digest"]
        != reservation_b["execution_target_digest"]
    )
    assert effective_action_key(
        workspace_id=str(item_a["workspace_id"]),
        item=item_a,
        template_id="create_followup_task",
        operation="execute",
    ) != effective_action_key(
        workspace_id=str(item_b["workspace_id"]),
        item=item_b,
        template_id="create_followup_task",
        operation="execute",
    )


@pytest.mark.asyncio
async def test_completed_receipt_never_replays_for_another_target():
    item_a = _item("employee-a")
    item_b = deepcopy(item_a)
    item_b["entity_id"] = "employee-b"
    stored = action_reservation_contract(
        workspace_id=str(item_a["workspace_id"]),
        item=item_a,
        template_id="create_followup_task",
        operation="execute",
        authorization_contract=_authorization(),
    )
    db = AsyncMock()
    db.fetchrow.return_value = {
        "id": 9,
        "status": "completed",
        "metadata": json.dumps({"reservation_contract": stored}),
    }

    result = await matching_action_replay(
        db,
        workspace_id=str(item_b["workspace_id"]),
        item=item_b,
        template_id="create_followup_task",
        operation="execute",
        authorization_contract=_authorization(),
    )

    assert result is None
