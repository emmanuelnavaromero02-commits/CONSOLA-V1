from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.services.control_room.business_action_digest import action_contract_digest
from app.services.permission_roles import ROLE_PERMISSIONS


@dataclass(frozen=True, repr=False)
class AuthorizationSnapshot:
    permission: str
    global_role: str
    workspace_role: str | None
    grant_source: str
    access_revision_digest: str
    rbac_policy_digest: str
    snapshot: dict[str, Any]


async def capture_authorization_snapshot(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: int,
    permission: str,
) -> AuthorizationSnapshot | None:
    user = await conn.fetchrow(
        """
        SELECT id, role, is_active, tenant_id::text AS tenant_id, created_at
          FROM users
         WHERE id = $1
           AND (tenant_id = $2::uuid OR role IN ('owner','super_admin','admin'))
         FOR SHARE
        """,
        int(actor_user_id),
        tenant_id,
    )
    if not user or user.get("is_active") is not True:
        return None
    memberships = await conn.fetch(
        """
        SELECT membership.role_id, membership.created_at, roles.name
          FROM user_workspace_roles AS membership
          JOIN roles ON roles.id = membership.role_id
         WHERE membership.user_id = $1
           AND membership.workspace_id = $2::uuid
         ORDER BY roles.name, membership.role_id
         FOR SHARE OF membership, roles
        """,
        int(actor_user_id),
        workspace_id,
    )
    global_role = str(user.get("role") or "")
    workspace_roles = [str(row["name"]) for row in memberships]
    global_grant = permission in ROLE_PERMISSIONS.get(global_role, set())
    workspace_grants = [
        role
        for role in workspace_roles
        if permission in ROLE_PERMISSIONS.get(role, set())
    ]
    if not global_grant and not workspace_grants:
        return None
    granting_workspace_role = workspace_grants[0] if workspace_grants else None
    recorded_workspace_role = granting_workspace_role or (
        workspace_roles[0] if workspace_roles else None
    )
    grant_source = "workspace_role" if granting_workspace_role else "global_role"
    access_revision = {
        "actor_user_id": int(actor_user_id),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "global_role": global_role,
        "user_tenant_id": str(user.get("tenant_id") or ""),
        "user_created_at": user.get("created_at"),
        "memberships": [
            {
                "role_id": int(row["role_id"]),
                "role": str(row["name"]),
                "created_at": row.get("created_at"),
            }
            for row in memberships
        ],
    }
    rbac_policy = {
        "version": "control-room-authority-rbac/v1",
        "permission": permission,
        "roles": {
            role: sorted(ROLE_PERMISSIONS.get(role, set()))
            for role in sorted({global_role, *workspace_roles})
        },
    }
    access_digest = action_contract_digest(access_revision)
    policy_digest = action_contract_digest(rbac_policy)
    snapshot = {
        "version": "control-room-authorization-snapshot/v1",
        "actor_user_id": int(actor_user_id),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "permission": permission,
        "global_role": global_role,
        "workspace_roles": workspace_roles,
        "granting_workspace_role": granting_workspace_role,
        "grant_source": grant_source,
        "access_revision_digest": access_digest,
        "rbac_policy_digest": policy_digest,
    }
    json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return AuthorizationSnapshot(
        permission,
        global_role,
        recorded_workspace_role,
        grant_source,
        access_digest,
        policy_digest,
        snapshot,
    )


__all__ = ("AuthorizationSnapshot", "capture_authorization_snapshot")
