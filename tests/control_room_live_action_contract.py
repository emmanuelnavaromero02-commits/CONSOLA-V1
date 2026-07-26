from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.services import control_room_service
from app.services.control_room.business_command_item import (
    load_persisted_command_item,
)
from app.services.control_room.business_explicit_action_binding import (
    attach_explicit_action_binding,
)
from app.services.control_room.business_policy_metadata import business_policy_metadata
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import persistence_metadata


ITEM_ID = "shared-live-business-item"
TEMPLATE_ID = "request_owner_review"
_PRIVATE_ACTION_KEYS = {
    "item_id",
    "template_id",
    "binding",
    "binding_id",
    "tenant_id",
    "workspace_id",
    "dataset",
    "system",
    "fingerprint",
    "policy_version",
    "producer",
    "provenance",
}


@dataclass(frozen=True)
class LiveActionScopes:
    admin_dsn: str
    console_dsn: str
    tenant_ids: tuple[str, str]
    workspace_ids: tuple[str, str]
    user_ids: tuple[int, int]
    items: tuple[dict[str, Any], dict[str, Any]]
    users: tuple[dict[str, Any], dict[str, Any]]


def assert_public_action_redacted(action: dict[str, Any]) -> None:
    assert set(action) == {
        "action_handle",
        "label",
        "operation",
        "enabled",
        "requires_approval",
        "prerequisites",
        "method",
        "endpoint",
    }
    assert len(action["action_handle"]) == 64
    assert action["endpoint"] == "/api/control-room/actions/preview"

    def private_keys(value: object) -> set[str]:
        if isinstance(value, dict):
            found = _PRIVATE_ACTION_KEYS.intersection(value)
            for nested in value.values():
                found.update(private_keys(nested))
            return found
        if isinstance(value, list):
            found: set[str] = set()
            for nested in value:
                found.update(private_keys(nested))
            return found
        return set()

    assert private_keys(action) == set()


def _business_item(
    *, tenant_id: str, workspace_id: str, owner_user_id: int
) -> dict[str, Any]:
    item = {
        "id": ITEM_ID,
        "kind": "anomaly",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "owner_user_id": owner_user_id,
        "cartridge": "sap_hcm",
        "source_system": "sap_hcm",
        "source_dataset": "gold_people",
        "domain": "People",
        "module": "People",
        "title": "Headcount variance requires review",
        "severity": "high",
        "status": "open",
        "entity_kind": "department",
        "entity_id": "department-1",
        "entity_label": "Department 1",
        "anomaly_type": "headcount_variance",
        "data_status": "ready",
        "metric_type": "count",
        "observed_value": 3,
        "population_count": 10,
        "detected_at": "2026-07-25T10:00:00Z",
    }
    item.update(
        runtime_row_evidence_fields(
            source_dataset="gold_people",
            source_system="sap_hcm",
            cartridge="sap_hcm",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source_row=dict(item),
            locator_field="id",
            observed_at=item["detected_at"],
            business_observation=item,
        )
    )
    return item


def _persisted_metadata(item: dict[str, Any]) -> dict[str, Any]:
    policy_item = {
        **item,
        "metadata": business_policy_metadata(item.get("metadata"), item),
    }
    return persistence_metadata(policy_item)


