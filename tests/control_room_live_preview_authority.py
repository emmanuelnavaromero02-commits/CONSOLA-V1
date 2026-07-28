from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import asyncpg

from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_item_persistence import persist_item_rows
from tests.control_room_live_action_binding_seed import persist_live_action_binding
from tests.test_control_room_live_postgres_workflows import _item, _rows, _scope


TEMPLATE_ID = "prepare_hcm_access_review"


@dataclass(frozen=True)
class PreviewScope:
    tenant_id: str
    workspace_id: str
    item_id: str
    user: dict[str, Any]
    item: dict[str, Any]
    binding_id: str


async def _enable_template(conn: asyncpg.Connection) -> None:
    template = ACTION_TEMPLATES[TEMPLATE_ID]
    await conn.execute(
        """
        INSERT INTO control_room_action_templates (
            template_id, cartridge_id, label, description, action_kind,
            risk_level, mode_default, requires_approval, config, enabled
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, '{}'::jsonb, TRUE)
        ON CONFLICT (template_id) DO UPDATE
        SET cartridge_id=EXCLUDED.cartridge_id,
            label=EXCLUDED.label,
            requires_approval=EXCLUDED.requires_approval,
            enabled=TRUE
        """,
        TEMPLATE_ID,
        template["cartridge_id"],
        template["label"],
        template["description"],
        template["action_kind"],
        template["risk_level"],
        template["mode_default"],
        template["requires_approval"],
    )


async def seed_preview_scopes(
    admin_dsn: str, console_dsn: str
) -> tuple[PreviewScope, PreviewScope]:
    conn = await asyncpg.connect(admin_dsn)
    seeded: list[tuple[str, str, str, dict[str, Any]]] = []
    try:
        await _enable_template(conn)
        item_id = f"preview-authority-{uuid4().hex}"
        for _ in range(2):
            tenant_id, workspace_id = await _scope(conn)
            item = _item(item_id, tenant_id, workspace_id)
            metadata = dict(item.get("metadata") or {})
            connection = dict(metadata.get("connection") or {})
            connection["writeback_path"] = "/writeback/A"
            item["metadata"] = {**metadata, "connection": connection}
            await persist_item_rows(
                conn,
                _rows([item], tenant_id, workspace_id),
                owner_scope_id=7,
            )
            await conn.execute(
                "UPDATE control_room_items SET metadata=jsonb_set("
                "metadata, '{connection}', $3::jsonb, true) "
                "WHERE workspace_id=$1::uuid AND item_id=$2",
                workspace_id,
                item_id,
                json.dumps(connection, sort_keys=True),
            )
            user = {
                "id": 7,
                "email": "workflow-owner@example.com",
                "role": "super_admin",
                "active_tenant_id": tenant_id,
                "active_workspace_id": workspace_id,
                "allowed_cartridges": ["*"],
            }
            seeded.append((tenant_id, workspace_id, item_id, user))
    finally:
        await conn.close()

    scopes: list[PreviewScope] = []
    for tenant_id, workspace_id, item_id, user in seeded:
        item, binding_id = await persist_live_action_binding(
            admin_dsn,
            console_dsn,
            user=user,
            item_id=item_id,
            template_id=TEMPLATE_ID,
        )
        scopes.append(
            PreviewScope(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                item_id=item_id,
                user=user,
                item=item,
                binding_id=binding_id,
            )
        )
    return scopes[0], scopes[1]


async def cleanup_preview_scopes(
    admin_dsn: str, scopes: tuple[PreviewScope, PreviewScope]
) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(
            "DELETE FROM workspaces WHERE id=ANY($1::uuid[])",
            [scope.workspace_id for scope in scopes],
        )
        await conn.execute(
            "DELETE FROM tenants WHERE id=ANY($1::uuid[])",
            [scope.tenant_id for scope in scopes],
        )
    finally:
        await conn.close()


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, dict) else {}


async def preview_snapshot(admin_dsn: str, scope: PreviewScope) -> dict[str, Any]:
    conn = await asyncpg.connect(admin_dsn)
    try:
        item = await conn.fetchrow(
            "SELECT entity_id, status, execution_status, metadata "
            "FROM control_room_items WHERE workspace_id=$1::uuid AND item_id=$2",
            scope.workspace_id,
            scope.item_id,
        )
        counts = await conn.fetchrow(
            "SELECT "
            "(SELECT count(*) FROM control_room_action_executions "
            " WHERE workspace_id=$1::uuid AND item_id=$2 "
            " AND mode IN ('preview','dry_run')) AS executions, "
            "(SELECT count(*) FROM action_runs WHERE workspace_id=$1::uuid "
            " AND item_id=$2 AND mode IN ('preview','dry_run')) AS runs, "
            "(SELECT count(*) FROM action_run_events e JOIN action_runs r "
            " ON r.id=e.action_run_id WHERE r.workspace_id=$1::uuid "
            " AND r.item_id=$2 AND r.mode IN ('preview','dry_run')) AS run_events, "
            "(SELECT count(*) FROM control_room_item_events "
            " WHERE workspace_id=$1::uuid AND item_id=$2 "
            " AND event_type IN ('action_preview','action_dry_run')) AS events, "
            "(SELECT count(*) FROM audit_events WHERE resource_id=$2 "
            " AND action IN ('control_room.action.preview',"
            "'control_room.action.dry_run') AND status='success' "
            " AND metadata->'authority_audit'->>'binding_id'=$3) AS audits",
            scope.workspace_id,
            scope.item_id,
            scope.binding_id,
        )
        runs = await conn.fetch(
            "SELECT mode, status, metadata FROM action_runs "
            "WHERE workspace_id=$1::uuid AND item_id=$2 "
            "AND mode IN ('preview','dry_run') ORDER BY id",
            scope.workspace_id,
            scope.item_id,
        )
    finally:
        await conn.close()
    item_data = dict(item)
    item_data["metadata"] = _json(item_data["metadata"])
    return {
        "item": item_data,
        "counts": dict(counts),
        "runs": [{**dict(row), "metadata": _json(row["metadata"])} for row in runs],
    }


async def mutate_preview_target(
    conn: asyncpg.Connection, scope: PreviewScope, *, target_kind: str
) -> dict[str, Any]:
    if target_kind == "entity":
        row = await conn.fetchrow(
            "UPDATE control_room_items SET entity_id=entity_id || '-B' "
            "WHERE workspace_id=$1::uuid AND item_id=$2 "
            "RETURNING entity_id, status, execution_status, metadata",
            scope.workspace_id,
            scope.item_id,
        )
    else:
        row = await conn.fetchrow(
            "UPDATE control_room_items SET metadata=jsonb_set("
            "metadata, '{connection,writeback_path}', to_jsonb($3::text), true) "
            "WHERE workspace_id=$1::uuid AND item_id=$2 "
            "RETURNING entity_id, status, execution_status, metadata",
            scope.workspace_id,
            scope.item_id,
            "/writeback/B",
        )
    data = dict(row)
    data["metadata"] = _json(data["metadata"])
    return data


async def backend_is_blocked(observer: asyncpg.Connection, pid: int) -> bool:
    for _ in range(200):
        if await observer.fetchval("SELECT cardinality(pg_blocking_pids($1)) > 0", pid):
            return True
        await asyncio.sleep(0.01)
    return False


__all__ = (
    "PreviewScope",
    "TEMPLATE_ID",
    "backend_is_blocked",
    "cleanup_preview_scopes",
    "mutate_preview_target",
    "preview_snapshot",
    "seed_preview_scopes",
)
