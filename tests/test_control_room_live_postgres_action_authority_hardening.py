from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import auth
from app.services.control_room import business_action_intents
from app.services.control_room.business_action_intents import promote_action_handle
from app.services.db_scope import SET_SCOPE_SQL
from tests.control_room_action_authority_flow import promote_live_intent
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
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> AuthoritySeed:
    return asyncio.run(
        seed_authority(postgres_with_real_init_schema, omega_console_live_dsn)
    )


async def _pool(seed: AuthoritySeed) -> asyncpg.Pool:
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=4)


async def _expect_check_violation(
    seed: AuthoritySeed,
    *,
    tenant_id: str,
    workspace_id: str,
    sql: str,
    args: tuple[object, ...],
) -> None:
    conn = await asyncpg.connect(seed.console_dsn)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
                await conn.execute(sql, *args)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rls_writer_cannot_move_updated_at_before_created_at(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "time-order")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
    finally:
        await pool.close()
    await _expect_check_violation(
        seed,
        tenant_id=scope.tenant_id,
        workspace_id=scope.workspace_id,
        sql="UPDATE control_room_action_intents "
        "SET updated_at=created_at-INTERVAL '1 second' WHERE id=$1::uuid",
        args=(promoted.intent_id,),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    (
        "SET consumed_at=issued_at-INTERVAL '1 second', consumed_by=subject_user_id",
        "SET status='revoked', consumed_at=NULL, consumed_by=NULL",
        "SET operation_digest=repeat('a',64), result_state='pending_approval', "
        "result_version=1, consumed_at=NOW(), consumed_by=subject_user_id",
    ),
)
async def test_token_status_and_time_shape_is_durable(
    authority_seed: AuthoritySeed,
    mutation: str,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "token-shape")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
    finally:
        await pool.close()
    await _expect_check_violation(
        seed,
        tenant_id=scope.tenant_id,
        workspace_id=scope.workspace_id,
        sql=f"UPDATE control_room_action_tokens {mutation} "
        "WHERE intent_id=$1::uuid AND stage='workflow'",
        args=(promoted.intent_id,),
    )


@pytest.mark.asyncio
async def test_exclusion_violation_is_translated_only_to_safe_409(
    authority_seed: AuthoritySeed,
):
    scope = authority_seed.first
    with (
        patch.object(
            business_action_intents,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(scope)),
        ),
        patch.object(
            business_action_intents,
            "load_enabled_action_template_ids",
            new=AsyncMock(return_value=frozenset({"create_followup_task"})),
        ),
        patch.object(auth, "pool", new=AsyncMock(return_value=object())),
        patch.object(
            business_action_intents,
            "run_with_db_scope",
            new=AsyncMock(side_effect=asyncpg.ExclusionViolationError("raw 23P01")),
        ),
    ):
        with pytest.raises(HTTPException) as conflict:
            await promote_action_handle(scope.maker, "a" * 64)
    assert conflict.value.status_code == 409
    assert conflict.value.detail == "action intent already exists"
