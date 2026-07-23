from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import audit_service, control_room_service
from tests.test_control_room_live_postgres_dismiss_reopen_audit import _seed
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


async def _set_status(dsn: str, workspace_id: str, item_id: str, status: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        result = await conn.execute(
            """UPDATE control_room_items
                  SET status=$3
                WHERE workspace_id=$1 AND item_id=$2""",
            workspace_id,
            item_id,
            status,
        )
        assert result == "UPDATE 1"
    finally:
        await conn.close()


async def _state(dsn: str, workspace_id: str, item_id: str) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            """SELECT status, metadata -> 'alert_state' AS alert_state,
                      (SELECT COUNT(*) FROM control_room_item_events
                        WHERE workspace_id=$1 AND item_id=$2
                          AND event_type='alert_false_positive') AS events,
                      (SELECT COUNT(*) FROM audit_events
                        WHERE resource_type='control_room_alert'
                          AND resource_id=$2
                          AND action='control_room.alert.false_positive') AS audits
                 FROM control_room_items
                WHERE workspace_id=$1 AND item_id=$2""",
            workspace_id,
            item_id,
        )
        state = dict(row)
        if isinstance(state["alert_state"], str):
            state["alert_state"] = json.loads(state["alert_state"])
        return state
    finally:
        await conn.close()


async def _run(
    dsn: str,
    *,
    user: dict,
    item: dict,
    audit_recorder=None,
) -> dict:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        patches = [
            patch.object(
                control_room_service,
                "_item_for_mutation",
                AsyncMock(return_value={**item, "status": "open"}),
            ),
            patch.object(
                control_room_service.auth,
                "pool",
                AsyncMock(return_value=pool),
            ),
        ]
        if audit_recorder is not None:
            patches.append(
                patch.object(
                    control_room_service.audit_service,
                    "record_event",
                    audit_recorder,
                )
            )
        with patches[0], patches[1]:
            if len(patches) == 3:
                with patches[2]:
                    return await control_room_service.mark_alert_false_positive(
                        item["id"], user, body={"reason": "duplicate"}
                    )
            return await control_room_service.mark_alert_false_positive(
                item["id"], user, body={"reason": "duplicate"}
            )
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ("approved", "dismissed", "resolved"))
async def test_live_false_positive_cannot_degrade_terminal_status(
    status: str,
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    user, item = await _seed(
        postgres_with_real_init_schema,
        f"false-positive-terminal-{status}",
        initial_status="open",
    )
    workspace_id = user["active_workspace_id"]
    await _set_status(postgres_with_real_init_schema, workspace_id, item["id"], status)

    with pytest.raises(HTTPException) as exc:
        await _run(omega_console_live_dsn, user=user, item=item)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "terminal_item"
    state = await _state(postgres_with_real_init_schema, workspace_id, item["id"])
    assert state == {
        "status": status,
        "alert_state": None,
        "events": 0,
        "audits": 0,
    }


@pytest.mark.asyncio
async def test_live_false_positive_audit_failure_rolls_back_everything(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    user, item = await _seed(
        postgres_with_real_init_schema,
        "false-positive-audit-rollback",
        initial_status="open",
    )
    real_record_event = audit_service.record_event

    async def fail_after_insert(**kwargs):
        await real_record_event(**kwargs)
        raise RuntimeError("false-positive audit failed")

    with pytest.raises(RuntimeError, match="false-positive audit failed"):
        await _run(
            omega_console_live_dsn,
            user=user,
            item=item,
            audit_recorder=fail_after_insert,
        )

    state = await _state(
        postgres_with_real_init_schema,
        user["active_workspace_id"],
        item["id"],
    )
    assert state == {
        "status": "open",
        "alert_state": None,
        "events": 0,
        "audits": 0,
    }


@pytest.mark.asyncio
async def test_live_false_positive_commits_one_event_and_audit(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    user, item = await _seed(
        postgres_with_real_init_schema,
        "false-positive-success",
        initial_status="open",
    )

    result = await _run(omega_console_live_dsn, user=user, item=item)

    assert result["item"]["status"] == "dismissed"
    state = await _state(
        postgres_with_real_init_schema,
        user["active_workspace_id"],
        item["id"],
    )
    assert state["status"] == "dismissed"
    assert state["alert_state"]["state"] == "false_positive"
    assert state["events"] == state["audits"] == 1
