from __future__ import annotations

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import control_room_service as service
from app.services.control_room.business_workflow_stage_cas import (
    select_option_with_stage_cas,
)
from tests.test_control_room_live_postgres_p15 import _linked_item, _user
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


@pytest.mark.asyncio
async def test_live_terminal_status_cannot_degrade_or_reopen_option(
    postgres_with_real_init_schema: str,
) -> None:
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema, "monotonic-execution-status"
    )
    user = _user(tenant_id, workspace_id)
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await service._set_execution_status(
            conn,
            user=user,
            item=item,
            execution_status="executed",
            critical=True,
        )
        stored = await conn.fetchrow(
            """SELECT execution_status,
                      metadata->'decision_eligibility_provenance'->>'stage'
                          AS workflow_stage
                 FROM control_room_items
                WHERE workspace_id = $1 AND item_id = $2""",
            workspace_id,
            item["id"],
        )
        assert dict(stored) == {
            "execution_status": "executed",
            "workflow_stage": "executed",
        }
        with pytest.raises(HTTPException) as status_exc:
            await service._set_execution_status(
                conn,
                user=user,
                item=item,
                execution_status="preview_generated",
                critical=True,
            )
        assert status_exc.value.status_code == 409
        assert (
            await conn.fetchval(
                """SELECT execution_status FROM control_room_items
                    WHERE workspace_id = $1 AND item_id = $2""",
                workspace_id,
                item["id"],
            )
            == "executed"
        )

        with pytest.raises(HTTPException) as option_exc:
            await select_option_with_stage_cas(
                conn,
                user=user,
                item=item,
                workspace_id=workspace_id,
                option_id="replacement",
                terminal_statuses=("approved", "resolved", "dismissed"),
                ensure_item_row=service._ensure_item_row,
            )
        assert option_exc.value.status_code == 409
        assert (
            await conn.fetchval(
                """SELECT selected_option_id FROM control_room_items
                    WHERE workspace_id = $1 AND item_id = $2""",
                workspace_id,
                item["id"],
            )
            is None
        )
    finally:
        await conn.close()
