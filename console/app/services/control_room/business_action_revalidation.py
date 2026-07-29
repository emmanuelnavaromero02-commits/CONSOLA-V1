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
from app.services.control_room.business_action_catalog import (
    require_enabled_action_template,
)
from app.services.permissions import has_permission


async def actor_has_current_permission(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: int,
    permission: str,
) -> bool:
    rows = await conn.fetch(
        """
        SELECT users.role, users.is_active, users.tenant_id::text AS tenant_id,
               roles.name AS workspace_role
          FROM users
          LEFT JOIN user_workspace_roles AS membership
            ON membership.user_id = users.id
           AND membership.workspace_id = $2::uuid
          LEFT JOIN roles ON roles.id = membership.role_id
         WHERE users.id = $3
           AND (users.tenant_id = $1::uuid OR users.role IN ('owner','super_admin','admin'))
        """,
        tenant_id,
        workspace_id,
        int(user_id),
    )
    for row in rows:
        if row.get("is_active") is not True:
            continue
        role = str(row.get("role") or "")
        scoped_role = row.get("workspace_role")
        if scoped_role is None and role not in {"owner", "super_admin", "admin"}:
            continue
        user = {
            "id": user_id,
            "role": role,
            "workspace_role": scoped_role or "workspace_admin",
            "active_tenant_id": tenant_id,
            "active_workspace_id": workspace_id,
        }
        if has_permission(user, permission):
            return True
    return False


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
        row = await fetch_authoritative_row_for_update(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item_id=item_id,
        )
    except Exception:
        return None
    contract = (
        contract_from_persisted_row(row, maker_user_id=maker_user_id)
        if row is not None
        else None
    )
    return (
        contract if contract is not None and _matches_intent(intent, contract) else None
    )


__all__ = ("actor_has_current_permission", "revalidate_intent")
