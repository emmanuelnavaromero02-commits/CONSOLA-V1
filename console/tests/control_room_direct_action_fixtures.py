from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.control_room.business_action_authorization_snapshot import (
    AuthorizationSnapshot,
)
from app.services.control_room.business_policy_metadata import business_policy_metadata
from app.services.control_room.business_workflow_provenance import (
    persistence_metadata,
)
from control_room_surface_fixtures import (
    OPERATOR,
    TENANT_ID,
    WORKSPACE_ID,
    business_item,
)


ACCESS_DIGEST = "a" * 64
POLICY_DIGEST = "b" * 64
STUDIO_ADMIN = {
    "id": 9,
    "role": "admin",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
    "allowed_cartridges": ["sap_hcm"],
}


def direct_row(item: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    policy_item = {
        **item,
        "metadata": business_policy_metadata(item.get("metadata"), item),
    }
    metadata = persistence_metadata(policy_item)
    metadata["evidence_refs"] = deepcopy(item.get("evidence_refs") or [])
    metadata.update(overrides.pop("metadata_updates", {}))
    row: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "owner_user_id": 9,
        "item_id": item["id"],
        "cartridge_id": item.get("cartridge"),
        "domain": item.get("domain"),
        "source_dataset": item.get("source_dataset"),
        "item_kind": item.get("kind"),
        "title": item.get("title"),
        "severity": item.get("severity"),
        "status": item.get("status") or "open",
        "decision_id": None,
        "entity_kind": item.get("entity_kind"),
        "entity_id": item.get("entity_id"),
        "entity_label": item.get("entity_label"),
        "anomaly_type": item.get("anomaly_type"),
        "metadata": metadata,
        "selected_option_id": None,
        "execution_status": "not_started",
        "decision_workspace_id": None,
    }
    row.update(overrides)
    return row


def authorization(
    user: dict[str, Any] = OPERATOR, **overrides: Any
) -> AuthorizationSnapshot:
    values: dict[str, Any] = {
        "actor_user_id": int(user["id"]),
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "permission": "control_room.write",
        "global_role": str(user.get("role") or ""),
        "workspace_role": None,
        "workspace_wide": True,
        "grant_source": "global_role",
        "access_revision_digest": ACCESS_DIGEST,
        "rbac_policy_digest": POLICY_DIGEST,
        "snapshot": {},
    }
    values.update(overrides)
    return AuthorizationSnapshot(**values)


def exception_item(item_id: str = "business-1", **updates: Any) -> dict[str, Any]:
    return business_item(item_id, **{"status": "dismissed", **updates})


def exception_row(item: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    metadata_updates = {
        "resolution": "exception_approved",
        "resolution_actor_id": 9,
        "resolution_reason": "Proveedor validado por auditoría interna",
        "resolution_at": "2026-09-25T10:00:00+00:00",
        **overrides.pop("metadata_updates", {}),
    }
    return direct_row(
        item,
        metadata_updates=metadata_updates,
        **{"status": "dismissed", **overrides},
    )


__all__ = (
    "ACCESS_DIGEST",
    "POLICY_DIGEST",
    "STUDIO_ADMIN",
    "authorization",
    "direct_row",
    "exception_item",
    "exception_row",
)
