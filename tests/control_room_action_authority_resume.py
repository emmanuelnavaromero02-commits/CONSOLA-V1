from __future__ import annotations

from typing import Any
import uuid

import asyncpg

from app.services.control_room.business_action_dry_run_authority import (
    _evidence_digest,
)


async def dry_run_evidence_digest(admin_dsn: str, action_run_id: int) -> str:
    conn = await asyncpg.connect(admin_dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT id, tenant_id::text AS tenant_id,
                   workspace_id::text AS workspace_id,
                   item_id, decision_id, action_type, adapter_name,
                   mode, status, actor_id, input, metadata, dry_run_result,
                   completed_at, updated_at
              FROM action_runs
             WHERE id=$1
            """,
            action_run_id,
        )
    finally:
        await conn.close()
    assert row is not None
    return _evidence_digest(dict(row))


async def clone_resume_intent(
    conn: Any,
    *,
    source_intent_id: str,
    binding_digest: str,
    state: str,
    result_code: str,
    checker_user_id: int | None = None,
    dry_run_action_run_id: int | None = None,
    dry_run_evidence_digest: str | None = None,
) -> str:
    intent_id = str(uuid.uuid4())
    inserted = await conn.fetchval(
        """
        INSERT INTO control_room_action_intents (
            id, tenant_id, workspace_id, item_id, maker_user_id,
            checker_user_id, executor_user_id, template_id, binding_digest,
            evidence_digest, observation_fingerprint, contract_digest,
            target_digest, dry_run_digest, dry_run_action_run_id,
            dry_run_evidence_digest, decision_digest, state, state_version,
            result_code, correlation_id, created_at, updated_at, expires_at
        )
        SELECT $2::uuid, tenant_id, workspace_id, item_id, maker_user_id,
               $4::bigint, NULL::bigint, template_id, $3,
               evidence_digest, observation_fingerprint, contract_digest,
               target_digest, dry_run_digest,
               COALESCE($8::bigint, dry_run_action_run_id),
               COALESCE($9::text, dry_run_evidence_digest),
               decision_digest, $5, 1, $6, $7::uuid, NOW(), NOW(),
               LEAST(expires_at, NOW() + INTERVAL '23 hours')
          FROM control_room_action_intents
         WHERE id=$1::uuid
        RETURNING id::text
        """,
        source_intent_id,
        intent_id,
        binding_digest,
        checker_user_id,
        state,
        result_code,
        str(uuid.uuid4()),
        dry_run_action_run_id,
        dry_run_evidence_digest,
    )
    assert inserted == intent_id
    if dry_run_action_run_id is not None:
        linked = await conn.execute(
            """UPDATE action_runs SET action_intent_id=$1::uuid
                 WHERE id=$2 AND action_intent_id IS NULL""",
            intent_id,
            dry_run_action_run_id,
        )
        assert linked == "UPDATE 1"
    return intent_id


__all__ = ("clone_resume_intent", "dry_run_evidence_digest")
