from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import auth
from app.services.control_room import (
    business_action_binding_producer,
    business_action_handle,
)
from app.services.control_room.business_action_execution_authority import (
    issue_execution_handle,
    reserve_execution,
)
from app.services.control_room.business_action_handle import (
    resolve_business_action_handle,
)
from app.services.control_room.business_action_transitions import (
    approve_intent,
    claim_intent_for_approval,
    reject_intent,
)
from app.services.db_scope import SET_SCOPE_SQL
from tests.control_room_action_authority_flow import (
    issue_live_binding,
    promote_live_intent,
)
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
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)


@pytest.mark.asyncio
async def test_real_producer_is_authoritative_user_bound_and_digest_only(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    pool = await _pool(seed)
    try:
        action = await issue_live_binding(seed, pool)
        assert len(action.action_handle) == 64
        with (
            patch.object(
                business_action_handle.auth,
                "pool",
                new=AsyncMock(return_value=pool),
            ),
            patch.object(
                business_action_handle,
                "collect_surface_snapshot",
                new=AsyncMock(return_value=snapshot(seed.first)),
            ),
        ):
            resolved = await resolve_business_action_handle(
                seed.first.maker,
                action.action_handle,
            )
        assert resolved.item_id == seed.first.item_id
        assert resolved.template_id == "create_followup_task"
        missing = snapshot(seed.first)
        missing = type(missing)(
            missing.generated_at,
            missing.scope,
            ({**seed.first.item, "id": "not-persisted"},),
            (),
            (),
            (),
        )
        with patch.object(
            business_action_binding_producer.auth,
            "pool",
            new=AsyncMock(return_value=pool),
        ):
            assert not await business_action_binding_producer.issue_action_bindings(
                seed.first.maker,
                missing,
                enabled_template_ids={"create_followup_task"},
            )
            with pytest.raises(HTTPException) as cross_user:
                await resolve_business_action_handle(
                    {**seed.first.checker, "allowed_cartridges": ["sap_hcm"]},
                    action.action_handle,
                )
        assert cross_user.value.status_code == 404
        admin = await asyncpg.connect(seed.admin_dsn)
        try:
            row = await admin.fetchrow(
                """SELECT encode(token_digest, 'hex') AS digest, stage,
                          subject_user_id, item_id, template_id
                     FROM control_room_action_tokens
                    WHERE item_id=$1 ORDER BY issued_at DESC LIMIT 1""",
                seed.first.item_id,
            )
        finally:
            await admin.close()
        assert row["digest"] != action.action_handle
        assert dict(row) | {} == {
            "digest": row["digest"],
            "stage": "action_binding",
            "subject_user_id": seed.first.maker["id"],
            "item_id": seed.first.item_id,
            "template_id": "create_followup_task",
        }
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_real_intent_binds_maker_scope_and_consumes_action_once(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    pool = await _pool(seed)
    try:
        scope = await seed_authority_item(seed, seed.first, "intent")
        promoted = await promote_live_intent(seed, pool, scope)
        assert "workflow_handle" not in repr(promoted)
        admin = await asyncpg.connect(seed.admin_dsn)
        try:
            row = await admin.fetchrow(
                "SELECT * FROM control_room_action_intents WHERE id=$1::uuid",
                promoted.intent_id,
            )
            tokens = await admin.fetch(
                "SELECT stage, status, octet_length(token_digest) AS size "
                "FROM control_room_action_tokens WHERE intent_id=$1::uuid",
                promoted.intent_id,
            )
        finally:
            await admin.close()
        assert str(row["workspace_id"]) == seed.first.workspace_id
        assert row["maker_user_id"] == seed.first.maker["id"]
        assert row["state"] == "pending_approval"
        assert [(r["stage"], r["status"], r["size"]) for r in tokens] == [
            ("workflow", "active", 32)
        ]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_real_maker_checker_approval_and_execution_reservation_replay(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    pool = await _pool(seed)
    try:
        scope = await seed_authority_item(seed, seed.first, "approval")
        promoted = await promote_live_intent(seed, pool, scope)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            with pytest.raises(HTTPException):
                await claim_intent_for_approval(seed.first.maker, promoted.intent_id)
            claim = await claim_intent_for_approval(
                seed.first.checker, promoted.intent_id
            )
            assert claim.approval_handle and "approval_handle" not in repr(claim)
            approved = await approve_intent(seed.first.checker, claim.approval_handle)
            repeated = await approve_intent(seed.first.checker, claim.approval_handle)
            execution = await issue_execution_handle(
                seed.first.checker, promoted.intent_id
            )
            reserved = await reserve_execution(
                seed.first.checker, execution.execution_handle or ""
            )
            replay = await reserve_execution(
                seed.first.checker, execution.execution_handle or ""
            )
        assert approved.state == repeated.state == "approved"
        assert repeated.replay is True
        assert reserved.state == replay.state == "execution_reserved"
        assert replay.replay is True
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_real_approve_reject_race_has_one_transition(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    pool = await _pool(seed)
    try:
        scope = await seed_authority_item(seed, seed.second, "race")
        promoted = await promote_live_intent(seed, pool, scope)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claim = await claim_intent_for_approval(
                seed.second.checker, promoted.intent_id
            )
            results = await asyncio.gather(
                approve_intent(seed.second.checker, claim.approval_handle or ""),
                reject_intent(seed.second.checker, claim.approval_handle or ""),
                return_exceptions=True,
            )
        assert sum(not isinstance(value, Exception) for value in results) == 1
        admin = await asyncpg.connect(seed.admin_dsn)
        try:
            final = await admin.fetchrow(
                "SELECT state, state_version FROM control_room_action_intents "
                "WHERE id=$1::uuid",
                promoted.intent_id,
            )
            decisions = await admin.fetchval(
                """SELECT count(*) FROM control_room_action_intent_events
                    WHERE intent_id=$1::uuid AND event_type IN ('approved','rejected')""",
                promoted.intent_id,
            )
        finally:
            await admin.close()
        assert final["state"] in {"approved", "rejected"}
        assert final["state_version"] == 3
        assert decisions == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_real_rls_hides_other_workspace_and_ledger_rejects_mutation(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    pool = await _pool(seed)
    try:
        scope = await seed_authority_item(seed, seed.first, "rls")
        promoted = await promote_live_intent(seed, pool, scope)
        conn = await asyncpg.connect(seed.console_dsn)
        try:
            async with conn.transaction():
                await conn.execute(
                    SET_SCOPE_SQL, seed.second.tenant_id, seed.second.workspace_id
                )
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM control_room_action_intents "
                        "WHERE id=$1::uuid",
                        promoted.intent_id,
                    )
                    == 0
                )
            async with conn.transaction():
                await conn.execute(
                    SET_SCOPE_SQL, seed.first.tenant_id, seed.first.workspace_id
                )
                event_id = await conn.fetchval(
                    "SELECT id FROM control_room_action_intent_events "
                    "WHERE intent_id=$1::uuid",
                    promoted.intent_id,
                )
                with pytest.raises(asyncpg.PostgresError):
                    await conn.execute(
                        "UPDATE control_room_action_intent_events "
                        "SET result_code='stale' WHERE id=$1",
                        event_id,
                    )
        finally:
            await conn.close()
    finally:
        await pool.close()
