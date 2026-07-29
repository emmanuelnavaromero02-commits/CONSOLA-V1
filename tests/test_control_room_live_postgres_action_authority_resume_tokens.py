from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import asyncpg
import pytest
from app.services import auth
from app.services.control_room import business_action_intents
from app.services.control_room.business_action_intents import promote_action_handle
from app.services.control_room.business_action_tokens import handle_digest
from app.services.control_room.business_action_transitions import (
    claim_intent_for_approval,
    reject_intent,
)
from fastapi import HTTPException

from tests.control_room_action_authority_dry_run import insert_authority_dry_run
from tests.control_room_action_authority_flow import issue_live_binding
from tests.control_room_action_authority_http import experience_gets
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
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=8)


@dataclass(frozen=True)
class PreviousAttempt:
    action_handle: str
    workflow_handle: str
    approval_handle: str
    intent_id: str


def _action_handle(payload: dict) -> str:
    actions = [
        action
        for section in payload["sections"]
        for fact in section["facts"]
        for action in fact["actions"]
    ]
    assert len(actions) == 1
    return str(actions[0]["action_handle"])


async def _prepare_resume(
    seed: AuthoritySeed, pool: asyncpg.Pool, marker: str
) -> tuple[AuthorityScope, PreviousAttempt]:
    scope = await seed_authority_item(seed, seed.first, marker)
    await insert_authority_dry_run(seed, scope)
    action = await issue_live_binding(seed, pool, scope)
    with (
        patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
        patch.object(
            business_action_intents,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(scope)),
        ),
    ):
        promoted = await promote_action_handle(scope.maker, action.action_handle)
        claim = await claim_intent_for_approval(scope.checker, promoted.intent_id)
        assert claim.approval_handle
        rejected = await reject_intent(scope.checker, claim.approval_handle)
    assert rejected.state == "rejected"
    await insert_authority_dry_run(seed, scope)
    return scope, PreviousAttempt(
        action.action_handle,
        promoted.workflow_handle,
        claim.approval_handle,
        promoted.intent_id,
    )


async def _binding_counts(seed: AuthoritySeed, scope: AuthorityScope) -> dict:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status='active') AS active
              FROM control_room_action_tokens
             WHERE tenant_id=$1::uuid AND workspace_id=$2::uuid
               AND subject_user_id=$3 AND item_id=$4
               AND stage='action_binding'
            """,
            scope.tenant_id,
            scope.workspace_id,
            scope.maker["id"],
            scope.item_id,
        )
        return dict(row)
    finally:
        await conn.close()


async def _promote_race(pool, scope: AuthorityScope, handles: list[str]):
    with (
        patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
        patch.object(
            business_action_intents,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(scope)),
        ),
    ):
        return await asyncio.gather(
            *(promote_action_handle(scope.maker, value) for value in handles),
            return_exceptions=True,
        )


async def _workspace_writer(seed: AuthoritySeed, scope: AuthorityScope) -> dict:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        email = f"authority-resume-writer-{uuid4().hex}@example.test"
        user_id = int(
            await conn.fetchval(
                """INSERT INTO users(email, password_hash, role, tenant_id, is_active)
                   VALUES ($1, 'x', 'user', $2::uuid, TRUE) RETURNING id""",
                email,
                scope.tenant_id,
            )
        )
        await conn.execute(
            """INSERT INTO user_workspace_roles(user_id, workspace_id, role_id)
               SELECT $1, $2::uuid, id FROM roles WHERE name='workspace_admin'""",
            user_id,
            scope.workspace_id,
        )
    finally:
        await conn.close()
    return {
        **scope.maker,
        "id": user_id,
        "email": email,
        "workspace_role": "workspace_admin",
    }


@pytest.mark.asyncio
async def test_resume_concurrent_gets_keep_one_active_binding(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    pool = await _pool(seed)
    try:
        scope, _previous = await _prepare_resume(seed, pool, "resume-get")
        payloads = await experience_gets(pool, scope, count=2, concurrent=True)
        handles = [_action_handle(payload) for payload in payloads]
        assert len(set(handles)) == 2
        counts = await _binding_counts(seed, scope)
        assert counts["active"] == 1
        assert counts["total"] <= 2
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_resume_promotions_create_one_new_intent_without_postgres_error(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    pool = await _pool(seed)
    try:
        scope, previous = await _prepare_resume(seed, pool, "resume-promote")
        payloads = await experience_gets(pool, scope, count=2, concurrent=True)
        handles = [_action_handle(payload) for payload in payloads]
        results = await _promote_race(pool, scope, handles)
        successes = [value for value in results if not isinstance(value, Exception)]
        assert len(successes) == 1
        assert all(not isinstance(value, asyncpg.PostgresError) for value in results)
        current = successes[0]
        assert current.intent_id != previous.intent_id
        assert current.workflow_handle != previous.workflow_handle

        conn = await asyncpg.connect(seed.admin_dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT i.id::text AS intent_id, e.operation_digest
                  FROM control_room_action_intents AS i
                  JOIN control_room_action_intent_events AS e
                    ON e.intent_id=i.id AND e.event_type='intent_created'
                 WHERE i.workspace_id=$1::uuid AND i.item_id=$2
                 ORDER BY i.created_at, i.id
                """,
                scope.workspace_id,
                scope.item_id,
            )
        finally:
            await conn.close()
        assert len(rows) == 2
        assert {row["intent_id"] for row in rows} == {
            previous.intent_id,
            current.intent_id,
        }
        assert len({row["operation_digest"] for row in rows}) == 2
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_resume_old_replays_and_cross_scope_cannot_affect_new_intent(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    pool = await _pool(seed)
    try:
        scope, previous = await _prepare_resume(seed, pool, "resume-isolation")
        handles = [
            _action_handle(payload)
            for payload in await experience_gets(pool, scope, count=2, concurrent=True)
        ]
        other_writer = await _workspace_writer(seed, scope)
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch.object(
                business_action_intents,
                "collect_surface_snapshot",
                new=AsyncMock(return_value=snapshot(scope)),
            ),
        ):
            for user in (other_writer, seed.second.maker):
                for value in handles:
                    with pytest.raises(HTTPException) as denied:
                        await promote_action_handle(user, value)
                    assert denied.value.status_code == 404

        results = await _promote_race(pool, scope, handles)
        (current,) = [value for value in results if not isinstance(value, Exception)]
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch.object(
                business_action_intents,
                "collect_surface_snapshot",
                new=AsyncMock(return_value=snapshot(scope)),
            ),
        ):
            replay = await reject_intent(scope.checker, previous.approval_handle)
            with pytest.raises(HTTPException) as old_action:
                await promote_action_handle(scope.maker, previous.action_handle)
        assert replay.intent_id == previous.intent_id
        assert replay.state == "rejected" and replay.replay is True
        assert old_action.value.status_code == 404

        conn = await asyncpg.connect(seed.admin_dsn)
        try:
            state = await conn.fetchrow(
                "SELECT state, state_version FROM control_room_action_intents "
                "WHERE id=$1::uuid",
                current.intent_id,
            )
            token_intents = await conn.fetch(
                """SELECT intent_id::text FROM control_room_action_tokens
                     WHERE token_digest=ANY($1::bytea[]) AND stage='workflow'""",
                [
                    handle_digest(previous.workflow_handle),
                    handle_digest(current.workflow_handle),
                ],
            )
        finally:
            await conn.close()
        assert dict(state) == {"state": "pending_approval", "state_version": 1}
        assert {row["intent_id"] for row in token_intents} == {
            previous.intent_id,
            current.intent_id,
        }
    finally:
        await pool.close()
