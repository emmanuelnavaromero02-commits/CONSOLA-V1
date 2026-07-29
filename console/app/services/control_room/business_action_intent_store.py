from __future__ import annotations

from typing import Any

from app.services.control_room.business_action_authoritative_item import (
    AuthorityItemContract,
)
from app.services.control_room.business_action_dry_run_authority import (
    AuthorityDryRun,
)


async def find_binding_intent(
    conn: Any, *, contract: AuthorityItemContract
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT *, id::text AS id, tenant_id::text AS tenant_id,
               workspace_id::text AS workspace_id,
               correlation_id::text AS correlation_id
          FROM control_room_action_intents
         WHERE tenant_id=$1::uuid AND workspace_id=$2::uuid
           AND maker_user_id=$3 AND binding_digest=$4
         FOR UPDATE
        """,
        contract.tenant_id,
        contract.workspace_id,
        contract.maker_user_id,
        contract.binding_digest,
    )
    return dict(row) if row else None


def intent_matches_contract(
    intent: dict[str, Any], contract: AuthorityItemContract
) -> bool:
    expected = {
        "tenant_id": contract.tenant_id,
        "workspace_id": contract.workspace_id,
        "item_id": contract.item_id,
        "maker_user_id": str(contract.maker_user_id),
        "template_id": "create_followup_task",
        "binding_digest": contract.binding_digest,
        "evidence_digest": contract.evidence_digest,
        "observation_fingerprint": contract.observation_fingerprint,
        "contract_digest": contract.contract_digest,
        "target_digest": contract.target_digest,
        "dry_run_digest": contract.dry_run_digest(),
        "decision_digest": contract.decision_digest,
    }
    return all(
        str(intent.get(key) or "") == str(value) for key, value in expected.items()
    )


async def insert_intent(
    conn: Any,
    *,
    contract: AuthorityItemContract,
    dry_run: AuthorityDryRun,
    intent_id: str,
    correlation_id: str,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        INSERT INTO control_room_action_intents (
            id, tenant_id, workspace_id, item_id, maker_user_id, template_id,
            binding_digest, evidence_digest, observation_fingerprint,
            contract_digest, target_digest, dry_run_digest,
            dry_run_action_run_id, dry_run_evidence_digest, decision_digest,
            state, state_version, result_code, correlation_id, expires_at
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid, $4, $5, 'create_followup_task',
            $6, $7, $8, $9, $10, $11, $12, $13, $14,
            'pending_approval', 1, 'created', $15::uuid,
            NOW() + INTERVAL '24 hours'
        )
        ON CONFLICT (workspace_id, binding_digest) DO NOTHING
        RETURNING *, tenant_id::text AS tenant_id,
                     workspace_id::text AS workspace_id, id::text AS id,
                     correlation_id::text AS correlation_id
        """,
        intent_id,
        contract.tenant_id,
        contract.workspace_id,
        contract.item_id,
        contract.maker_user_id,
        contract.binding_digest,
        contract.evidence_digest,
        contract.observation_fingerprint,
        contract.contract_digest,
        contract.target_digest,
        dry_run.contract_digest,
        dry_run.action_run_id,
        dry_run.evidence_digest,
        contract.decision_digest,
        correlation_id,
    )
    return dict(row) if row else None


__all__ = ("find_binding_intent", "insert_intent", "intent_matches_contract")
