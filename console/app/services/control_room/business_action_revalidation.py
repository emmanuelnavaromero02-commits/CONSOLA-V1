from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.services.control_room.business_action_authoritative_item import (
    AuthorityItemContract,
    contract_from_persisted_row,
)
from app.services.control_room.business_action_authority_repository import (
    fetch_authoritative_row_for_update,
)
from app.services.control_room.business_action_authorization_snapshot import (
    capture_authorization_snapshot,
)
from app.services.control_room.business_action_catalog import (
    require_enabled_action_template,
)
from app.services.control_room.business_action_dry_run_authority import (
    require_authority_dry_run,
)


async def actor_has_current_permission(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: int,
    permission: str,
) -> bool:
    # The DB-backed snapshot rechecks users.is_active and user_workspace_roles.
    return (
        await capture_authorization_snapshot(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=user_id,
            permission=permission,
        )
        is not None
    )


def _matches_intent(intent: Mapping[str, Any], contract: AuthorityItemContract) -> bool:
    expected = {
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


async def revalidate_intent(
    conn: Any, intent: Mapping[str, Any]
) -> AuthorityItemContract | None:
    expires_at = intent.get("expires_at")
    if not isinstance(expires_at, datetime) or expires_at.astimezone(
        UTC
    ) <= datetime.now(UTC):
        return None
    tenant_id = str(intent.get("tenant_id") or "")
    workspace_id = str(intent.get("workspace_id") or "")
    item_id = str(intent.get("item_id") or "")
    maker_user_id = int(intent.get("maker_user_id") or 0)
    if not all((tenant_id, workspace_id, item_id, maker_user_id)):
        return None
    try:
        await require_enabled_action_template(conn, "create_followup_task")
        authorization = await capture_authorization_snapshot(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=maker_user_id,
            permission="control_room.write",
        )
        if authorization is None:
            return None
        row = await fetch_authoritative_row_for_update(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item_id=item_id,
        )
    except Exception:
        return None
    contract = (
        contract_from_persisted_row(row, authorization=authorization)
        if row is not None
        else None
    )
    if contract is None or not _matches_intent(intent, contract):
        return None
    try:
        await require_authority_dry_run(
            conn,
            user={
                "active_tenant_id": tenant_id,
                "active_workspace_id": workspace_id,
            },
            contract=contract,
            action_run_id=int(intent.get("dry_run_action_run_id") or 0),
            expected_evidence_digest=str(intent.get("dry_run_evidence_digest") or ""),
            expected_intent_id=str(intent.get("id") or ""),
        )
    except Exception:
        return None
    return contract


__all__ = ("actor_has_current_permission", "revalidate_intent")
