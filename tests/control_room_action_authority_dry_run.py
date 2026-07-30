from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import asyncpg

from app.services.control_room.business_execution_precondition import dry_run_metadata
from tests.control_room_action_authority_live import AuthorityScope, AuthoritySeed


async def insert_authority_dry_run(
    seed: AuthoritySeed,
    scope: AuthorityScope,
    *,
    actor_id: int | None = None,
    decision_id: int | None = None,
    action_type: str = "create_followup_task",
    status: str = "dry_run_completed",
    result: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> int:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        return int(
            await conn.fetchval(
                """
                INSERT INTO action_runs (
                    tenant_id, workspace_id, item_id, decision_id, action_type,
                    adapter_name, mode, status, idempotency_key, actor_id,
                    actor_email, input, dry_run_result, metadata, completed_at
                ) VALUES (
                    $1::uuid, $2::uuid, $3, $4, $5,
                    'internal_followup_task', 'dry_run', $6, $7, $8,
                    'authority-maker@example.test', '{}'::jsonb, $9::jsonb,
                    $10::jsonb, NOW()
                ) RETURNING id
                """,
                scope.tenant_id,
                scope.workspace_id,
                scope.item_id,
                decision_id
                if decision_id is not None
                else int(scope.item["decision_id"]),
                action_type,
                status,
                f"authority-dry-run:{uuid4().hex}",
                actor_id if actor_id is not None else int(scope.maker["id"]),
                json.dumps(dict(result or {"ok": True, "validated": True})),
                json.dumps(
                    dict(
                        metadata
                        if metadata is not None
                        else dry_run_metadata(
                            scope.item, template_id="create_followup_task"
                        )
                    )
                ),
            )
        )
    finally:
        await conn.close()


__all__ = ("insert_authority_dry_run",)
