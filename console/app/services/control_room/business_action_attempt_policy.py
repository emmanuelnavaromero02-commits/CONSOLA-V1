from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_action_authoritative_item import (
    AuthorityItemContract,
)
from app.services.control_room.business_action_dry_run_authority import (
    require_authority_dry_run,
)


_PERMANENT_BLOCKERS = {
    "approved",
    "execution_reserved",
    "completed",
    "failed",
}


@dataclass(frozen=True)
class BindingIssue:
    reuse_intent_id: str | None = None
    dry_run_action_run_id: int | None = None
    dry_run_evidence_digest: str | None = None


def binding_attempt_lock_key(
    *,
    tenant_id: str,
    workspace_id: str,
    maker_user_id: int,
    item_id: str,
) -> str:
    return ":".join(
        (
            "control-room-intent",
            tenant_id,
            workspace_id,
            str(maker_user_id),
            item_id,
            "create_followup_task",
        )
    )


def _pending_matches(
    intent: Mapping[str, Any], contract: AuthorityItemContract
) -> bool:
    expected = {
        "binding_digest": contract.binding_digest,
        "evidence_digest": contract.evidence_digest,
        "observation_fingerprint": contract.observation_fingerprint,
        "contract_digest": contract.contract_digest,
        "target_digest": contract.target_digest,
        "dry_run_digest": contract.dry_run_digest(),
        "decision_digest": contract.decision_digest,
    }
    return all(str(intent.get(key) or "") == value for key, value in expected.items())


async def binding_issue_allowed(
    conn: Any, contract: AuthorityItemContract
) -> BindingIssue | None:
    rows = await conn.fetch(
        """
        SELECT id::text AS id, state, binding_digest, evidence_digest,
               observation_fingerprint, contract_digest, target_digest,
               dry_run_digest, decision_digest, expires_at > NOW() AS current
          FROM control_room_action_intents
         WHERE tenant_id=$1::uuid AND workspace_id=$2::uuid
           AND maker_user_id=$3 AND item_id=$4
           AND template_id='create_followup_task'
         ORDER BY created_at DESC, id DESC
        """,
        contract.tenant_id,
        contract.workspace_id,
        contract.maker_user_id,
        contract.item_id,
    )
    if any(str(row["state"]) in _PERMANENT_BLOCKERS for row in rows):
        return None
    current_pending = next(
        (
            row
            for row in rows
            if row["state"] == "pending_approval" and row["current"] is True
        ),
        None,
    )
    if current_pending is not None:
        return (
            BindingIssue(reuse_intent_id=str(current_pending["id"]))
            if _pending_matches(current_pending, contract)
            else None
        )
    if not rows:
        return BindingIssue()
    try:
        dry_run = await require_authority_dry_run(
            conn,
            user={
                "active_tenant_id": contract.tenant_id,
                "active_workspace_id": contract.workspace_id,
            },
            contract=contract,
        )
    except HTTPException:
        return None
    return BindingIssue(
        dry_run_action_run_id=dry_run.action_run_id,
        dry_run_evidence_digest=dry_run.evidence_digest,
    )


__all__ = ("BindingIssue", "binding_attempt_lock_key", "binding_issue_allowed")
