from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import workspace_scope
from app.services.control_room.business_action_replay import (
    action_reservation_contract,
    canonical_json,
)
from app.services.control_room.business_action_runtime_contract import (
    runtime_action_digests,
)
from app.services.control_room.business_reservation_lease import (
    reservation_lease_token,
)
from app.services.control_room.cache_identity import authorization_cache_identity
from app.services.control_room.business_workflow_provenance import (
    ELIGIBILITY_POLICY_VERSION,
    business_observation_fingerprint,
)


DRY_RUN_CONTRACT_KEY = "business_dry_run_contract"


def dry_run_contract(item: Mapping[str, Any], *, template_id: str) -> dict[str, Any]:
    return {
        "version": 2,
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "item_id": str(item.get("id") or item.get("item_id") or "").strip(),
        "decision_id": item.get("decision_id"),
        "template_id": str(template_id or "").strip(),
        "fingerprint": business_observation_fingerprint(item),
        **runtime_action_digests(item, template_id=template_id),
    }


def dry_run_metadata(item: Mapping[str, Any], *, template_id: str) -> dict[str, Any]:
    return {DRY_RUN_CONTRACT_KEY: dry_run_contract(item, template_id=template_id)}


def _missing_dry_run() -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "matching_dry_run_required",
            "message": "a matching successful dry-run is required before execution",
        },
    )


def _business_state_changed() -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "item_business_state_changed",
            "message": "control room item changed; reload before mutating",
        },
    )


def execution_authorization_contract(user: Mapping[str, Any]) -> dict[str, Any]:
    identity = authorization_cache_identity(dict(user))
    permissions = tuple(identity.effective_permissions)
    if not {"control_room.write", "control_room.execute"}.issubset(permissions):
        raise HTTPException(403, "control room execution permission required")
    return {
        "tenant_id": identity.tenant_id,
        "workspace_id": identity.workspace_id,
        "global_role": identity.global_role,
        "workspace_role": identity.workspace_role,
        "user_id": identity.user_id,
        "effective_permissions": list(permissions),
        "allowed_cartridges": list(identity.allowed_cartridges),
        "access_revisions": [list(value) for value in identity.access_revisions],
    }


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


async def lock_pending_action_reservation(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    template_id: str,
    reservation_id: int,
    effective_key: str,
    input_payload: Mapping[str, Any] | None = None,
    authority_audit: Mapping[str, Any] | None = None,
    lease_token: str | None = None,
) -> dict[str, Any]:
    _tenant_id, workspace_id = workspace_scope(user)
    row = await conn.fetchrow(
        """
        SELECT id, status, idempotency_key, metadata, updated_at
          FROM action_runs
         WHERE workspace_id = $1::uuid
           AND id = $2
           AND idempotency_key = $3
         FOR UPDATE
        """,
        workspace_id,
        int(reservation_id),
        effective_key,
    )
    if not row or str(row.get("status") or "") != "pending":
        raise HTTPException(409, "action reservation is no longer pending")
    if lease_token is not None and (
        not lease_token or reservation_lease_token(row) != lease_token
    ):
        raise _business_state_changed()
    metadata = _mapping(row.get("metadata"))
    stored = metadata.get("reservation_contract")
    expected = action_reservation_contract(
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
        operation="execute",
        authorization_contract=execution_authorization_contract(user),
        input_payload=input_payload,
    )
    if not isinstance(stored, Mapping) or canonical_json(stored) != canonical_json(
        expected
    ):
        raise _business_state_changed()
    if authority_audit is not None:
        stored_authority = metadata.get("authority_audit")
        if not isinstance(stored_authority, Mapping) or canonical_json(
            stored_authority
        ) != canonical_json(authority_audit):
            raise _business_state_changed()
    return dict(row)


async def require_matching_dry_run(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    template_id: str,
) -> dict[str, Any]:
    _tenant_id, workspace_id = workspace_scope(user)
    contract = dry_run_contract(item, template_id=template_id)
    if not all(
        contract.get(key)
        for key in ("item_id", "decision_id", "template_id", "fingerprint")
    ):
        raise _missing_dry_run()
    row = await conn.fetchrow(
        """
        SELECT id, metadata, dry_run_result
          FROM action_runs
         WHERE workspace_id = $1::uuid
           AND item_id = $2
           AND decision_id = $3
           AND action_type = $4
           AND mode = 'dry_run'
           AND status = 'dry_run_completed'
           AND metadata -> $5 = $6::jsonb
           AND dry_run_result ->> 'ok' = 'true'
           AND dry_run_result ->> 'validated' = 'true'
         ORDER BY completed_at DESC NULLS LAST, updated_at DESC
         LIMIT 1
         FOR SHARE
        """,
        workspace_id,
        contract["item_id"],
        int(contract["decision_id"]),
        contract["template_id"],
        DRY_RUN_CONTRACT_KEY,
        json.dumps(contract, sort_keys=True, separators=(",", ":")),
    )
    if not row:
        raise _missing_dry_run()
    return dict(row)


__all__ = (
    "DRY_RUN_CONTRACT_KEY",
    "dry_run_contract",
    "dry_run_metadata",
    "execution_authorization_contract",
    "lock_pending_action_reservation",
    "require_matching_dry_run",
)
