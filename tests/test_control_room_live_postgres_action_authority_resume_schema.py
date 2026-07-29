from __future__ import annotations

import asyncio
import hashlib

import asyncpg
import pytest

from app.services.db_scope import SET_SCOPE_SQL
from tests.control_room_action_authority_dry_run import insert_authority_dry_run
from tests.control_room_action_authority_flow import promote_live_intent
from tests.control_room_action_authority_live import (
    AuthoritySeed,
    seed_authority,
    seed_authority_item,
)
from tests.control_room_action_authority_resume import (
    clone_resume_intent,
    dry_run_evidence_digest,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


_LIVE_STATES = {"pending_approval", "approved", "execution_reserved"}


@pytest.fixture(scope="module")
def authority_resume_seed(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> AuthoritySeed:
    return asyncio.run(
        seed_authority(postgres_with_real_init_schema, omega_console_live_dsn)
    )


async def _pool(seed: AuthoritySeed) -> asyncpg.Pool:
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=4)


def _digest(marker: str) -> str:
    return hashlib.sha256(marker.encode("utf-8")).hexdigest()


async def _terminal_source(
    conn: asyncpg.Connection,
    *,
    intent_id: str,
    checker_user_id: int,
    state: str,
) -> None:
    result = await conn.execute(
        """
        UPDATE control_room_action_intents
           SET checker_user_id=$2,
               executor_user_id=NULL,
               state=$3,
               state_version=3,
               result_code=$3,
               updated_at=NOW()
         WHERE id=$1::uuid
        """,
        intent_id,
        checker_user_id,
        state,
    )
    assert result == "UPDATE 1"


@pytest.mark.asyncio
async def test_schema_rejects_second_live_equivalent_intent_even_with_new_binding(
    authority_resume_seed: AuthoritySeed,
) -> None:
    seed = authority_resume_seed
    scope = await seed_authority_item(seed, seed.first, "live-equivalent")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
    finally:
        await pool.close()

    fresh_run = await insert_authority_dry_run(seed, scope)
    fresh_evidence = await dry_run_evidence_digest(seed.admin_dsn, fresh_run)

    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await clone_resume_intent(
                conn,
                source_intent_id=promoted.intent_id,
                binding_digest=_digest(f"second-live:{promoted.intent_id}"),
                state="pending_approval",
                result_code="created",
                dry_run_action_run_id=fresh_run,
                dry_run_evidence_digest=fresh_evidence,
            )
        count = await conn.fetchval(
            """
            SELECT count(*)
              FROM control_room_action_intents
             WHERE workspace_id=$1::uuid AND item_id=$2
               AND state = ANY($3::text[])
            """,
            scope.workspace_id,
            scope.item_id,
            sorted(_LIVE_STATES),
        )
    finally:
        await conn.close()
    assert count == 1


@pytest.mark.asyncio
async def test_schema_preserves_multiple_terminal_equivalent_histories(
    authority_resume_seed: AuthoritySeed,
) -> None:
    seed = authority_resume_seed
    scope = await seed_authority_item(seed, seed.first, "terminal-history")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
    finally:
        await pool.close()

    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        await _terminal_source(
            conn,
            intent_id=promoted.intent_id,
            checker_user_id=int(scope.checker["id"]),
            state="rejected",
        )
        historical_ids = [promoted.intent_id]
        for marker, state in (("second", "stale"), ("third", "rejected")):
            fresh_run = await insert_authority_dry_run(seed, scope)
            fresh_evidence = await dry_run_evidence_digest(seed.admin_dsn, fresh_run)
            historical_ids.append(
                await clone_resume_intent(
                    conn,
                    source_intent_id=promoted.intent_id,
                    binding_digest=_digest(f"{marker}:{promoted.intent_id}"),
                    state=state,
                    result_code=state,
                    checker_user_id=int(scope.checker["id"]),
                    dry_run_action_run_id=fresh_run,
                    dry_run_evidence_digest=fresh_evidence,
                )
            )
        rows = await conn.fetch(
            """
            SELECT id::text AS id, state, result_code
              FROM control_room_action_intents
             WHERE id = ANY($1::uuid[])
             ORDER BY id
            """,
            historical_ids,
        )
    finally:
        await conn.close()
    assert {row["id"] for row in rows} == set(historical_ids)
    assert {(row["state"], row["result_code"]) for row in rows} == {
        ("rejected", "rejected"),
        ("stale", "stale"),
    }


@pytest.mark.asyncio
async def test_real_migration_uses_no_clock_dependent_schema_predicate(
    authority_resume_seed: AuthoritySeed,
) -> None:
    conn = await asyncpg.connect(authority_resume_seed.admin_dsn)
    try:
        applied = await conn.fetchval(
            """
            SELECT count(*)
              FROM schema_migrations
             WHERE filename IN (
                '99zzb_control_room_action_authority.sql',
                '99zzc_control_room_action_authority_security.sql'
             )
            """
        )
        predicates = await conn.fetch(
            """
            SELECT pg_get_expr(index.indpred, index.indrelid) AS predicate
              FROM pg_index AS index
              JOIN pg_class AS relation ON relation.oid=index.indrelid
              JOIN pg_namespace AS namespace
                ON namespace.oid=relation.relnamespace
             WHERE namespace.nspname='public'
               AND relation.relname LIKE 'control_room_action%'
               AND index.indpred IS NOT NULL
            """
        )
    finally:
        await conn.close()

    assert applied == 2
    definitions = [str(row["predicate"]).lower() for row in predicates]
    assert all("now(" not in definition for definition in definitions)
    assert all("current_timestamp" not in definition for definition in definitions)


@pytest.mark.asyncio
async def test_rls_hides_terminal_intent_history_from_other_workspace(
    authority_resume_seed: AuthoritySeed,
) -> None:
    seed = authority_resume_seed
    scope = await seed_authority_item(seed, seed.first, "terminal-rls")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
    finally:
        await pool.close()

    fresh_run = await insert_authority_dry_run(seed, scope)
    fresh_evidence = await dry_run_evidence_digest(seed.admin_dsn, fresh_run)

    admin = await asyncpg.connect(seed.admin_dsn)
    try:
        await _terminal_source(
            admin,
            intent_id=promoted.intent_id,
            checker_user_id=int(scope.checker["id"]),
            state="rejected",
        )
        second_id = await clone_resume_intent(
            admin,
            source_intent_id=promoted.intent_id,
            binding_digest=_digest(f"rls-history:{promoted.intent_id}"),
            state="stale",
            result_code="stale",
            checker_user_id=int(scope.checker["id"]),
            dry_run_action_run_id=fresh_run,
            dry_run_evidence_digest=fresh_evidence,
        )
    finally:
        await admin.close()

    history_ids = [promoted.intent_id, second_id]
    conn = await asyncpg.connect(seed.console_dsn)
    try:
        async with conn.transaction():
            await conn.execute(
                SET_SCOPE_SQL,
                seed.second.tenant_id,
                seed.second.workspace_id,
            )
            hidden = await conn.fetchval(
                "SELECT count(*) FROM control_room_action_intents "
                "WHERE id = ANY($1::uuid[])",
                history_ids,
            )
        async with conn.transaction():
            await conn.execute(SET_SCOPE_SQL, scope.tenant_id, scope.workspace_id)
            visible = await conn.fetchval(
                "SELECT count(*) FROM control_room_action_intents "
                "WHERE id = ANY($1::uuid[])",
                history_ids,
            )
    finally:
        await conn.close()
    assert hidden == 0
    assert visible == 2
