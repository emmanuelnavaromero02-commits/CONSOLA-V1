from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.services import control_room_service as service


USER = {
    "id": 7,
    "email": "owner@example.com",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}
ITEM = {"id": "item-a", "owner_user_id": 7}


class StatusConnection:
    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.calls.append((sql, args))
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested_status",
    ("preview_generated", "dry_run_validated", "dry_run_failed", "blocked"),
)
async def test_terminal_execution_status_rejects_nonterminal_overwrite(
    requested_status: str,
) -> None:
    conn = StatusConnection("UPDATE 0")

    with pytest.raises(HTTPException) as exc:
        await service._set_execution_status(
            conn,
            user=USER,
            item=ITEM,
            execution_status=requested_status,
            critical=True,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "code": "execution_status_conflict",
        "message": "control room execution status is already terminal",
    }
    sql, args = conn.calls[0]
    assert "COALESCE(execution_status, 'not_started')" in sql
    assert "<> ALL" in sql
    assert {"executed", "resolved", "terminal"}.issubset(set(args[5]))


@pytest.mark.asyncio
async def test_execution_status_update_writes_status_and_metadata() -> None:
    conn = StatusConnection("UPDATE 1")

    await service._set_execution_status(
        conn,
        user=USER,
        item=ITEM,
        execution_status="executed",
        critical=True,
    )

    sql, args = conn.calls[0]
    assert args[0] == "executed"
    assert json.loads(str(args[3])) == {"execution_status": "executed"}
    assert "{decision_eligibility_provenance}" in sql
    assert '"stage":"executed"' in sql
    assert '"reason":"explicit_execution"' in sql
