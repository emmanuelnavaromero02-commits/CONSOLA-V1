from __future__ import annotations

import asyncpg
import pytest

from app.services import audit_service
from app.services.control_room.business_item_approval_guard import (
    DENIED_AUDIT_ACTION,
)
from tests.test_control_room_live_postgres_implicit_approval import (
    _approve,
    _assert_not_approved,
    _seed_linked,
    _state,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


AUDIT_ACTIONS = ("control_room.approve", DENIED_AUDIT_ACTION)


async def _audit_counts(dsn: str, item_id: str) -> dict[str, int]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """SELECT action, COUNT(*) AS count
                 FROM audit_events
                WHERE resource_type='control_room_item'
                  AND resource_id=$1
                  AND action=ANY($2::text[])
                GROUP BY action""",
            item_id,
            list(AUDIT_ACTIONS),
        )
        return {row["action"]: row["count"] for row in rows}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_live_approval_audit_failure_rolls_back_every_effect(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    maker, checker, item = await _seed_linked(
        postgres_with_real_init_schema, "p12-audit-rollback"
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)

    async def fail_approval_audit(**kwargs):
        if kwargs["action"] == "control_room.approve":
            raise RuntimeError("failed approval audit")
        await audit_service.record_event(**kwargs)

    try:
        with pytest.raises(RuntimeError, match="failed approval audit"):
            await _approve(
                pool,
                user=checker,
                item=item,
                decision_id=item["decision_id"],
                audit_recorder=fail_approval_audit,
            )
    finally:
        await pool.close()

    _assert_not_approved(await _state(postgres_with_real_init_schema, maker, item["id"]))
    assert await _audit_counts(postgres_with_real_init_schema, item["id"]) == {}


@pytest.mark.asyncio
async def test_live_success_audit_is_invisible_until_workflow_commit(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    maker, checker, item = await _seed_linked(
        postgres_with_real_init_schema, "p12-audit-visibility"
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)
    observations: list[tuple[int, ...]] = []

    async def record_and_observe(**kwargs):
        await audit_service.record_event(**kwargs)
        observer = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            row = await observer.fetchrow(
                """
                SELECT
                  (SELECT COUNT(*) FROM control_room_item_events
                    WHERE workspace_id=$1 AND item_id=$2),
                  (SELECT COUNT(*) FROM control_room_lessons
                    WHERE workspace_id=$1 AND item_id=$2),
                  (SELECT COUNT(*) FROM audit_events
                    WHERE resource_id=$2 AND action=ANY($3::text[]))
                """,
                maker["active_workspace_id"],
                item["id"],
                list(AUDIT_ACTIONS),
            )
            observations.append(tuple(row.values()))
        finally:
            await observer.close()

    try:
        await _approve(
            pool,
            user=checker,
            item=item,
            decision_id=item["decision_id"],
            audit_recorder=record_and_observe,
        )
    finally:
        await pool.close()

    assert observations == [(0, 0, 0)]
    state = await _state(postgres_with_real_init_schema, maker, item["id"])
    assert state["status"] == "approved"
    assert (state["approved_actions"], state["lessons"]) == (1, 1)
    assert await _audit_counts(postgres_with_real_init_schema, item["id"]) == {
        "control_room.approve": 1,
    }


@pytest.mark.asyncio
async def test_live_denial_audit_failure_still_fails_closed(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    maker, _checker, item = await _seed_linked(
        postgres_with_real_init_schema, "p12-denial-audit-failure"
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)

    async def fail_denial_audit(**kwargs):
        if kwargs["action"] == DENIED_AUDIT_ACTION:
            raise RuntimeError("failed denial audit")
        await audit_service.record_event(**kwargs)

    try:
        with pytest.raises(RuntimeError, match="failed denial audit"):
            await _approve(
                pool,
                user=maker,
                item=item,
                decision_id=item["decision_id"],
                audit_recorder=fail_denial_audit,
            )
    finally:
        await pool.close()

    _assert_not_approved(await _state(postgres_with_real_init_schema, maker, item["id"]))
    assert await _audit_counts(postgres_with_real_init_schema, item["id"]) == {}
