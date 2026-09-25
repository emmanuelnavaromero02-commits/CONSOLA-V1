from __future__ import annotations

import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.control_room.business_action_attempt_policy import BindingIssue
from app.services.control_room.business_action_authoritative_item import (
    AuthorityItemContract,
)
from app.services.control_room.business_action_authority_policy import (
    ACTION_BINDING_TTL_SECONDS,
)
from app.services.control_room.business_action_tokens import handle_digest
from app.services.security_context import sign_server_payload


_PURPOSE = "control-room-action-binding/v1"


def _handle_from_nonce(value: object) -> str:
    nonce = bytes(value) if isinstance(value, (bytes, bytearray, memoryview)) else b""
    if len(nonce) != 32:
        raise RuntimeError("control room action binding nonce is invalid")
    return sign_server_payload(nonce, purpose=_PURPOSE)


def _same_optional(left: object, right: object) -> bool:
    return (str(left) if left is not None else None) == (
        str(right) if right is not None else None
    )


def _slot_is_current(
    row: Mapping[str, Any],
    contract: AuthorityItemContract,
    issue: BindingIssue,
    now: datetime,
) -> bool:
    expires_at = row.get("expires_at")
    expected = {
        "binding_digest": contract.binding_digest,
        "evidence_digest": contract.evidence_digest,
        "observation_fingerprint": contract.observation_fingerprint,
        "contract_digest": contract.contract_digest,
        "target_digest": contract.target_digest,
        "decision_digest": contract.decision_digest,
        "access_revision_digest": contract.access_revision_digest,
        "rbac_policy_digest": contract.rbac_policy_digest,
    }
    return bool(
        row.get("status") == "active"
        and isinstance(expires_at, datetime)
        and expires_at.astimezone(UTC) > now
        and all(str(row.get(key) or "") == value for key, value in expected.items())
        and _same_optional(row.get("intent_id"), issue.reuse_intent_id)
        and _same_optional(
            row.get("binding_dry_run_action_run_id"), issue.dry_run_action_run_id
        )
        and _same_optional(
            row.get("binding_dry_run_evidence_digest"),
            issue.dry_run_evidence_digest,
        )
    )


async def issue_binding_slot(
    conn: Any,
    contract: AuthorityItemContract,
    issue: BindingIssue,
    *,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    issued_at = (now or datetime.now(UTC)).astimezone(UTC)
    row = await conn.fetchrow(
        """
        SELECT id::text, intent_id::text, status, token_digest,
               binding_handle_nonce,
               binding_digest, evidence_digest, observation_fingerprint,
               contract_digest, target_digest, decision_digest,
               access_revision_digest, rbac_policy_digest,
               binding_dry_run_action_run_id, binding_dry_run_evidence_digest,
               issued_at, expires_at
          FROM control_room_action_tokens
         WHERE tenant_id=$1::uuid AND workspace_id=$2::uuid
           AND subject_user_id=$3 AND item_id=$4
           AND template_id='create_followup_task' AND stage='action_binding'
         FOR UPDATE
        """,
        contract.tenant_id,
        contract.workspace_id,
        contract.maker_user_id,
        contract.item_id,
    )
    if row and _slot_is_current(row, contract, issue, issued_at):
        handle = _handle_from_nonce(row["binding_handle_nonce"])
        if handle_digest(handle) != bytes(row["token_digest"]):
            raise RuntimeError("control room action binding digest is invalid")
        return handle, row["expires_at"]

    nonce = secrets.token_bytes(32)
    handle = _handle_from_nonce(nonce)
    expires_at = issued_at + timedelta(seconds=ACTION_BINDING_TTL_SECONDS)
    values = (
        issue.reuse_intent_id,
        handle_digest(handle),
        nonce,
        contract.binding_digest,
        contract.evidence_digest,
        contract.observation_fingerprint,
        contract.contract_digest,
        contract.target_digest,
        contract.decision_digest,
        contract.access_revision_digest,
        contract.rbac_policy_digest,
        issue.dry_run_action_run_id,
        issue.dry_run_evidence_digest,
        issued_at,
        expires_at,
    )
    if row:
        saved = await conn.fetchrow(
            """
            UPDATE control_room_action_tokens
               SET intent_id=$2::uuid, token_digest=$3, binding_handle_nonce=$4,
                   binding_digest=$5, evidence_digest=$6,
                   observation_fingerprint=$7, contract_digest=$8,
                   target_digest=$9, decision_digest=$10,
                   access_revision_digest=$11, rbac_policy_digest=$12,
                   binding_dry_run_action_run_id=$13,
                   binding_dry_run_evidence_digest=$14, status='active',
                   operation_digest=NULL, result_state=NULL, result_version=NULL,
                   issued_at=$15, expires_at=$16, consumed_at=NULL, consumed_by=NULL
             WHERE id=$1::uuid RETURNING id
            """,
            row["id"],
            *values,
        )
    else:
        saved = await conn.fetchrow(
            """
            INSERT INTO control_room_action_tokens (
                tenant_id, workspace_id, intent_id, stage, subject_user_id,
                token_digest, binding_handle_nonce, item_id, template_id,
                binding_digest, evidence_digest, observation_fingerprint,
                contract_digest, target_digest, decision_digest,
                access_revision_digest, rbac_policy_digest,
                binding_dry_run_action_run_id, binding_dry_run_evidence_digest,
                issued_at, expires_at
            ) VALUES (
                $1::uuid,$2::uuid,$3::uuid,'action_binding',$4,$5,$6,$7,
                'create_followup_task',$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19
            ) RETURNING id
            """,
            contract.tenant_id,
            contract.workspace_id,
            issue.reuse_intent_id,
            contract.maker_user_id,
            handle_digest(handle),
            nonce,
            contract.item_id,
            contract.binding_digest,
            contract.evidence_digest,
            contract.observation_fingerprint,
            contract.contract_digest,
            contract.target_digest,
            contract.decision_digest,
            contract.access_revision_digest,
            contract.rbac_policy_digest,
            issue.dry_run_action_run_id,
            issue.dry_run_evidence_digest,
            issued_at,
            expires_at,
        )
    if not saved:
        raise RuntimeError("control room action binding token save failed")
    return handle, expires_at


__all__ = ("issue_binding_slot",)
