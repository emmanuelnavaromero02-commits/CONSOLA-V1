from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.services import control_room_service as service
from app.services.control_room.business_command_item import (
    load_persisted_command_item,
)
from app.services.control_room.business_explicit_action_binding import (
    attach_explicit_action_binding,
)
from tests.test_control_room_live_postgres_p15 import (
    _linked_item,
    _seed_matching_dry_run,
    _user,
)


@dataclass(frozen=True)
class AuthoritativeScope:
    tenant_id: str
    workspace_id: str
    item_id: str
    user: dict[str, Any]
    item: dict[str, Any]
    binding_id: str


async def _load_item(
    pool: asyncpg.Pool,
    *,
    item_id: str,
    user: dict[str, Any],
) -> dict[str, Any]:
    async def pool_factory():
        return pool

    item = await load_persisted_command_item(
        item_id,
        user,
        pool_factory=pool_factory,
        run_scoped=service._run_with_db_scope,  # noqa: SLF001
        item_statuses=service.ITEM_STATUSES,
        severity_weights=service.SEVERITY_WEIGHT,
    )
    if item is None:
        raise AssertionError("seeded authoritative item was not readable")
    return item


async def seed_authoritative_scope(
    admin_dsn: str,
    pool: asyncpg.Pool,
    *,
    item_id: str,
    template_id: str,
) -> AuthoritativeScope:
    tenant_id, workspace_id, _item = await _linked_item(admin_dsn, item_id)
    user = {
        **_user(tenant_id, workspace_id),
        "role": "super_admin",
        "allowed_cartridges": ["*"],
    }
    if template_id == "prepare_hcm_access_review":
        conn = await asyncpg.connect(admin_dsn)
        try:
            await conn.execute(
                "UPDATE control_room_items SET metadata=jsonb_set("
                "metadata, '{connection}', $3::jsonb, true) "
                "WHERE workspace_id=$1::uuid AND item_id=$2",
                workspace_id,
                item_id,
                json.dumps(
                    {
                        "base_url": "https://sap.example.test",
                        "writeback_path": "/writeback/A",
                    }
                ),
            )
        finally:
            await conn.close()
    loaded = await _load_item(pool, item_id=item_id, user=user)
    bound = attach_explicit_action_binding(loaded, template_id=template_id)
    binding = next(
        value
        for value in bound["metadata"]["explicit_action_bindings"]
        if value["template_id"] == template_id
    )
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(
            "UPDATE control_room_items SET metadata=$3::jsonb "
            "WHERE workspace_id=$1::uuid AND item_id=$2",
            workspace_id,
            item_id,
            json.dumps(bound["metadata"], sort_keys=True),
        )
    finally:
        await conn.close()
    await _seed_matching_dry_run(
        admin_dsn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item=bound,
        template_id=template_id,
    )
    current = await _load_item(pool, item_id=item_id, user=user)
    return AuthoritativeScope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item_id=item_id,
        user=user,
        item=current,
        binding_id=str(binding["binding_id"]),
    )


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, dict) else {}


async def authoritative_snapshot(dsn: str, scope: AuthoritativeScope) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT entity_id, status, execution_status, decision_id, metadata "
            "FROM control_room_items WHERE workspace_id=$1::uuid AND item_id=$2",
            scope.workspace_id,
            scope.item_id,
        )
        counts = await conn.fetchrow(
            "SELECT "
            "(SELECT count(*) FROM action_runs WHERE workspace_id=$1::uuid "
            " AND item_id=$2 AND mode='execute') AS runs, "
            "(SELECT count(*) FROM action_run_events e JOIN action_runs r "
            " ON r.id=e.action_run_id WHERE r.workspace_id=$1::uuid "
            " AND r.item_id=$2 AND r.mode='execute') AS run_events, "
            "(SELECT count(*) FROM control_room_item_events "
            " WHERE workspace_id=$1::uuid AND item_id=$2) AS events, "
            "(SELECT count(*) FROM audit_events WHERE resource_id=$2 "
            " AND action='control_room.action.execute' AND status='success') AS audits, "
            "(SELECT count(*) FROM control_room_action_executions "
            " WHERE workspace_id=$1::uuid AND item_id=$2 "
            " AND mode='execute_live') AS executions, "
            "(SELECT count(*) FROM decision_actions WHERE decision_id=$3) AS effects",
            scope.workspace_id,
            scope.item_id,
            int(scope.item["decision_id"]),
        )
        run_rows = await conn.fetch(
            "SELECT status, error_code, "
            "metadata->'remote_attempt'->>'status' AS remote_attempt "
            "FROM action_runs WHERE workspace_id=$1::uuid AND item_id=$2 "
            "AND mode='execute' ORDER BY id",
            scope.workspace_id,
            scope.item_id,
        )
    finally:
        await conn.close()
    item = dict(row)
    item["metadata"] = _json(item["metadata"])
    return {
        "item": item,
        "counts": dict(counts),
        "runs": [dict(value) for value in run_rows],
    }


async def mutate_target(
    conn: asyncpg.Connection,
    scope: AuthoritativeScope,
    *,
    target_kind: str,
) -> dict[str, Any]:
    if target_kind == "entity":
        row = await conn.fetchrow(
            "UPDATE control_room_items SET entity_id=entity_id || '-B' "
            "WHERE workspace_id=$1::uuid AND item_id=$2 "
            "RETURNING entity_id, status, execution_status, decision_id, metadata",
            scope.workspace_id,
            scope.item_id,
        )
    else:
        row = await conn.fetchrow(
            "UPDATE control_room_items SET metadata=jsonb_set("
            "metadata, '{connection,writeback_path}', to_jsonb($3::text), true) "
            "WHERE workspace_id=$1::uuid AND item_id=$2 "
            "RETURNING entity_id, status, execution_status, decision_id, metadata",
            scope.workspace_id,
            scope.item_id,
            "/writeback/B",
        )
    result = dict(row)
    result["metadata"] = _json(result["metadata"])
    return result


__all__ = (
    "AuthoritativeScope",
    "authoritative_snapshot",
    "mutate_target",
    "seed_authoritative_scope",
)
