from __future__ import annotations

import asyncpg
import pytest

from app.services.control_room.business_action_reservation import (
    ReservationState,
    acquire_action_reservation,
)
from app.services.db_scope import SET_SCOPE_SQL
from tests.test_control_room_live_postgres_p15 import _linked_item
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


@pytest.mark.asyncio
async def test_live_stale_external_reservation_reclaims_same_row_and_key(
    postgres_with_real_init_schema: str,
):
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema,
        "p15-external-recovery",
    )
    first_conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await first_conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
        first = await acquire_action_reservation(
            first_conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item=item,
            template_id="external-recovery",
            adapter_name="IdempotentAdapter",
            operation="execute",
        )
        await first_conn.execute(
            """UPDATE action_runs
                  SET updated_at = NOW() - INTERVAL '10 minutes'
                WHERE workspace_id=$1 AND id=$2""",
            workspace_id,
            first.id,
        )
    finally:
        await first_conn.close()

    retry_conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await retry_conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
        retry = await acquire_action_reservation(
            retry_conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item=item,
            template_id="external-recovery",
            adapter_name="IdempotentAdapter",
            operation="execute",
        )
        count = await retry_conn.fetchval(
            """SELECT COUNT(*) FROM action_runs
                WHERE workspace_id=$1 AND idempotency_key=$2""",
            workspace_id,
            first.effective_key,
        )
    finally:
        await retry_conn.close()

    assert retry.state is ReservationState.ACQUIRED
    assert retry.id == first.id
    assert retry.effective_key == first.effective_key
    assert count == 1
