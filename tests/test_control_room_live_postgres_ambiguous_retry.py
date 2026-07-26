from __future__ import annotations

import asyncio

import asyncpg
import pytest

from app.services.control_room.business_action_attempt import (
    mark_remote_attempt_ambiguous,
    mark_remote_attempt_started,
    remote_attempt_status,
)
from app.services.control_room.business_reservation_lease import (
    reservation_lease_token,
)
from app.services.control_room.business_action_reservation import (
    ReservationState,
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
async def test_live_ambiguous_remote_attempt_retries_never_reacquire(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema,
        "ambiguous-concurrent-retry",
    )
    user = _user(tenant_id, workspace_id)
    template_id = "prepare_hcm_access_review"
    payload = {"mode": "execute_live", "action_payload": {}}
    await _seed_matching_dry_run(
        postgres_with_real_init_schema,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
    )

    setup = await asyncpg.connect(omega_console_live_dsn)
    try:
        async with setup.transaction():
            await setup.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
            reservation = await acquire_guarded_action_reservation(
                setup,
                user=user,
                item=_mutation_item(item),
                template_id=template_id,
                adapter_name="CountingAdapter",
                operation="execute",
                input_payload=payload,
            )
            assert reservation.state is ReservationState.ACQUIRED
            await mark_remote_attempt_started(
                setup,
                workspace_id=workspace_id,
                reservation_id=reservation.id,
                effective_key=reservation.effective_key,
                adapter="CountingAdapter",
                target="sap_hcm",
                lease_token=reservation_lease_token(reservation.row),
            )
            await mark_remote_attempt_ambiguous(
                setup,
                workspace_id=workspace_id,
                reservation_id=reservation.id,
                effective_key=reservation.effective_key,
                error_code="RuntimeError",
            )
    finally:
        await setup.close()

    barrier = asyncio.Barrier(2)
    remote_calls = 1

    async def retry():
        conn = await asyncpg.connect(omega_console_live_dsn)
        try:
            async with conn.transaction():
                await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
                await barrier.wait()
                return await acquire_guarded_action_reservation(
                    conn,
                    user=user,
                    item=_mutation_item(item),
                    template_id=template_id,
                    adapter_name="CountingAdapter",
                    operation="execute",
                    input_payload=payload,
                )
        finally:
            await conn.close()

    retries = await asyncio.gather(retry(), retry())
    assert [value.state for value in retries] == [
        ReservationState.IN_PROGRESS,
        ReservationState.IN_PROGRESS,
    ]
    assert {value.id for value in retries} == {reservation.id}
    assert {value.effective_key for value in retries} == {reservation.effective_key}
    assert remote_calls == 1

    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        rows = await check.fetch(
            "SELECT * FROM action_runs WHERE workspace_id=$1 AND item_id=$2 "
            "AND mode='execute'",
            workspace_id,
            item["id"],
        )
    finally:
        await check.close()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert remote_attempt_status(dict(rows[0])) == "ambiguous"
