from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_action_key import effective_action_key
from app.services.control_room.business_action_digest import action_contract_digest
from app.services.control_room.business_action_runtime_contract import (
    runtime_action_digests,
)
from app.services.control_room.business_execution_target import (
    execution_reconciliation_context,
)
from app.services.control_room.business_reservation_errors import reservation_fetchrow
from app.services.control_room.business_workflow_provenance import (
    ELIGIBILITY_POLICY_VERSION,
    business_observation_fingerprint,
)


def action_reservation_contract(
    *,
    workspace_id: str,
    item: Mapping[str, Any],
    template_id: str,
    operation: str,
    authorization_contract: Mapping[str, Any] | None,
    input_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": 2,
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "workspace_id": workspace_id,
        "item_id": str(item.get("id") or item.get("item_id") or ""),
        "fingerprint": business_observation_fingerprint(item),
        "decision_id": item.get("decision_id"),
        "template_id": template_id,
        "operation": operation,
        "authorization": dict(authorization_contract or {}),
        "input_payload_digest": action_contract_digest(dict(input_payload or {})),
        "reconciliation_context": execution_reconciliation_context(item),
        **runtime_action_digests(item, template_id=template_id),
    }


def json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def canonical_json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)


async def matching_action_replay(
    conn: Any,
    *,
    workspace_id: str,
    item: Mapping[str, Any],
    template_id: str,
    operation: str,
    authorization_contract: Mapping[str, Any],
    input_payload: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    key = effective_action_key(
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
        operation=operation,
        input_payload=input_payload,
    )
    row = await reservation_fetchrow(
        conn,
        """
        SELECT * FROM action_runs
         WHERE workspace_id = $1::uuid AND idempotency_key = $2
        """,
        workspace_id,
        key,
    )
    if not row:
        return None
    contract = action_reservation_contract(
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
        operation=operation,
        authorization_contract=authorization_contract,
        input_payload=input_payload,
    )
    stored = json_mapping(row.get("metadata")).get("reservation_contract")
    if not isinstance(stored, Mapping) or canonical_json(stored) != canonical_json(
        contract
    ):
        return None
    return key, dict(row)


__all__ = (
    "action_reservation_contract",
    "canonical_json",
    "json_mapping",
    "matching_action_replay",
)
