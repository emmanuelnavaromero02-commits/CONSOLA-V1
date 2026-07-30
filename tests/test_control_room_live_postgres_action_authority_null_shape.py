from __future__ import annotations

import asyncio

import asyncpg
import pytest

from app.services.db_scope import SET_SCOPE_SQL
from tests.control_room_action_authority_flow import issue_live_binding
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
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=3)


async def _binding_id(seed: AuthoritySeed, marker: str) -> tuple[object, object]:
    scope = await seed_authority_item(seed, seed.first, marker)
    pool = await _pool(seed)
    try:
        await issue_live_binding(seed, pool, scope)
    finally:
        await pool.close()
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        token_id = await conn.fetchval(
            "SELECT id FROM control_room_action_tokens "
            "WHERE workspace_id=$1::uuid AND subject_user_id=$2 "
            "AND item_id=$3 AND stage='action_binding'",
            scope.workspace_id,
            scope.maker["id"],
            scope.item_id,
        )
    finally:
        await conn.close()
    return scope, token_id


async def _check_violation(seed: AuthoritySeed, scope, sql: str, *args: object) -> None:
    conn = await asyncpg.connect(seed.console_dsn)
    try:
        with pytest.raises(asyncpg.CheckViolationError) as violation:
            async with conn.transaction():
                await conn.execute(SET_SCOPE_SQL, scope.tenant_id, scope.workspace_id)
                await conn.execute(sql, *args)
        assert violation.value.sqlstate == "23514"
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "column",
    ("binding_handle_nonce", "template_id"),
)
async def test_binding_update_rejects_required_null(
    authority_seed: AuthoritySeed, column: str
):
    seed = authority_seed
    scope, token_id = await _binding_id(seed, f"null-update-{column}")
    await _check_violation(
        seed,
        scope,
        f"UPDATE control_room_action_tokens SET {column}=NULL WHERE id=$1::uuid",
        token_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "null_column",
    ("binding_handle_nonce", "template_id"),
)
async def test_binding_insert_and_null_duplicate_are_rejected(
    authority_seed: AuthoritySeed, null_column: str
):
    seed = authority_seed
    scope, token_id = await _binding_id(seed, f"null-insert-{null_column}")
    null_nonce = (
        "NULL"
        if null_column == "binding_handle_nonce"
        else "source.binding_handle_nonce"
    )
    null_template = "NULL" if null_column == "template_id" else "source.template_id"
    sql = f"""
        INSERT INTO control_room_action_tokens (
            tenant_id, workspace_id, intent_id, stage, subject_user_id,
            token_digest, binding_handle_nonce, item_id, template_id,
            binding_digest, evidence_digest, observation_fingerprint,
            contract_digest, target_digest, decision_digest,
            binding_dry_run_action_run_id, binding_dry_run_evidence_digest,
            issued_at, expires_at
        )
        SELECT source.tenant_id, source.workspace_id, NULL, 'action_binding',
               source.subject_user_id + CASE WHEN $2 THEN 100000 ELSE 0 END,
               digest(gen_random_uuid()::text, 'sha256'),
               {null_nonce}, source.item_id, {null_template},
               source.binding_digest, source.evidence_digest,
               source.observation_fingerprint, source.contract_digest,
               source.target_digest, source.decision_digest, NULL, NULL,
               NOW(), NOW() + INTERVAL '5 minutes'
          FROM control_room_action_tokens AS source
         WHERE source.id=$1::uuid
    """
    await _check_violation(
        seed, scope, sql, token_id, null_column == "binding_handle_nonce"
    )
