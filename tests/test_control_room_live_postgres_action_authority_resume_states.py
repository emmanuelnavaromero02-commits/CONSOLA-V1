from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.services import auth
from app.services.control_room import (
    business_action_binding_producer,
    business_action_intents,
)
from app.services.control_room.business_action_binding_producer import (
    issue_action_bindings,
)
from app.services.control_room.business_action_intents import promote_action_handle
from tests.control_room_action_authority_dry_run import insert_authority_dry_run
from tests.control_room_action_authority_live import (
    AuthorityScope,
    AuthoritySeed,
    seed_authority,
    seed_authority_item,
    snapshot,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


@pytest.fixture(scope="module")
def authority_seed(
    postgres_with_real_init_schema: str, omega_console_live_dsn: str
) -> AuthoritySeed:
    return asyncio.run(
        seed_authority(postgres_with_real_init_schema, omega_console_live_dsn)
    )


async def _pool(seed: AuthoritySeed) -> asyncpg.Pool:
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=4)


async def _bindings(scope: AuthorityScope, pool: asyncpg.Pool) -> dict[str, tuple]:
    with patch.object(
        business_action_binding_producer.auth,
        "pool",
        new=AsyncMock(return_value=pool),
    ):
        return await issue_action_bindings(
            scope.maker,
            snapshot(scope),
            enabled_template_ids={"create_followup_task"},
        )


async def _issue_handle(scope: AuthorityScope, pool: asyncpg.Pool) -> str:
    issued = await _bindings(scope, pool)
    assert set(issued) == {scope.item_id}
    (action,) = issued[scope.item_id]
    return action.action_handle


async def _promote(scope: AuthorityScope, pool: asyncpg.Pool, action_handle: str):
    with (
        patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
        patch.object(
            business_action_intents,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(scope)),
        ),
    ):
        return await promote_action_handle(scope.maker, action_handle)


async def _first_attempt(
    seed: AuthoritySeed, scope: AuthorityScope, pool: asyncpg.Pool
):
    dry_run_id = await insert_authority_dry_run(seed, scope)
    action_handle = await _issue_handle(scope, pool)
    promoted = await _promote(scope, pool, action_handle)
    return dry_run_id, action_handle, promoted


async def _force_state(
    seed: AuthoritySeed,
    intent_id: str,
    state: str,
    checker_user_id: int,
    *,
    expired: bool = False,
) -> None:
    result_code = "created" if state == "pending_approval" else state
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        await conn.execute(
            """
            UPDATE control_room_action_intents
               SET state=$2, result_code=$3, state_version=state_version + 1,
                   checker_user_id=CASE
                     WHEN $2 IN ('approved','execution_reserved') THEN $4
                     ELSE checker_user_id END,
                   executor_user_id=CASE
                     WHEN $2='execution_reserved' THEN $4 ELSE executor_user_id END,
                   created_at=CASE WHEN $5 THEN NOW() - INTERVAL '25 hours'
                                   ELSE created_at END,
                   expires_at=CASE WHEN $5 THEN NOW() - INTERVAL '1 hour'
                                   ELSE expires_at END,
                   updated_at=NOW()
             WHERE id=$1::uuid
            """,
            intent_id,
            state,
            result_code,
            checker_user_id,
            expired,
        )
    finally:
        await conn.close()


