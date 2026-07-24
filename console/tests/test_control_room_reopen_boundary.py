from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
)


USER = {
    "id": 7,
    "email": "owner@example.com",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


def _dismissed_row(**updates) -> dict:
    return {
        "item_id": "business-1",
        "status": "dismissed",
        "decision_id": None,
        "selected_option_id": None,
        "execution_status": "not_started",
        "metadata": {},
        **updates,
    }


async def _scoped(pool, _user, work):
    return await work(pool, "tenant-a", "workspace-a")


class ReopenConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, sql: str, *_args):
        self.statements.append(sql)
        if "UPDATE control_room_items" in sql:
            return "UPDATE 1"
        if "INSERT INTO control_room_item_events" in sql:
            return "INSERT 0 1"
        raise AssertionError(sql)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "workflow",
    [
        {
            "decision_id": 42,
            "metadata": {DECISION_PROVENANCE_KEY: {"stage": "approved"}},
        },
        {
            "selected_option_id": "review",
            "metadata": {DECISION_PROVENANCE_KEY: {"stage": "option_selected"}},
        },
        {
            "execution_status": "dry_run_validated",
            "metadata": {DECISION_PROVENANCE_KEY: {"stage": "executed"}},
        },
        {
            "execution_status": "executed",
            "metadata": {DECISION_PROVENANCE_KEY: {"stage": "executed"}},
        },
        {"metadata": {DECISION_PROVENANCE_KEY: {"stage": "option_selected"}}},
    ],
    ids=("approved", "option", "dry-run", "executed", "provenance-only"),
)
async def test_linked_dismissed_workflow_rejects_reopen_before_dml(workflow) -> None:
    conn = ReopenConnection()
    audit = AsyncMock()
    locked = _dismissed_row(**workflow)
    guard = AsyncMock(return_value=locked)
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(
                return_value={
                    "id": "business-1",
                    "status": "dismissed",
                    "selected_option_id": "remediate",
                }
            ),
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=conn)),
        patch.object(control_room_service, "_run_with_db_scope", _scoped),
        patch.object(control_room_service, "lock_authoritative_business_item", guard),
        patch.object(control_room_service.audit_service, "record_event", audit),
    ):
        with pytest.raises(HTTPException) as error:
            await control_room_service.reopen_item("business-1", USER)

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "workflow_reopen_not_allowed"
    assert conn.statements == []
    audit.assert_not_awaited()
    guarded_item = guard.await_args.kwargs["item"]
    assert "selected_option_id" not in guarded_item
    assert "status" not in guarded_item


@pytest.mark.asyncio
async def test_clean_dismissed_item_reopens_with_guarded_update() -> None:
    conn = ReopenConnection()
    audit = AsyncMock()
    ensure = AsyncMock()
    quarantine = {"generations": [{"decision_id": 41, "fingerprint": "workflow-a"}]}
    locked = _dismissed_row(metadata={WORKFLOW_QUARANTINE_KEY: quarantine})
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(return_value={"id": "business-1", "status": "dismissed"}),
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=conn)),
        patch.object(control_room_service, "_run_with_db_scope", _scoped),
        patch.object(
            control_room_service,
            "lock_authoritative_business_item",
            AsyncMock(return_value=locked),
        ),
        patch.object(control_room_service, "_ensure_item_row", ensure),
        patch.object(control_room_service.audit_service, "record_event", audit),
    ):
        result = await control_room_service.reopen_item("business-1", USER)

    assert result["reopened"] is True
    assert result["item"]["status"] == "open"
    ensure.assert_not_awaited()
    assert len(conn.statements) == 2
    update = " ".join(conn.statements[0].split())
    assert "status = 'dismissed'" in update
    assert "decision_id IS NULL" in update
    assert "selected_option_id" in update
    assert "execution_status" in update
    assert DECISION_PROVENANCE_KEY in update
    assert WORKFLOW_QUARANTINE_KEY not in update
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["status"] == "success"
