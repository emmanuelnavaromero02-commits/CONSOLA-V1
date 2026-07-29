from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.services import auth
from app.services.control_room import (
    business_action_handle,
    business_action_intents,
)
from app.services.control_room.business_action_execution_authority import (
    issue_execution_handle,
)
from app.services.control_room.business_action_handle import (
    resolve_business_action_handle,
)
from app.services.control_room.business_action_tokens import issue_stage_token
from app.services.control_room.business_action_transitions import (
    approve_intent,
    claim_intent_for_approval,
)
from app.services.db_scope import run_with_db_scope
from tests.control_room_action_authority_dry_run import insert_authority_dry_run
from tests.control_room_action_authority_flow import (
    issue_live_binding,
    promote_live_intent,
)
from tests.control_room_action_authority_http import experience_gets
from tests.control_room_action_authority_live import (
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
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=8)


def _action_handle(payload: dict) -> str:
    return next(
        action["action_handle"]
        for section in payload["sections"]
        for fact in section["facts"]
        for action in fact["actions"]
    )


async def _token_counts(
    seed: AuthoritySeed, scope, *, stage: str, intent_id: str | None = None
):
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        return dict(
            await conn.fetchrow(
                """
                SELECT count(*) AS total,
                       count(*) FILTER (WHERE status='active') AS active
                  FROM control_room_action_tokens
                 WHERE workspace_id=$1::uuid AND stage=$2
                   AND ($3::text IS NULL OR item_id=$3)
                   AND ($4::uuid IS NULL OR intent_id=$4::uuid)
                """,
                scope.workspace_id,
                stage,
                scope.item_id if stage == "action_binding" else None,
                intent_id,
            )
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_repeated_and_concurrent_experience_gets_keep_one_active_binding(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    sequential = await seed_authority_item(seed, seed.first, "bounded-http")
    pool = await _pool(seed)
    try:
        handles = [
            _action_handle(payload)
            for payload in await experience_gets(
                pool, sequential, count=8, concurrent=False
            )
        ]
        assert len(set(handles)) == 8
        assert await _token_counts(seed, sequential, stage="action_binding") == {
            "total": 1,
            "active": 1,
        }

        racing = await seed_authority_item(seed, seed.first, "bounded-race")
        raced = [
            _action_handle(payload)
            for payload in await experience_gets(pool, racing, count=2, concurrent=True)
        ]
        assert len(set(raced)) == 2
        assert await _token_counts(seed, racing, stage="action_binding") == {
            "total": 1,
            "active": 1,
        }
        with (
            patch.object(
                business_action_handle.auth,
                "pool",
                new=AsyncMock(return_value=pool),
            ),
            patch.object(
                business_action_handle,
                "collect_surface_snapshot",
                new=AsyncMock(return_value=snapshot(racing)),
            ),
        ):
            results = await asyncio.gather(
                *(
                    resolve_business_action_handle(racing.maker, value)
                    for value in raced
                ),
                return_exceptions=True,
            )
        assert sum(not isinstance(value, Exception) for value in results) == 1
        assert all(not isinstance(value, asyncpg.PostgresError) for value in results)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_experience_stops_issuing_bindings_after_intent_exists(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "intent-suppresses-get")
    pool = await _pool(seed)
    try:
        await promote_live_intent(seed, pool, scope)
        payloads = await experience_gets(pool, scope, count=3, concurrent=True)
        assert all(
            not fact["actions"]
            for payload in payloads
            for section in payload["sections"]
            for fact in section["facts"]
        )
        assert await _token_counts(seed, scope, stage="action_binding") == {
            "total": 1,
            "active": 0,
        }
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_workflow_approval_and_execution_tokens_rotate_in_place(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "bounded-stages")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)

        async def issue_workflow(conn, _tenant, _workspace):
            return await issue_stage_token(
                conn,
                user=scope.maker,
                intent_id=promoted.intent_id,
                stage="workflow",
                intent_expires_at=promoted.expires_at,
            )

        await asyncio.gather(
            *(run_with_db_scope(pool, scope.maker, issue_workflow) for _ in range(5))
        )
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claims = [
                await claim_intent_for_approval(scope.checker, promoted.intent_id)
                for _ in range(5)
            ]
            await approve_intent(scope.checker, claims[-1].approval_handle or "")
            executions = [
                await issue_execution_handle(scope.checker, promoted.intent_id)
                for _ in range(5)
            ]
        for stage in ("workflow", "approval", "execution"):
            counts = await _token_counts(
                seed, scope, stage=stage, intent_id=promoted.intent_id
            )
            assert counts["active"] <= 1
            assert counts["total"] <= 2
        assert executions[-1].execution_handle
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent", (False, True))
async def test_two_distinct_binding_handles_promote_without_sql_error(
    authority_seed: AuthoritySeed, concurrent: bool
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "two-handles")
    await insert_authority_dry_run(seed, scope)
    pool = await _pool(seed)
    try:
        first = await issue_live_binding(seed, pool, scope)
        second = await issue_live_binding(seed, pool, scope)
        assert first.action_handle != second.action_handle
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch.object(
                business_action_intents,
                "collect_surface_snapshot",
                new=AsyncMock(return_value=snapshot(scope)),
            ),
        ):
            calls = (
                promote_live_handle(scope, first.action_handle),
                promote_live_handle(scope, second.action_handle),
            )
            if concurrent:
                results = await asyncio.gather(*calls, return_exceptions=True)
            else:
                results = []
                for call in calls:
                    try:
                        results.append(await call)
                    except Exception as exc:
                        results.append(exc)
        assert sum(not isinstance(value, Exception) for value in results) == 1
        assert all(not isinstance(value, asyncpg.PostgresError) for value in results)
        conn = await asyncpg.connect(seed.admin_dsn)
        try:
            counts = await conn.fetchrow(
                """
                SELECT count(*) AS intents,
                  (SELECT count(*) FROM control_room_action_intent_events e
                    JOIN control_room_action_intents i ON i.id=e.intent_id
                   WHERE i.workspace_id=$1::uuid AND i.item_id=$2
                     AND e.event_type='intent_created') AS events
                  FROM control_room_action_intents
                 WHERE workspace_id=$1::uuid AND item_id=$2
                """,
                scope.workspace_id,
                scope.item_id,
            )
        finally:
            await conn.close()
        assert dict(counts) == {"intents": 1, "events": 1}
    finally:
        await pool.close()


async def promote_live_handle(scope, handle: str):
    return await business_action_intents.promote_action_handle(scope.maker, handle)
