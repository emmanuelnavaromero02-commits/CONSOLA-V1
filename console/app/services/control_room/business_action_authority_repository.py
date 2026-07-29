from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_action_authoritative_item import (
    AuthorityItemContract,
)
from app.services.control_room.business_action_attempt_policy import (
    binding_attempt_lock_key,
    binding_issue_allowed,
)
from app.services.control_room.business_action_authority_policy import (
    ACTION_BINDING_TTL_SECONDS,
    actor_id,
    authority_scope,
)
from app.services.control_room.business_action_tokens import (
    handle_digest,
    new_handle,
)


INTENT_TABLE = "control_room_action_intents"

AUTHORITATIVE_ITEMS_SQL = """
SELECT item.tenant_id::text AS tenant_id,
       item.workspace_id::text AS workspace_id,
       item.owner_user_id, item.item_id, item.cartridge_id, item.domain,
       item.source_dataset, item.item_kind, item.title, item.severity,
       item.status, item.decision_id, item.entity_kind, item.entity_id,
       item.entity_label, item.anomaly_type, item.metadata,
       item.first_seen_at, item.last_seen_at, item.resolved_at,
       item.dismissed_at, item.impact_estimate, item.impact_currency,
       item.confidence, item.priority_score, item.selected_option_id,
       item.execution_status,
       decision.workspace_id::text AS decision_workspace_id
  FROM control_room_items AS item
  JOIN decisions AS decision
    ON decision.id = item.decision_id
   AND decision.workspace_id = item.workspace_id
 WHERE item.tenant_id = $1::uuid
   AND item.workspace_id = $2::uuid
   AND item.item_id = ANY($3::text[])
 ORDER BY item.item_id
 LIMIT $4
"""


async def fetch_authoritative_rows(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    item_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    bounded = sorted({str(value) for value in item_ids if str(value).strip()})[:1000]
    if not bounded:
        return {}
    rows = await conn.fetch(
        AUTHORITATIVE_ITEMS_SQL,
        tenant_id,
        workspace_id,
        bounded,
        len(bounded),
    )
    return {str(row["item_id"]): dict(row) for row in rows}


async def fetch_authoritative_row_for_update(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    item_id: str,
) -> dict[str, Any] | None:
    rows = await conn.fetch(
        AUTHORITATIVE_ITEMS_SQL + " FOR UPDATE OF item",
        tenant_id,
        workspace_id,
        [item_id],
        1,
    )
    return dict(rows[0]) if rows else None


async def insert_action_binding_token(
    conn: Any,
    contract: AuthorityItemContract,
    *,
    now: datetime | None = None,
) -> tuple[str, datetime] | None:
    issued_at = (now or datetime.now(UTC)).astimezone(UTC)
    expires_at = issued_at + timedelta(seconds=ACTION_BINDING_TTL_SECONDS)
    handle = new_handle()
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
        binding_attempt_lock_key(
            tenant_id=contract.tenant_id,
            workspace_id=contract.workspace_id,
            maker_user_id=contract.maker_user_id,
            item_id=contract.item_id,
        ),
    )
    issue = await binding_issue_allowed(conn, contract)
    if issue is None:
        return None
    row = await conn.fetchrow(
        """
        INSERT INTO control_room_action_tokens (
            tenant_id, workspace_id, intent_id, stage, subject_user_id, token_digest,
            item_id, template_id, binding_digest, evidence_digest,
            observation_fingerprint, contract_digest, target_digest,
            decision_digest, binding_dry_run_action_run_id,
            binding_dry_run_evidence_digest, issued_at, expires_at
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid, 'action_binding', $4, $5,
            $6, 'create_followup_task', $7, $8, $9, $10, $11, $12,
            $13, $14, $15, $16
        )
        ON CONFLICT (
            tenant_id, workspace_id, subject_user_id, item_id, template_id
        ) WHERE stage = 'action_binding' AND status = 'active'
        DO UPDATE SET
            token_digest = EXCLUDED.token_digest,
            intent_id = EXCLUDED.intent_id,
            binding_digest = EXCLUDED.binding_digest,
            evidence_digest = EXCLUDED.evidence_digest,
            observation_fingerprint = EXCLUDED.observation_fingerprint,
            contract_digest = EXCLUDED.contract_digest,
            target_digest = EXCLUDED.target_digest,
            decision_digest = EXCLUDED.decision_digest,
            binding_dry_run_action_run_id = EXCLUDED.binding_dry_run_action_run_id,
            binding_dry_run_evidence_digest = EXCLUDED.binding_dry_run_evidence_digest,
            issued_at = EXCLUDED.issued_at,
            expires_at = EXCLUDED.expires_at
        RETURNING id
        """,
        contract.tenant_id,
        contract.workspace_id,
        issue.reuse_intent_id,
        contract.maker_user_id,
        handle_digest(handle),
        contract.item_id,
        contract.binding_digest,
        contract.evidence_digest,
        contract.observation_fingerprint,
        contract.contract_digest,
        contract.target_digest,
        contract.decision_digest,
        issue.dry_run_action_run_id,
        issue.dry_run_evidence_digest,
        issued_at,
        expires_at,
    )
    if not row:
        raise RuntimeError("control room action binding token insert failed")
    return handle, expires_at


