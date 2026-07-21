from __future__ import annotations

import json

import asyncpg
import pytest

from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    business_observation_fingerprint,
)
from tests.test_control_room_live_postgres_workflows import (
    AsyncNoop,
    _item,
    _rows,
    _scope,
)
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


@pytest.mark.asyncio
async def test_live_workflow_generation_rearm_is_explicit_and_transactional(
    postgres_with_real_init_schema: str,
) -> None:
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id, workspace_id = await _scope(conn)
        observation_a = _item("generation-item", tenant_id, workspace_id)
        observation_b = {**observation_a, "observed_value": 2}
        await persist_item_rows(
            conn,
            _rows([observation_a], tenant_id, workspace_id),
            owner_scope_id=7,
        )
        async with conn.transaction():
            decision_a = await create_and_link_decision(
                conn,
                user={"id": 7, "email": "workflow-owner@example.com"},
                item={**observation_a, "owner_user_id": 7},
                workspace_id=workspace_id,
                ensure_item_row=AsyncNoop(),
                record_item_event=AsyncNoop(),
            )
        await conn.execute(
            "INSERT INTO control_room_item_events "
            "(tenant_id, workspace_id, item_id, event_type, metadata) "
            "VALUES ($1, $2, $3, 'decision_created', $4::jsonb)",
            tenant_id,
            workspace_id,
            observation_a["id"],
            json.dumps({"decision_id": decision_a["id"]}),
        )

        for _ in range(3):
            await persist_item_rows(
                conn,
                _rows([observation_b], tenant_id, workspace_id),
                owner_scope_id=7,
            )
            assert (
                await conn.fetchval(
                    "SELECT decision_id FROM control_room_items "
                    "WHERE workspace_id=$1 AND item_id=$2",
                    workspace_id,
                    observation_b["id"],
                )
                is None
            )

        assert await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM decisions WHERE id=$1)", decision_a["id"]
        )
        async with conn.transaction():
            decision_b = await create_and_link_decision(
                conn,
                user={"id": 7, "email": "workflow-owner@example.com"},
                item={**observation_b, "owner_user_id": 7},
                workspace_id=workspace_id,
                ensure_item_row=AsyncNoop(),
                record_item_event=AsyncNoop(),
            )

        stored = await conn.fetchrow(
            "SELECT decision_id, metadata FROM control_room_items "
            "WHERE workspace_id=$1 AND item_id=$2",
            workspace_id,
            observation_b["id"],
        )
        metadata = json.loads(stored["metadata"])
        provenance = metadata[DECISION_PROVENANCE_KEY]
        generations = metadata[WORKFLOW_QUARANTINE_KEY]["generations"]
        assert stored["decision_id"] == decision_b["id"]
        assert decision_b["id"] != decision_a["id"]
        assert provenance["fingerprint"] == business_observation_fingerprint(
            observation_b
        )
        assert provenance["decision_id"] == decision_b["id"]
        assert provenance["stage"] == "decision_created"
        assert provenance["reason"] == "explicit_decision_creation"
        assert provenance["linked_at"]
        assert len(generations) == 1
        assert generations[0]["decision_id"] == decision_a["id"]
        assert generations[0]["fingerprint"] == business_observation_fingerprint(
            observation_a
        )
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM control_room_item_events "
                "WHERE workspace_id=$1 AND item_id=$2 AND event_type='decision_created'",
                workspace_id,
                observation_a["id"],
            )
            == 1
        )
        await persist_item_rows(
            conn,
            _rows([observation_b], tenant_id, workspace_id),
            owner_scope_id=7,
        )
        assert (
            await conn.fetchval(
                "SELECT decision_id FROM control_room_items "
                "WHERE workspace_id=$1 AND item_id=$2",
                workspace_id,
                observation_b["id"],
            )
            == decision_b["id"]
        )
    finally:
        await conn.close()