async def _intent_history(seed: AuthoritySeed, intent_id: str) -> dict:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        intent = await conn.fetchrow(
            """SELECT current.id::text, current.correlation_id::text,
                      current.state, current.state_version,
                      current.dry_run_action_run_id,
                      current.dry_run_evidence_digest,
                      (SELECT count(*) FROM control_room_action_intents AS attempt
                        WHERE attempt.workspace_id=current.workspace_id
                          AND attempt.item_id=current.item_id
                          AND attempt.maker_user_id=current.maker_user_id
                      ) AS attempt_count
                   FROM control_room_action_intents AS current
                  WHERE current.id=$1::uuid""",
            intent_id,
        )
        events = await conn.fetch(
            """SELECT * FROM control_room_action_intent_events
                WHERE intent_id=$1::uuid ORDER BY id""",
            intent_id,
        )
        run = await conn.fetchrow(
            """SELECT id, action_intent_id::text, mode, status, dry_run_result,
                      metadata, completed_at, updated_at
                   FROM action_runs
                  WHERE action_intent_id=$1::uuid""",
            intent_id,
        )
        return {
            "intent": dict(intent or {}),
            "events": [dict(row) for row in events],
            "run": dict(run or {}),
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_state", "expired"),
    (("rejected", False), ("stale", False), ("pending_approval", True)),
)
async def test_retryable_state_requires_fresh_dry_run_and_creates_new_attempt(
    authority_seed: AuthoritySeed, terminal_state: str, expired: bool
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, f"resume-{terminal_state}")
    pool = await _pool(seed)
    try:
        old_dry_run, old_action, old = await _first_attempt(seed, scope, pool)
        await _force_state(
            seed,
            old.intent_id,
            terminal_state,
            scope.checker["id"],
            expired=expired,
        )
        assert await _bindings(scope, pool) == {}
        before = await _intent_history(seed, old.intent_id)

        new_dry_run = await insert_authority_dry_run(seed, scope)
        new_action = await _issue_handle(scope, pool)
        new = await _promote(scope, pool, new_action)
        after = await _intent_history(seed, old.intent_id)
        created = await _intent_history(seed, new.intent_id)

        assert new.intent_id != old.intent_id
        assert created["intent"]["correlation_id"] != before["intent"]["correlation_id"]
        assert new_dry_run != old_dry_run
        assert new_action != old_action
        assert new.workflow_handle != old.workflow_handle
        assert created["intent"]["dry_run_action_run_id"] == new_dry_run
        assert (
            created["events"][0]["operation_digest"]
            != before["events"][0]["operation_digest"]
        )
        assert before["events"] == after["events"]
        assert before["run"] == after["run"]
        before_intent, after_intent = dict(before["intent"]), dict(after["intent"])
        assert before_intent.pop("attempt_count") == 1
        assert after_intent.pop("attempt_count") == 2
        assert before_intent == after_intent
        assert created["intent"]["attempt_count"] == 2
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_state", ("completed", "failed"))
async def test_non_retryable_terminal_state_remains_permanently_blocked(
    authority_seed: AuthoritySeed, terminal_state: str
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, f"blocked-{terminal_state}")
    pool = await _pool(seed)
    try:
        _run, _handle, old = await _first_attempt(seed, scope, pool)
        await _force_state(seed, old.intent_id, terminal_state, scope.checker["id"])
        await insert_authority_dry_run(seed, scope)
        before = await _intent_history(seed, old.intent_id)
        assert await _bindings(scope, pool) == {}
        assert before["intent"]["attempt_count"] == 1
        assert await _intent_history(seed, old.intent_id) == before
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_live_pending_intent_is_reused_without_a_second_intent(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "pending-live")
    pool = await _pool(seed)
    try:
        dry_run_id, _action, old = await _first_attempt(seed, scope, pool)
        before = await _intent_history(seed, old.intent_id)
        resumed = await _promote(scope, pool, await _issue_handle(scope, pool))
        after = await _intent_history(seed, old.intent_id)
        assert resumed.intent_id == old.intent_id
        assert resumed.workflow_handle != old.workflow_handle
        assert after["intent"]["dry_run_action_run_id"] == dry_run_id
        assert after == before
        assert after["intent"]["attempt_count"] == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("live_state", ("approved", "execution_reserved"))
async def test_live_approved_or_reserved_intent_blocks_new_attempt(
    authority_seed: AuthoritySeed, live_state: str
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, f"live-{live_state}")
    pool = await _pool(seed)
    try:
        _run, _handle, old = await _first_attempt(seed, scope, pool)
        await _force_state(seed, old.intent_id, live_state, scope.checker["id"])
        await insert_authority_dry_run(seed, scope)
        assert await _bindings(scope, pool) == {}
        assert (await _intent_history(seed, old.intent_id))["intent"][
            "attempt_count"
        ] == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("expired_state", ("approved", "execution_reserved"))
async def test_expired_approved_or_reserved_is_explicitly_fail_closed(
    authority_seed: AuthoritySeed, expired_state: str
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, f"expired-{expired_state}")
    pool = await _pool(seed)
    try:
        _run, _handle, old = await _first_attempt(seed, scope, pool)
        await _force_state(
            seed,
            old.intent_id,
            expired_state,
            scope.checker["id"],
            expired=True,
        )
        await insert_authority_dry_run(seed, scope)
        before = await _intent_history(seed, old.intent_id)
        assert await _bindings(scope, pool) == {}
        assert before["intent"]["attempt_count"] == 1
        assert await _intent_history(seed, old.intent_id) == before
    finally:
        await pool.close()