async def seed_live_action_scopes(admin_dsn: str, console_dsn: str) -> LiveActionScopes:
    conn = await asyncpg.connect(admin_dsn)
    suffix = uuid.uuid4().hex
    try:
        await conn.execute(
            "INSERT INTO cartridges (id, name, category) "
            "VALUES ('sap_hcm', 'SAP HCM', 'hr') ON CONFLICT (id) DO NOTHING"
        )
        tenants = []
        workspaces = []
        users = []
        for index in (1, 2):
            tenant = await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
                f"Action tenant {index} {suffix}",
                f"action-tenant-{index}-{suffix}",
            )
            workspace = await conn.fetchval(
                "INSERT INTO workspaces (tenant_id, name) VALUES ($1, $2) RETURNING id",
                tenant,
                f"Action workspace {index} {suffix}",
            )
            user_id = await conn.fetchval(
                "INSERT INTO users (email, password_hash, role) "
                "VALUES ($1, 'test', 'admin') RETURNING id",
                f"action-{index}-{suffix}@example.test",
            )
            tenants.append(str(tenant))
            workspaces.append(str(workspace))
            users.append(int(user_id))

        items = tuple(
            _business_item(
                tenant_id=tenants[index],
                workspace_id=workspaces[index],
                owner_user_id=users[index],
            )
            for index in range(2)
        )
        for item in items:
            await conn.execute(
                """
                INSERT INTO control_room_items (
                    tenant_id, workspace_id, owner_user_id, item_id,
                    cartridge_id, domain, source_dataset, item_kind, title,
                    severity, status, entity_id, entity_label, anomaly_type,
                    entity_kind, metadata
                ) VALUES (
                    $1::uuid, $2::uuid, $3, $4, 'sap_hcm', 'People',
                    'gold_people', 'anomaly', $5, 'high', 'open',
                    'department-1', 'Department 1', 'headcount_variance',
                    'department', $6::jsonb
                )
                """,
                item["tenant_id"],
                item["workspace_id"],
                item["owner_user_id"],
                ITEM_ID,
                item["title"],
                json.dumps(_persisted_metadata(item), sort_keys=True),
            )
        user_payloads = tuple(
            {
                "id": users[index],
                "email": f"action-{index}-{suffix}@example.test",
                "role": "super_admin",
                "active_tenant_id": tenants[index],
                "active_workspace_id": workspaces[index],
                "allowed_cartridges": ["sap_hcm"],
            }
            for index in range(2)
        )
        pool = await asyncpg.create_pool(console_dsn, min_size=1, max_size=2)
        try:
            bound_items = []
            for index, user in enumerate(user_payloads):
                loaded = await load_live_action_item(pool, user)
                if loaded is None:
                    raise RuntimeError("seeded live action item was not readable")
                bound = attach_explicit_action_binding(
                    loaded,
                    template_id=TEMPLATE_ID,
                )
                await conn.execute(
                    "UPDATE control_room_items SET metadata=$3::jsonb "
                    "WHERE tenant_id=$1::uuid AND workspace_id=$2::uuid "
                    "AND item_id=$4",
                    tenants[index],
                    workspaces[index],
                    json.dumps(_persisted_metadata(bound), sort_keys=True),
                    ITEM_ID,
                )
                bound_items.append(bound)
            items = tuple(bound_items)
        finally:
            await pool.close()
        return LiveActionScopes(
            admin_dsn=admin_dsn,
            console_dsn=console_dsn,
            tenant_ids=tuple(tenants),
            workspace_ids=tuple(workspaces),
            user_ids=tuple(users),
            items=items,
            users=user_payloads,
        )
    finally:
        await conn.close()


async def cleanup_live_action_scopes(scopes: LiveActionScopes) -> None:
    conn = await asyncpg.connect(scopes.admin_dsn)
    try:
        await conn.execute(
            "DELETE FROM workspaces WHERE id = ANY($1::uuid[])",
            list(scopes.workspace_ids),
        )
        await conn.execute(
            "DELETE FROM tenants WHERE id = ANY($1::uuid[])", list(scopes.tenant_ids)
        )
        await conn.execute(
            "DELETE FROM users WHERE id = ANY($1::bigint[])", list(scopes.user_ids)
        )
        await conn.execute(
            "UPDATE control_room_action_templates SET enabled=TRUE "
            "WHERE template_id=$1",
            TEMPLATE_ID,
        )
    finally:
        await conn.close()


async def load_live_action_item(pool, user: dict[str, Any]) -> dict[str, Any] | None:
    async def pool_factory():
        return pool

    return await load_persisted_command_item(
        ITEM_ID,
        user,
        pool_factory=pool_factory,
        run_scoped=control_room_service._run_with_db_scope,  # noqa: SLF001
        item_statuses=control_room_service.ITEM_STATUSES,
        severity_weights=control_room_service.SEVERITY_WEIGHT,
    )


__all__ = (
    "ITEM_ID",
    "TEMPLATE_ID",
    "LiveActionScopes",
    "assert_public_action_redacted",
    "cleanup_live_action_scopes",
    "load_live_action_item",
    "seed_live_action_scopes",
)
