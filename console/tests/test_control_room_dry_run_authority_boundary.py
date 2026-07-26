from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room import business_authoritative_execution
from console.tests.control_room_execution_helpers import authoritative_item_row
from console.tests.test_control_room_persistent_cycle_helpers import (
    USER,
    binding_id,
    item,
)


class NoDmlConnection:
    def __init__(self) -> None:
        self.execute = AsyncMock(side_effect=AssertionError("execute reached"))
        self.fetch = AsyncMock(side_effect=AssertionError("fetch reached"))
        self.fetchrow = AsyncMock(side_effect=AssertionError("fetchrow reached"))
        self.fetchval = AsyncMock(side_effect=AssertionError("fetchval reached"))


@pytest.mark.asyncio
async def test_dry_run_rejects_stale_target_before_any_mutation_or_audit() -> None:
    snapshot_a = item(status="decision_created")
    locked_b = authoritative_item_row(snapshot_a)
    locked_b["entity_id"] = "P-200"
    connection = NoDmlConnection()

    async def scoped(_pool, _user, work):
        return await work(connection, "tenant-A", "workspace-A")

    mutations = {
        name: AsyncMock(side_effect=AssertionError(f"{name} reached"))
        for name in (
            "_ensure_item_row",
            "_record_action_execution",
            "_record_action_run",
            "_set_execution_status",
            "_record_item_event",
        )
    }
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(return_value=snapshot_a),
        ),
        patch.object(
            control_room_service.auth, "pool", AsyncMock(return_value=object())
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
        patch.object(
            business_authoritative_execution,
            "lock_authoritative_business_item",
            AsyncMock(return_value=locked_b),
        ) as lock_item,
        patch.object(
            control_room_service,
            "require_enabled_action_template",
            AsyncMock(side_effect=AssertionError("template lookup reached")),
        ) as template_lookup,
        patch.object(
            control_room_service.audit_service,
            "record_event",
            AsyncMock(side_effect=AssertionError("audit reached")),
        ) as audit,
        patch.object(
            control_room_service, "_ensure_item_row", mutations["_ensure_item_row"]
        ),
        patch.object(
            control_room_service,
            "_record_action_execution",
            mutations["_record_action_execution"],
        ),
        patch.object(
            control_room_service,
            "_record_action_run",
            mutations["_record_action_run"],
        ),
        patch.object(
            control_room_service,
            "_set_execution_status",
            mutations["_set_execution_status"],
        ),
        patch.object(
            control_room_service,
            "_record_item_event",
            mutations["_record_item_event"],
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.action_dry_run(
                str(snapshot_a["id"]),
                USER,
                template_id="create_followup_task",
                binding_id=binding_id(snapshot_a),
            )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_business_state_changed"
    lock_item.assert_awaited_once()
    template_lookup.assert_not_awaited()
    audit.assert_not_awaited()
    for mutation in mutations.values():
        mutation.assert_not_awaited()
    for query in (
        connection.execute,
        connection.fetch,
        connection.fetchrow,
        connection.fetchval,
    ):
        query.assert_not_awaited()
