from __future__ import annotations

import asyncio

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import audit_service
from tests.test_control_room_live_postgres_implicit_approval import (
    _approve,
    _seed,
    _state,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


AUDIT_ACTIONS = (
    "control_room.decision.create",
    "control_room.approve",
)


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


def _assert_zero_workflow(state: dict) -> None:
    assert state == {
        "status": "open",
        "decision_id": None,
        "decisions": 0,
        "created_actions": 0,
        "approved_actions": 0,
        "events": {},
        "lessons": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_action", AUDIT_ACTIONS)
async def test_live_each_audit_failure_rolls_back_every_effect(
    failed_action: str,
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    suffix = failed_action.rsplit(".", 1)[-1]
    user, item = await _seed(
        postgres_with_real_init_schema,
        f"p12-audit-rollback-{suffix}",
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)

    async def fail_selected_audit(**kwargs):
        if kwargs["action"] == failed_action:
            raise RuntimeError(f"failed {failed_action}")
        await audit_service.record_event(**kwargs)

    try:
        with pytest.raises(RuntimeError, match=f"failed {failed_action}"):
            await _approve(
                pool,
                user=user,
                item=item,
                audit_recorder=fail_selected_audit,
            )
    finally:
        await pool.close()

    _assert_zero_workflow(
        await _state(postgres_with_real_init_schema, user, item["id"])
    )
    assert await _audit_counts(postgres_with_real_init_schema, item["id"]) == {}


@pytest.mark.asyncio
async def test_live_success_audits_are_invisible_until_workflow_commit(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    user, item = await _seed(postgres_with_real_init_schema, "p12-audit-visibility")
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)
    observations: list[tuple[int, ...]] = []

    async def record_and_observe(**kwargs):
        await audit_service.record_event(**kwargs)
        observer = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            row = await observer.fetchrow(
                """
                SELECT
                  (SELECT COUNT(*) FROM decisions WHERE workspace_id=$1),
                  (SELECT COUNT(*) FROM control_room_item_events
                    WHERE workspace_id=$1 AND item_id=$2),
                  (SELECT COUNT(*) FROM control_room_lessons
                    WHERE workspace_id=$1 AND item_id=$2),
                  (SELECT COUNT(*) FROM audit_events
                    WHERE resource_id=$2 AND action=ANY($3::text[]))
                """,
                user["active_workspace_id"],
                item["id"],
                list(AUDIT_ACTIONS),
            )
            observations.append(tuple(row.values()))
        finally:
            await observer.close()

    try:
        await _approve(
            pool,
            user=user,
            item=item,
            audit_recorder=record_and_observe,
        )
    finally:
        await pool.close()

    assert observations == [(0, 0, 0, 0), (0, 0, 0, 0)]
    state = await _state(postgres_with_real_init_schema, user, item["id"])
    assert state["status"] == "approved"
    assert (
        state["decisions"],
        state["created_actions"],
        state["approved_actions"],
    ) == (
        1,
        1,
        1,
    )
    assert await _audit_counts(postgres_with_real_init_schema, item["id"]) == {
        "control_room.decision.create": 1,
        "control_room.approve": 1,
    }


@pytest.mark.asyncio
async def test_live_concurrent_implicit_approvals_keep_one_audit_pair(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    user, item = await _seed(postgres_with_real_init_schema, "p12-audit-concurrent")
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=2, max_size=4)
    barrier = asyncio.Barrier(2)

    async def run():
        await barrier.wait()
        try:
            return await _approve(pool, user=user, item=item)
        except HTTPException as exc:
            return exc

    try:
        results = await asyncio.gather(run(), run())
    finally:
        await pool.close()

    assert sorted(
        result.status_code if isinstance(result, HTTPException) else 200
        for result in results
    ) == [200, 409]
    state = await _state(postgres_with_real_init_schema, user, item["id"])
    assert (
        state["decisions"],
        state["created_actions"],
        state["approved_actions"],
    ) == (
        1,
        1,
        1,
    )
    assert await _audit_counts(postgres_with_real_init_schema, item["id"]) == {
        "control_room.decision.create": 1,
        "control_room.approve": 1,
    }
