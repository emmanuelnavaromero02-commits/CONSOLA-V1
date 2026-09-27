from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.control_room.business_action_authority_policy import (
    ACTION_BINDING_TTL_SECONDS,
    DIRECT_ACTION_TEMPLATE_IDS,
)
from app.services.control_room.business_action_binding_slot import (
    binding_handle_from_nonce,
)
from app.services.control_room.business_action_direct_contract import (
    DirectActionContract,
)
from app.services.control_room.business_action_tokens import handle_digest


REUSE_MARGIN_SECONDS = 5 * 60
RECENT_CONSUMPTION_SECONDS = 60
SlotKey = tuple[str, str]

EXISTING_SLOTS_SQL = """
SELECT item_id, template_id, status, token_digest, binding_handle_nonce,
       binding_digest, evidence_digest, observation_fingerprint,
       contract_digest, target_digest, decision_digest,
       access_revision_digest, rbac_policy_digest,
       intent_id::text AS intent_id, binding_dry_run_action_run_id,
       expires_at, consumed_at
  FROM control_room_action_tokens
 WHERE tenant_id = $1::uuid
   AND workspace_id = $2::uuid
   AND stage = 'action_binding'
   AND subject_user_id = $3
   AND item_id = ANY($4::text[])
   AND template_id = ANY($5::text[])
"""

UPSERT_SLOTS_SQL = """
INSERT INTO control_room_action_tokens (
    tenant_id, workspace_id, stage, subject_user_id, token_digest,
    binding_handle_nonce, item_id, template_id, binding_digest,
    evidence_digest, observation_fingerprint, contract_digest,
    target_digest, decision_digest, access_revision_digest,
    rbac_policy_digest, issued_at, expires_at
)
SELECT $1::uuid, $2::uuid, 'action_binding', $3, slot.token_digest,
       slot.nonce, slot.item_id, slot.template_id, slot.binding_digest,
       slot.evidence_digest, slot.observation_fingerprint,
       slot.contract_digest, slot.target_digest, slot.decision_digest,
       $4, $5, $6, $7
  FROM unnest(
       $10::bytea[], $11::bytea[], $12::text[], $13::text[], $14::text[],
       $15::text[], $16::text[], $17::text[], $18::text[], $19::text[]
  ) AS slot(
       token_digest, nonce, item_id, template_id, binding_digest,
       evidence_digest, observation_fingerprint, contract_digest,
       target_digest, decision_digest
  )
ON CONFLICT (tenant_id, workspace_id, subject_user_id, item_id, template_id)
   WHERE stage = 'action_binding'
DO UPDATE SET
    intent_id = NULL,
    token_digest = EXCLUDED.token_digest,
    binding_handle_nonce = EXCLUDED.binding_handle_nonce,
    binding_digest = EXCLUDED.binding_digest,
    evidence_digest = EXCLUDED.evidence_digest,
    observation_fingerprint = EXCLUDED.observation_fingerprint,
    contract_digest = EXCLUDED.contract_digest,
    target_digest = EXCLUDED.target_digest,
    decision_digest = EXCLUDED.decision_digest,
    access_revision_digest = EXCLUDED.access_revision_digest,
    rbac_policy_digest = EXCLUDED.rbac_policy_digest,
    binding_dry_run_action_run_id = NULL,
    binding_dry_run_evidence_digest = NULL,
    status = 'active',
    operation_digest = NULL,
    result_state = NULL,
    result_version = NULL,
    issued_at = EXCLUDED.issued_at,
    expires_at = EXCLUDED.expires_at,
    consumed_at = NULL,
    consumed_by = NULL
WHERE NOT (
        control_room_action_tokens.status = 'active'
    AND control_room_action_tokens.expires_at > $8
    AND control_room_action_tokens.intent_id IS NULL
    AND control_room_action_tokens.binding_dry_run_action_run_id IS NULL
    AND control_room_action_tokens.contract_digest = EXCLUDED.contract_digest
    AND control_room_action_tokens.access_revision_digest
        = EXCLUDED.access_revision_digest
    AND control_room_action_tokens.rbac_policy_digest
        = EXCLUDED.rbac_policy_digest
)
AND NOT (
        control_room_action_tokens.status = 'consumed'
    AND control_room_action_tokens.contract_digest = EXCLUDED.contract_digest
    AND control_room_action_tokens.consumed_at > $9
)
RETURNING item_id, template_id, token_digest
"""

_DIGEST_FIELDS = (
    "binding_digest",
    "evidence_digest",
    "observation_fingerprint",
    "contract_digest",
    "target_digest",
    "decision_digest",
    "access_revision_digest",
    "rbac_policy_digest",
)


def _key(item_id: object, template_id: object) -> SlotKey:
    return str(item_id or ""), str(template_id or "")


def _as_utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


def _same_contract(row: Mapping[str, Any], contract: DirectActionContract) -> bool:
    return all(
        str(row.get(field) or "") == str(getattr(contract, field))
        for field in _DIGEST_FIELDS
    )


