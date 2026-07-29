from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import auth
from app.services.control_room.business_action_execution_authority import (
    reserve_execution,
)
from app.services.control_room.business_action_tokens import handle_digest
from app.services.control_room.business_action_transition_core import (
    lock_intent,
    transition_intent,
)
from app.services.control_room.business_action_transitions import (
    approve_intent,
    claim_intent_for_approval,
)
from app.services.db_scope import SET_SCOPE_SQL
from tests.control_room_action_authority_flow import promote_live_intent
from tests.control_room_action_authority_live import (
    AuthoritySeed,
    seed_authority,
    seed_authority_item,
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
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)


@pytest.mark.asyncio
async def test_changed_authoritative_item_transitions_to_stale_without_handle(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "stale")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        admin = await asyncpg.connect(seed.admin_dsn)
        try:
            await admin.execute(
                """UPDATE control_room_items SET title=title || ' changed'
                    WHERE workspace_id=$1::uuid AND item_id=$2""",
                scope.workspace_id,
                scope.item_id,
            )
        finally:
            await admin.close()
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claim = await claim_intent_for_approval(
                scope.checker,
                promoted.intent_id,
            )
        assert claim.state == "stale"
        assert claim.approval_handle is None
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_tokens_reject_wrong_stage_actor_workspace_and_invalid_shapes(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "tokens")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claim = await claim_intent_for_approval(
                scope.checker,
                promoted.intent_id,
            )
            handle = claim.approval_handle or ""
            for call in (
                reserve_execution(scope.checker, handle),
                approve_intent(seed.second.checker, handle),
                approve_intent(scope.checker, "0" * 63),
                approve_intent(scope.checker, "f" * 64),
            ):
                with pytest.raises(HTTPException) as rejected:
                    await call
                assert rejected.value.status_code == 404
                assert handle not in str(rejected.value.detail)

            no_permission = {**seed.outsider, "workspace_role": "viewer"}
            with pytest.raises(HTTPException) as forbidden:
                await approve_intent(no_permission, handle)
            assert forbidden.value.status_code == 403

        admin = await asyncpg.connect(seed.admin_dsn)
        try:
            await admin.execute(
                """UPDATE control_room_action_tokens
                      SET issued_at=NOW() - INTERVAL '2 hours',
                          expires_at=NOW() - INTERVAL '1 hour'
                    WHERE token_digest=$1 AND stage='approval'""",
                handle_digest(handle),
            )
        finally:
            await admin.close()
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            with pytest.raises(HTTPException) as expired:
                await approve_intent(scope.checker, handle)
        assert expired.value.status_code == 404
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_revoked_checker_permission_blocks_without_transition(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "revoked")
    pool = await _pool(seed)
    admin = await asyncpg.connect(seed.admin_dsn)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claim = await claim_intent_for_approval(
                scope.checker,
                promoted.intent_id,
            )
        await admin.execute(
            """DELETE FROM user_workspace_roles
                WHERE user_id=$1 AND workspace_id=$2::uuid
                  AND role_id=(SELECT id FROM roles
                                WHERE name='control_room_approver')""",
            scope.checker["id"],
            scope.workspace_id,
        )
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            with pytest.raises(HTTPException) as revoked:
                await approve_intent(scope.checker, claim.approval_handle or "")
        assert revoked.value.status_code == 403
        state = await admin.fetchrow(
            "SELECT state, state_version FROM control_room_action_intents "
            "WHERE id=$1::uuid",
            promoted.intent_id,
        )
        assert dict(state) == {"state": "pending_approval", "state_version": 2}
    finally:
        await admin.execute(
            """INSERT INTO user_workspace_roles(user_id, workspace_id, role_id)
               SELECT $1, $2::uuid, id FROM roles
                WHERE name='control_room_approver'
               ON CONFLICT DO NOTHING""",
            scope.checker["id"],
            scope.workspace_id,
        )
        await admin.close()
        await pool.close()


@pytest.mark.asyncio
async def test_ledger_failure_rolls_back_state_and_ledger_is_immutable(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "ledger")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            await claim_intent_for_approval(scope.checker, promoted.intent_id)
        conn = await asyncpg.connect(seed.console_dsn)
        try:
            with pytest.raises(asyncpg.UniqueViolationError):
                async with conn.transaction():
                    await conn.execute(
                        SET_SCOPE_SQL,
                        scope.tenant_id,
                        scope.workspace_id,
                    )
                    intent = await lock_intent(
                        conn,
                        tenant_id=scope.tenant_id,
                        workspace_id=scope.workspace_id,
                        intent_id=promoted.intent_id,
                    )
                    duplicate = await conn.fetchval(
                        """SELECT operation_digest
                              FROM control_room_action_intent_events
                             WHERE intent_id=$1::uuid AND intent_version=2""",
                        promoted.intent_id,
                    )
                    await transition_intent(
                        conn,
                        intent=intent,
                        actor_user_id=scope.checker["id"],
                        event_type="approved",
                        operation_digest=duplicate,
                    )
            async with conn.transaction():
                await conn.execute(
                    SET_SCOPE_SQL,
                    scope.tenant_id,
                    scope.workspace_id,
                )
                state = await conn.fetchrow(
                    "SELECT state, state_version FROM control_room_action_intents "
                    "WHERE id=$1::uuid",
                    promoted.intent_id,
                )
                assert dict(state) == {
                    "state": "pending_approval",
                    "state_version": 2,
                }
                with pytest.raises(asyncpg.PostgresError):
                    await conn.execute(
                        "DELETE FROM control_room_action_intent_events "
                        "WHERE intent_id=$1::uuid",
                        promoted.intent_id,
                    )
        finally:
            await conn.close()
    finally:
        await pool.close()
