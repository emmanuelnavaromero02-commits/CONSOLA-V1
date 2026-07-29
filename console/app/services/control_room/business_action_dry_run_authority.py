from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_action_authoritative_item import (
    AuthorityItemContract,
)
from app.services.control_room.business_action_digest import action_contract_digest
from app.services.control_room.business_execution_precondition import (
    require_matching_dry_run,
)


@dataclass(frozen=True, repr=False)
class AuthorityDryRun:
    action_run_id: int
    contract_digest: str
    evidence_digest: str


def _invalid_dry_run() -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "matching_dry_run_required",
            "message": "a matching successful dry-run is required before execution",
        },
    )


def _evidence_digest(row: Mapping[str, Any]) -> str:
    return action_contract_digest(
        {
            "version": "control-room-authority-dry-run/v1",
            "action_run_id": int(row["id"]),
            "tenant_id": str(row["tenant_id"]),
            "workspace_id": str(row["workspace_id"]),
            "item_id": str(row["item_id"]),
            "decision_id": int(row["decision_id"]),
            "action_type": str(row["action_type"]),
            "adapter_name": str(row["adapter_name"]),
            "mode": str(row["mode"]),
            "status": str(row["status"]),
            "actor_id": int(row["actor_id"]),
            "input": row["input"],
            "metadata": row["metadata"],
            "dry_run_result": row["dry_run_result"],
            "completed_at": row["completed_at"],
            "updated_at": row["updated_at"],
        }
    )


async def require_authority_dry_run(
    conn: Any,
    *,
    user: Mapping[str, Any],
    contract: AuthorityItemContract,
    action_run_id: int | None = None,
    expected_evidence_digest: str | None = None,
    expected_intent_id: str | None = None,
) -> AuthorityDryRun:
    row = await require_matching_dry_run(
        conn,
        user=user,
        item=contract.item,
        template_id="create_followup_task",
        actor_user_id=contract.maker_user_id,
        action_run_id=action_run_id,
        adapter_name="internal_followup_task",
        for_update=True,
    )
    linked_intent = str(row.get("action_intent_id") or "")
    if linked_intent != str(expected_intent_id or ""):
        raise _invalid_dry_run()
    contract_digest = contract.dry_run_digest()
    evidence_digest = _evidence_digest(row)
    if expected_evidence_digest and evidence_digest != expected_evidence_digest:
        raise _invalid_dry_run()
    return AuthorityDryRun(int(row["id"]), contract_digest, evidence_digest)


async def link_authority_dry_run(
    conn: Any,
    *,
    dry_run: AuthorityDryRun,
    contract: AuthorityItemContract,
    intent_id: str,
) -> None:
    row = await conn.fetchrow(
        """
        UPDATE action_runs
           SET action_intent_id = $1::uuid
         WHERE tenant_id = $2::uuid AND workspace_id = $3::uuid
           AND id = $4 AND action_intent_id IS NULL
         RETURNING id
        """,
        intent_id,
        contract.tenant_id,
        contract.workspace_id,
        dry_run.action_run_id,
    )
    if not row:
        raise _invalid_dry_run()


__all__ = (
    "AuthorityDryRun",
    "link_authority_dry_run",
    "require_authority_dry_run",
)
