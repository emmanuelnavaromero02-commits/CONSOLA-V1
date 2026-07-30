from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import auth
from app.services.control_room.business_action_transitions import (
    claim_intent_for_approval,
)
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
    postgres_with_real_init_schema: str, omega_console_live_dsn: str
) -> AuthoritySeed:
    return asyncio.run(
        seed_authority(postgres_with_real_init_schema, omega_console_live_dsn)
    )


async def _pool(seed: AuthoritySeed) -> asyncpg.Pool:
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=4)


@pytest.mark.asyncio
async def test_ledger_keeps_db_authorization_snapshot_after_role_revocation(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "ledger-auth-history")
    pool = await _pool(seed)
    admin = await asyncpg.connect(seed.admin_dsn)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            await claim_intent_for_approval(scope.checker, promoted.intent_id)
        before = await admin.fetchrow(
            """
            SELECT authorization_permission, actor_global_role,
                   actor_workspace_role, access_revision_digest,
                   rbac_policy_digest, authorization_snapshot::text AS snapshot,
                   authorization_digest
              FROM control_room_action_intent_events
             WHERE intent_id=$1::uuid AND event_type='approval_claimed'
            """,
            promoted.intent_id,
        )
        assert before["authorization_permission"] == "control_room.approve"
        assert before["actor_workspace_role"] == "control_room_approver"
        assert all(
            len(str(before[key])) == 64
            for key in (
                "access_revision_digest",
                "rbac_policy_digest",
                "authorization_digest",
            )
        )
        frozen = bytes(str(before["snapshot"]), "utf-8")
        await admin.execute(
            """
            DELETE FROM user_workspace_roles
             WHERE user_id=$1 AND workspace_id=$2::uuid
               AND role_id=(SELECT id FROM roles
                             WHERE name='control_room_approver')
            """,
            scope.checker["id"],
            scope.workspace_id,
        )
        after = await admin.fetchrow(
            """
            SELECT authorization_permission, actor_global_role,
                   actor_workspace_role, access_revision_digest,
                   rbac_policy_digest, authorization_snapshot::text AS snapshot,
                   authorization_digest
              FROM control_room_action_intent_events
             WHERE intent_id=$1::uuid AND event_type='approval_claimed'
            """,
            promoted.intent_id,
        )
        assert dict(after) == dict(before)
        assert bytes(str(after["snapshot"]), "utf-8") == frozen
    finally:
        await admin.execute(
            """
            INSERT INTO user_workspace_roles(user_id, workspace_id, role_id)
            SELECT $1, $2::uuid, id FROM roles
             WHERE name='control_room_approver'
            ON CONFLICT DO NOTHING
            """,
            scope.checker["id"],
            scope.workspace_id,
        )
        await admin.close()
        await pool.close()


@pytest.mark.asyncio
async def test_forged_session_role_cannot_create_authorized_ledger_event(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "ledger-forged-role")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        forged = {
            **seed.outsider,
            "workspace_role": "control_room_approver",
        }
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            with pytest.raises(HTTPException) as blocked:
                await claim_intent_for_approval(forged, promoted.intent_id)
        assert blocked.value.status_code == 403
        admin = await asyncpg.connect(seed.admin_dsn)
        try:
            count = await admin.fetchval(
                """
                SELECT count(*) FROM control_room_action_intent_events
                 WHERE intent_id=$1::uuid AND actor_user_id=$2
                """,
                promoted.intent_id,
                forged["id"],
            )
        finally:
            await admin.close()
        assert count == 0
    finally:
        await pool.close()
