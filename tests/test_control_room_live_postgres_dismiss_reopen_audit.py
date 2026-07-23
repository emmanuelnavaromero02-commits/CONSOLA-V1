from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.services import audit_service, control_room_service
from app.services.control_room.business_item_persistence import persist_item_rows
from tests.test_control_room_live_postgres_workflows import _item, _rows, _scope
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


TRANSITIONS = {
    "dismiss": {
        "initial_status": "open",
        "final_status": "dismissed",
        "event": "dismissed",
        "audit": "control_room.dismiss",
    },
    "reopen": {
        "initial_status": "dismissed",
        "final_status": "open",
        "event": "reopened",
        "audit": "control_room.reopen",
    },
}


def _user(tenant_id: str, workspace_id: str) -> dict:
    return {
        "id": 7,
        "email": "workflow-owner@example.com",
        "role": "user",
        "workspace_role": "workspace_admin",
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
    }


async def _seed(
    dsn: str,
    item_id: str,
    *,
    initial_status: str,
) -> tuple[dict, dict]:
    conn = await asyncpg.connect(dsn)
    try:
        tenant_id, workspace_id = await _scope(conn)
        item = {
            **_item(item_id, tenant_id, workspace_id),
            "owner_user_id": 7,
        }
        await persist_item_rows(
            conn,
            _rows([item], tenant_id, workspace_id),
            owner_scope_id=7,
        )
        if initial_status == "dismissed":
            result = await conn.execute(
                """UPDATE control_room_items
                      SET status='dismissed', dismissed_at=NOW()
                    WHERE workspace_id=$1 AND item_id=$2""",
                workspace_id,
                item_id,
            )
            assert result == "UPDATE 1"
        return _user(tenant_id, workspace_id), item
    finally:
        await conn.close()


async def _state(dsn: str, user: dict, item_id: str) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        status = await conn.fetchval(
            """SELECT status FROM control_room_items
                WHERE workspace_id=$1 AND item_id=$2""",
            user["active_workspace_id"],
            item_id,
        )
        events = await conn.fetch(
            """SELECT event_type, COUNT(*) AS count
                 FROM control_room_item_events
                WHERE workspace_id=$1 AND item_id=$2
                  AND event_type=ANY($3::text[])
                GROUP BY event_type""",
            user["active_workspace_id"],
            item_id,
            ["dismissed", "reopened"],
        )
        audits = await conn.fetch(
            """SELECT action, COUNT(*) AS count
                 FROM audit_events
                WHERE resource_type='control_room_item'
                  AND resource_id=$1
                  AND action=ANY($2::text[])
                GROUP BY action""",
            item_id,
            ["control_room.dismiss", "control_room.reopen"],
        )
        return {
            "status": status,
            "events": {row["event_type"]: row["count"] for row in events},
            "audits": {row["action"]: row["count"] for row in audits},
        }
    finally:
        await conn.close()


async def _run(operation: str, *, pool, user: dict, item: dict) -> dict:
    method = getattr(control_room_service, f"{operation}_item")
    return await method(item["id"], user, reason="operator request")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", tuple(TRANSITIONS))
async def test_live_audit_failure_rolls_back_status_event_and_audit(
    operation: str,
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    transition = TRANSITIONS[operation]
    user, item = await _seed(
        postgres_with_real_init_schema,
        f"p1-{operation}-audit-rollback",
        initial_status=transition["initial_status"],
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)
    real_record_event = audit_service.record_event

    async def fail_after_audit_insert(**kwargs):
        await real_record_event(**kwargs)
        raise RuntimeError(f"{operation} audit failed")

    try:
        with (
            patch.object(
                control_room_service,
                "_item_for_mutation",
                AsyncMock(return_value=item),
            ),
            patch.object(
                control_room_service.auth,
                "pool",
                AsyncMock(return_value=pool),
            ),
            patch.object(
                control_room_service.audit_service,
                "record_event",
                fail_after_audit_insert,
            ),
        ):
            with pytest.raises(RuntimeError, match=f"{operation} audit failed"):
                await _run(operation, pool=pool, user=user, item=item)
    finally:
        await pool.close()

    assert await _state(postgres_with_real_init_schema, user, item["id"]) == {
        "status": transition["initial_status"],
        "events": {},
        "audits": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", tuple(TRANSITIONS))
async def test_live_success_is_invisible_until_transition_commit(
    operation: str,
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    transition = TRANSITIONS[operation]
    user, item = await _seed(
        postgres_with_real_init_schema,
        f"p1-{operation}-audit-visibility",
        initial_status=transition["initial_status"],
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)
    real_record_event = audit_service.record_event
    observations: list[tuple[str, int, int]] = []

    async def record_and_observe(**kwargs):
        await real_record_event(**kwargs)
        observer = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            row = await observer.fetchrow(
                """
                SELECT
                  (SELECT status FROM control_room_items
                    WHERE workspace_id=$1 AND item_id=$2) AS status,
                  (SELECT COUNT(*) FROM control_room_item_events
                    WHERE workspace_id=$1 AND item_id=$2
                      AND event_type=$3) AS events,
                  (SELECT COUNT(*) FROM audit_events
                    WHERE resource_type='control_room_item'
                      AND resource_id=$2 AND action=$4) AS audits
                """,
                user["active_workspace_id"],
                item["id"],
                transition["event"],
                transition["audit"],
            )
            observations.append(
                (str(row["status"]), int(row["events"]), int(row["audits"]))
            )
        finally:
            await observer.close()

    try:
        with (
            patch.object(
                control_room_service,
                "_item_for_mutation",
                AsyncMock(return_value=item),
            ),
            patch.object(
                control_room_service.auth,
                "pool",
                AsyncMock(return_value=pool),
            ),
            patch.object(
                control_room_service.audit_service,
                "record_event",
                record_and_observe,
            ),
        ):
            result = await _run(operation, pool=pool, user=user, item=item)
    finally:
        await pool.close()

    assert result[f"{operation}ed"] is True
    assert observations == [(transition["initial_status"], 0, 0)]
    assert await _state(postgres_with_real_init_schema, user, item["id"]) == {
        "status": transition["final_status"],
        "events": {transition["event"]: 1},
        "audits": {transition["audit"]: 1},
    }
