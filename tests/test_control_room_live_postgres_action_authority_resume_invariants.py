from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import auth
from app.services.control_room import (
    business_action_authority_repository,
    business_action_intents,
    business_action_transitions,
)
from app.services.control_room.business_action_intents import promote_action_handle
from app.services.control_room.business_action_transitions import (
    claim_intent_for_approval,
    reject_intent,
)
from app.services.db_scope import SET_SCOPE_SQL
from tests.control_room_action_authority_dry_run import insert_authority_dry_run
from tests.control_room_action_authority_flow import (
    issue_live_binding,
    promote_live_intent,
)
from tests.control_room_action_authority_http import experience_gets
from tests.control_room_action_authority_live import (
    AuthorityScope,
    AuthoritySeed,
    seed_authority,
    seed_authority_item,
    snapshot,
)
from tests.control_room_action_authority_resume import (
    clone_resume_intent,
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


async def _promote(scope: AuthorityScope, pool: asyncpg.Pool, handle: str):
    with (
        patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
        patch.object(
            business_action_intents,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(scope)),
        ),
    ):
        return await promote_action_handle(scope.maker, handle)


async def _reject(scope: AuthorityScope, pool: asyncpg.Pool, intent_id: str) -> None:
    with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
        claim = await claim_intent_for_approval(scope.checker, intent_id)
        assert claim.approval_handle
        assert (
            await reject_intent(scope.checker, claim.approval_handle)
        ).state == "rejected"


async def _force_rejected(seed: AuthoritySeed, scope: AuthorityScope, intent_id: str):
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        await conn.execute(
            """UPDATE control_room_action_intents
                  SET state='rejected', result_code='rejected',
                      checker_user_id=$2, state_version=state_version+1,
                      updated_at=NOW()
                WHERE id=$1::uuid""",
            intent_id,
            scope.checker["id"],
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_database_rejects_reusing_a_dry_run_for_another_intent(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "unique-dry-run")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
    finally:
        await pool.close()
    await _force_rejected(seed, scope, promoted.intent_id)
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await clone_resume_intent(
                conn,
                source_intent_id=promoted.intent_id,
                binding_digest=hashlib.sha256(b"reuse-dry-run").hexdigest(),
                state="stale",
                result_code="stale",
                checker_user_id=int(scope.checker["id"]),
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rls_writer_cannot_reopen_a_terminal_intent(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "terminal-immutable")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
    finally:
        await pool.close()
    await _force_rejected(seed, scope, promoted.intent_id)
    conn = await asyncpg.connect(seed.console_dsn)
    try:
        async with conn.transaction():
            await conn.execute(SET_SCOPE_SQL, scope.tenant_id, scope.workspace_id)
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(
                    """UPDATE control_room_action_intents
                          SET state='pending_approval', result_code='created',
                              state_version=state_version+1, updated_at=NOW()
                        WHERE id=$1::uuid""",
                    promoted.intent_id,
                )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_pending_reuse_handle_cannot_create_the_next_attempt(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "old-reuse-handle")
    pool = await _pool(seed)
    try:
        old = await promote_live_intent(seed, pool, scope)
        reuse = await issue_live_binding(seed, pool, scope)
        await _reject(scope, pool, old.intent_id)
        await insert_authority_dry_run(seed, scope)
        with pytest.raises(HTTPException) as denied:
            await _promote(scope, pool, reuse.action_handle)
        assert denied.value.status_code == 404
        conn = await asyncpg.connect(seed.admin_dsn)
        try:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM control_room_action_intents "
                    "WHERE workspace_id=$1::uuid AND item_id=$2",
                    scope.workspace_id,
                    scope.item_id,
                )
                == 1
            )
        finally:
            await conn.close()
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_experience_get_and_promotion_have_no_lock_cycle(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "get-promote-lock-order")
    await insert_authority_dry_run(seed, scope)
    pool = await _pool(seed)
    try:
        action = await issue_live_binding(seed, pool, scope)
        original_resolve = business_action_intents.resolve_action_binding_token
        original_policy = business_action_authority_repository.binding_issue_allowed

        async def delayed_resolve(*args, **kwargs):
            value = await original_resolve(*args, **kwargs)
            if kwargs.get("for_update"):
                await asyncio.sleep(0.2)
            return value

        async def delayed_policy(*args, **kwargs):
            await asyncio.sleep(0.2)
            return await original_policy(*args, **kwargs)

        with (
            patch.object(
                business_action_intents,
                "resolve_action_binding_token",
                new=delayed_resolve,
            ),
            patch.object(
                business_action_authority_repository,
                "binding_issue_allowed",
                new=delayed_policy,
            ),
        ):
            results = await asyncio.wait_for(
                asyncio.gather(
                    _promote(scope, pool, action.action_handle),
                    experience_gets(pool, scope, count=1, concurrent=True),
                    return_exceptions=True,
                ),
                timeout=10,
            )
        assert all(not isinstance(value, asyncpg.PostgresError) for value in results)
        assert sum(not isinstance(value, Exception) for value in results) == 2
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_pending_promotion_and_rejection_have_no_item_intent_lock_cycle(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "promote-reject-lock-order")
    pool = await _pool(seed)
    try:
        pending = await promote_live_intent(seed, pool, scope)
        reuse = await issue_live_binding(seed, pool, scope)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claim = await claim_intent_for_approval(scope.checker, pending.intent_id)
        assert claim.approval_handle
        original_item = business_action_intents.fetch_authoritative_row_for_update
        original_intent = business_action_transitions.lock_intent

        async def delayed_item(*args, **kwargs):
            value = await original_item(*args, **kwargs)
            await asyncio.sleep(0.2)
            return value

        async def delayed_intent(*args, **kwargs):
            value = await original_intent(*args, **kwargs)
            await asyncio.sleep(0.2)
            return value

        async def reject():
            with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
                return await reject_intent(scope.checker, claim.approval_handle or "")

        with (
            patch.object(
                business_action_intents,
                "fetch_authoritative_row_for_update",
                new=delayed_item,
            ),
            patch.object(
                business_action_transitions,
                "lock_intent",
                new=delayed_intent,
            ),
        ):
            results = await asyncio.wait_for(
                asyncio.gather(
                    _promote(scope, pool, reuse.action_handle),
                    reject(),
                    return_exceptions=True,
                ),
                timeout=10,
            )
        assert all(not isinstance(value, asyncpg.PostgresError) for value in results)
        assert any(not isinstance(value, Exception) for value in results)
    finally:
        await pool.close()