def _current_handle(
    row: Mapping[str, Any], contract: DirectActionContract, reuse_after: datetime
) -> str | None:
    expires_at = _as_utc(row.get("expires_at"))
    if not (
        row.get("status") == "active"
        and expires_at is not None
        and expires_at > reuse_after
        and row.get("intent_id") is None
        and row.get("binding_dry_run_action_run_id") is None
        and _same_contract(row, contract)
    ):
        return None
    handle = binding_handle_from_nonce(row.get("binding_handle_nonce"))
    if handle_digest(handle) != bytes(row.get("token_digest") or b""):
        return None
    return handle


def _recently_consumed(
    row: Mapping[str, Any], contract: DirectActionContract, since: datetime
) -> bool:
    consumed_at = _as_utc(row.get("consumed_at"))
    return bool(
        row.get("status") == "consumed"
        and consumed_at is not None
        and consumed_at > since
        and str(row.get("contract_digest") or "") == contract.contract_digest
    )


async def _existing(
    conn: Any, contracts: Sequence[DirectActionContract]
) -> dict[SlotKey, dict[str, Any]]:
    first = contracts[0]
    rows = await conn.fetch(
        EXISTING_SLOTS_SQL,
        first.tenant_id,
        first.workspace_id,
        first.maker_user_id,
        sorted({contract.item_id for contract in contracts}),
        sorted({contract.template_id for contract in contracts}),
    )
    return {_key(row["item_id"], row["template_id"]): dict(row) for row in rows}


async def issue_direct_slots(
    conn: Any,
    contracts: Sequence[DirectActionContract],
    *,
    now: datetime | None = None,
) -> dict[SlotKey, str]:
    """Reuse current slots and write only missing or outdated ones in one statement."""
    if not contracts:
        return {}
    first = contracts[0]
    if any(
        (contract.tenant_id, contract.workspace_id, contract.maker_user_id)
        != (first.tenant_id, first.workspace_id, first.maker_user_id)
        or contract.template_id not in DIRECT_ACTION_TEMPLATE_IDS
        or (contract.access_revision_digest, contract.rbac_policy_digest)
        != (first.access_revision_digest, first.rbac_policy_digest)
        for contract in contracts
    ):
        raise RuntimeError("control room direct slots must share one authority scope")
    issued_at = (now or datetime.now(UTC)).astimezone(UTC)
    reuse_after = issued_at + timedelta(seconds=REUSE_MARGIN_SECONDS)
    consumed_since = issued_at - timedelta(seconds=RECENT_CONSUMPTION_SECONDS)
    existing = await _existing(conn, contracts)

    handles: dict[SlotKey, str] = {}
    pending: list[tuple[DirectActionContract, bytes, str]] = []
    for contract in contracts:
        key = _key(contract.item_id, contract.template_id)
        row = existing.get(key)
        if row is not None:
            current = _current_handle(row, contract, reuse_after)
            if current is not None:
                handles[key] = current
                continue
            if _recently_consumed(row, contract, consumed_since):
                continue
        nonce = secrets.token_bytes(32)
        pending.append((contract, nonce, binding_handle_from_nonce(nonce)))
    if not pending:
        return handles

    saved = await conn.fetch(
        UPSERT_SLOTS_SQL,
        first.tenant_id,
        first.workspace_id,
        first.maker_user_id,
        first.access_revision_digest,
        first.rbac_policy_digest,
        issued_at,
        issued_at + timedelta(seconds=ACTION_BINDING_TTL_SECONDS),
        reuse_after,
        consumed_since,
        [handle_digest(handle) for _contract, _nonce, handle in pending],
        [nonce for _contract, nonce, _handle in pending],
        [contract.item_id for contract, _nonce, _handle in pending],
        [contract.template_id for contract, _nonce, _handle in pending],
        [contract.binding_digest for contract, _nonce, _handle in pending],
        [contract.evidence_digest for contract, _nonce, _handle in pending],
        [contract.observation_fingerprint for contract, _nonce, _handle in pending],
        [contract.contract_digest for contract, _nonce, _handle in pending],
        [contract.target_digest for contract, _nonce, _handle in pending],
        [contract.decision_digest for contract, _nonce, _handle in pending],
    )
    written = {
        _key(row["item_id"], row["template_id"]): bytes(row["token_digest"])
        for row in saved
    }
    leftovers: list[DirectActionContract] = []
    for contract, _nonce, handle in pending:
        key = _key(contract.item_id, contract.template_id)
        if written.get(key) == handle_digest(handle):
            handles[key] = handle
        else:
            leftovers.append(contract)
    if leftovers:
        concurrent = await _existing(conn, leftovers)
        for contract in leftovers:
            key = _key(contract.item_id, contract.template_id)
            row = concurrent.get(key)
            current = (
                _current_handle(row, contract, issued_at) if row is not None else None
            )
            if current is not None:
                handles[key] = current
    return handles


__all__ = (
    "EXISTING_SLOTS_SQL",
    "RECENT_CONSUMPTION_SECONDS",
    "REUSE_MARGIN_SECONDS",
    "UPSERT_SLOTS_SQL",
    "issue_direct_slots",
)