async def resolve_action_binding_token(
    conn: Any,
    *,
    user: Mapping[str, Any],
    action_handle: str,
    for_update: bool = False,
) -> dict[str, Any]:
    tenant_id, workspace_id = authority_scope(user)
    lock = " FOR UPDATE" if for_update else ""
    row = await conn.fetchrow(
        """
        SELECT id::text, tenant_id::text, workspace_id::text, intent_id::text,
               subject_user_id, item_id, template_id, binding_digest,
               evidence_digest, observation_fingerprint, contract_digest,
               target_digest, decision_digest, binding_dry_run_action_run_id,
               binding_dry_run_evidence_digest, issued_at, expires_at, status
          FROM control_room_action_tokens
         WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid
           AND stage = 'action_binding' AND subject_user_id = $3
           AND token_digest = $4 AND status = 'active' AND expires_at > NOW()
        """
        + lock,
        tenant_id,
        workspace_id,
        actor_id(user),
        handle_digest(action_handle),
    )
    if not row:
        raise HTTPException(404, "action authority not found")
    return dict(row)


async def consume_action_binding_token(
    conn: Any,
    *,
    token_id: str,
    user: Mapping[str, Any],
    operation_digest: str,
    result_state: str,
    result_version: int,
) -> None:
    tenant_id, workspace_id = authority_scope(user)
    row = await conn.fetchrow(
        """
        UPDATE control_room_action_tokens
           SET status = 'consumed', operation_digest = $1,
               result_state = $2, result_version = $3,
               consumed_at = NOW(), consumed_by = $4
         WHERE id = $5::uuid AND tenant_id = $6::uuid AND workspace_id = $7::uuid
           AND stage = 'action_binding' AND subject_user_id = $4
           AND status = 'active' AND expires_at > NOW()
         RETURNING id
        """,
        operation_digest,
        result_state,
        int(result_version),
        actor_id(user),
        token_id,
        tenant_id,
        workspace_id,
    )
    if not row:
        raise HTTPException(404, "action authority not found")


async def revoke_action_binding_token(
    conn: Any, *, token_id: str, user: Mapping[str, Any]
) -> None:
    tenant_id, workspace_id = authority_scope(user)
    row = await conn.fetchrow(
        """
        UPDATE control_room_action_tokens
           SET status = 'revoked', consumed_at = NOW(), consumed_by = $1
         WHERE id = $2::uuid AND tenant_id = $3::uuid AND workspace_id = $4::uuid
           AND stage = 'action_binding' AND subject_user_id = $1
           AND status = 'active'
         RETURNING id
        """,
        actor_id(user),
        token_id,
        tenant_id,
        workspace_id,
    )
    if not row:
        raise HTTPException(404, "action authority not found")


__all__ = (
    "AUTHORITATIVE_ITEMS_SQL",
    "INTENT_TABLE",
    "consume_action_binding_token",
    "fetch_authoritative_rows",
    "fetch_authoritative_row_for_update",
    "insert_action_binding_token",
    "resolve_action_binding_token",
    "revoke_action_binding_token",
)
