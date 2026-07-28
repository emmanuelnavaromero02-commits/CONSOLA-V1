from __future__ import annotations

import json

import asyncpg
import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_key import (
    legacy_effective_action_key_v1,
)
from app.services.control_room.business_action_reservation import (
    acquire_guarded_action_reservation,
)
from app.services.db_scope import SET_SCOPE_SQL
from tests.test_control_room_live_postgres_p15 import (
    _linked_item,
    _mutation_item,
    _seed_matching_dry_run,
    _user,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


@pytest.mark.asyncio
async def test_live_v1_reservation_blocks_new_v2_execution(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
):
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema, "legacy-reservation-quarantine"
    )
    template_id = "prepare_hcm_access_review"
    await _seed_matching_dry_run(
        postgres_with_real_init_schema,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
    )
    legacy_key = legacy_effective_action_key_v1(
        workspace_id=str(workspace_id),
        item=_mutation_item(item),
        template_id=template_id,
        operation="execute",
    )
    setup = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await setup.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
        await setup.execute(
            """
            INSERT INTO action_runs (
                tenant_id, workspace_id, item_id, decision_id, action_type,
                adapter_name, mode, status, idempotency_key, input, metadata
            )
            VALUES ($1, $2, $3, $4, $5,
                    'LegacyAdapter', 'execute', 'completed', $6,
                    '{}'::jsonb, $7::jsonb)
            """,
            tenant_id,
            workspace_id,
            item["id"],
            int(item["decision_id"]),
            template_id,
            legacy_key,
            json.dumps({"reservation_contract": {"version": 1}}),
        )
    finally:
        await setup.close()

    scoped = await asyncpg.connect(omega_console_live_dsn)
    try:
        async with scoped.transaction():
            await scoped.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
            with pytest.raises(HTTPException) as exc:
                await acquire_guarded_action_reservation(
                    scoped,
                    user=_user(tenant_id, workspace_id),
                    item=_mutation_item(item),
                    template_id=template_id,
                    adapter_name="NewAdapter",
                    operation="execute",
                    input_payload={"mode": "execute_live"},
                )
    finally:
        await scoped.close()

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == (
        "legacy_action_reservation_requires_reconciliation"
    )
    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        rows = await check.fetch(
            "SELECT idempotency_key FROM action_runs "
            "WHERE workspace_id=$1 AND item_id=$2 AND mode='execute'",
            workspace_id,
            item["id"],
        )
    finally:
        await check.close()
    assert [row["idempotency_key"] for row in rows] == [legacy_key]
