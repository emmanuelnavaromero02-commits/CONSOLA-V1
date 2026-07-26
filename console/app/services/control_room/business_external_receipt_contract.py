from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from app.services.control_room.business_action_digest import action_contract_digest
from app.services.control_room.business_action_runtime_contract import (
    runtime_action_digests,
)
from app.services.control_room.business_execution_target import (
    execution_reconciliation_context,
)
from app.services.control_room.business_projection import (
    normalize_persisted_business_item,
)
from app.services.control_room.business_workflow_provenance import (
    ELIGIBILITY_POLICY_VERSION,
    WorkflowStage,
    business_observation_fingerprint,
    workflow_has_eligible_provenance,
)


_AUTHORITY_AUDIT_KEY = "authority_audit"
_AUTHORITY_AUDIT_VERSION = "control-room-authority-audit/v1"
_AUTHORITY_AUDIT_KEYS = frozenset(
    {
        "version",
        "binding_id",
        "key_id",
        "issued_at",
        "expires_at",
        "observation_fingerprint",
        "execution_target_digest",
        "template_contract_digest",
        "input_payload_digest",
    }
)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _runtime_item(
    item_row: Mapping[str, Any], contract: Mapping[str, Any]
) -> dict[str, Any] | None:
    context = contract.get("reconciliation_context")
    if not isinstance(context, Mapping) or set(context) != {"threshold_state"}:
        return None
    normalized = normalize_persisted_business_item(item_row)
    candidate = {**normalized, **dict(context)}
    try:
        expected = execution_reconciliation_context(candidate)
    except ValueError:
        return None
    if dict(context) != expected:
        return None
    return candidate


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def authority_audit_matches_contract(
    metadata: Mapping[str, Any], contract: Mapping[str, Any]
) -> bool:
    audit = metadata.get(_AUTHORITY_AUDIT_KEY)
    if not isinstance(audit, Mapping) or set(audit) != _AUTHORITY_AUDIT_KEYS:
        return False
    issued_at = _timestamp(audit.get("issued_at"))
    expires_at = _timestamp(audit.get("expires_at"))
    if not (
        audit.get("version") == _AUTHORITY_AUDIT_VERSION
        and isinstance(audit.get("binding_id"), str)
        and bool(str(audit.get("binding_id") or "").strip())
        and isinstance(audit.get("key_id"), str)
        and bool(str(audit.get("key_id") or "").strip())
        and issued_at is not None
        and expires_at is not None
        and expires_at - issued_at == timedelta(seconds=900)
    ):
        return False
    pairs = {
        "observation_fingerprint": "fingerprint",
        "execution_target_digest": "execution_target_digest",
        "template_contract_digest": "template_contract_digest",
        "input_payload_digest": "input_payload_digest",
    }
    return all(
        audit.get(audit_key) == contract.get(contract_key)
        for audit_key, contract_key in pairs.items()
    )


def reservation_authority_audit_valid(
    row: Mapping[str, Any], expected_audit: Mapping[str, Any]
) -> bool:
    metadata = _mapping(row.get("metadata"))
    stored_audit = metadata.get(_AUTHORITY_AUDIT_KEY)
    return bool(
        isinstance(stored_audit, Mapping)
        and dict(stored_audit) == dict(expected_audit)
        and reservation_stored_authority_audit_valid(row)
    )


def reservation_stored_authority_audit_valid(row: Mapping[str, Any]) -> bool:
    metadata = _mapping(row.get("metadata"))
    contract = metadata.get("reservation_contract")
    return isinstance(contract, Mapping) and authority_audit_matches_contract(
        metadata, contract
    )


def receipt_contract_matches(
    receipt: Mapping[str, Any], item_row: Mapping[str, Any], workspace_id: str
) -> bool:
    metadata = _mapping(receipt.get("metadata"))
    contract = metadata.get("reservation_contract")
    if not isinstance(contract, Mapping):
        return False
    if not authority_audit_matches_contract(metadata, contract):
        return False
    result = _mapping(receipt.get("execution_result"))
    if result.get("executed") is not True:
        return False
    projection_status = result.get("local_projection_status")
    if projection_status not in {
        "pending_reconciliation",
        "completed",
    }:
        return False
    decision_id = item_row.get("decision_id")
    item_id = str(item_row.get("item_id") or "").strip()
    if not item_id or decision_id is None:
        return False
    if any(
        (
            contract.get("version") != 2,
            str(contract.get("workspace_id") or "") != workspace_id,
            str(contract.get("item_id") or "") != item_id,
            str(contract.get("decision_id") or "") != str(decision_id),
            str(contract.get("policy_version") or "") != ELIGIBILITY_POLICY_VERSION,
            str(contract.get("template_id") or "")
            != str(receipt.get("action_type") or ""),
            str(contract.get("operation") or "") != "execute",
            str(receipt.get("item_id") or "") != item_id,
            str(receipt.get("decision_id") or "") != str(decision_id),
            contract.get("input_payload_digest")
            != action_contract_digest(_mapping(receipt.get("input"))),
        )
    ):
        return False
    runtime_item = _runtime_item(item_row, contract)
    if runtime_item is None:
        return False
    if business_observation_fingerprint(runtime_item) != str(
        contract.get("fingerprint") or ""
    ):
        return False
    try:
        expected_digests = runtime_action_digests(
            runtime_item,
            template_id=str(contract.get("template_id") or ""),
        )
    except ValueError:
        return False
    if any(contract.get(key) != value for key, value in expected_digests.items()):
        return False
    allowed_stages = (
        (WorkflowStage.EXECUTED,)
        if projection_status == "completed"
        else (WorkflowStage.APPROVED,)
    )
    return str(item_row.get("status") or "").lower() == "approved" and (
        workflow_has_eligible_provenance(
            _mapping(item_row.get("metadata")),
            runtime_item,
            decision_id=decision_id,
            allowed_stages=allowed_stages,
        )
    )


__all__ = (
    "authority_audit_matches_contract",
    "receipt_contract_matches",
    "reservation_authority_audit_valid",
    "reservation_stored_authority_audit_valid",
)
