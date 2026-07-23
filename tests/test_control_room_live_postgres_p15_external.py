from __future__ import annotations

import asyncio

import asyncpg
import pytest

from app.services import control_room_service as service
from app.services.control_room.business_action_reservation import (
    ReservationState,
    acquire_guarded_action_reservation,
)
from app.services.control_room.business_action_attempt import (
    mark_remote_attempt_started,
)
from app.services.db_scope import SET_SCOPE_SQL
from tests.test_control_room_live_postgres_p15 import (
    _linked_item,
    _mutation_item,
    _seed_matching_dry_run,
    _template,
    _user,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


@pytest.mark.asyncio
async def test_live_external_remote_call_is_once_and_uses_reserved_key(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema, "p15-external"
    )
    user = _user(tenant_id, workspace_id)
    template = _template("p15_external", cartridge="replicon")
    payload = {"mode": "execute_live", "action_payload": {}}
    await _seed_matching_dry_run(
        postgres_with_real_init_schema,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item=item,
        template_id="p15_external",
    )

    class Adapter(service.BaseAdapter):
        supports_idempotency = True
        calls: list[str] = []

        async def execute(self, action_data, credentials, dry_run=True):
            self.calls.append(action_data["idempotency_key"])
            await asyncio.sleep(0.05)
            return service.ExecutionResult(True, "executed", "ok", {"id": "remote-1"})

    monkeypatch.setattr(
        service.WriteBackAdapterFactory,
        "get_adapter",
        classmethod(lambda _cls, _template_type: Adapter()),
    )
    barrier = asyncio.Barrier(2)

    async def execute():
        await barrier.wait()
        conn = await asyncpg.connect(omega_console_live_dsn)
        try:
            async with conn.transaction():
                await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
                reservation = await acquire_guarded_action_reservation(
                    conn,
                    user=user,
                    item=_mutation_item(item),
                    template_id="p15_external",
                    adapter_name="Adapter",
                    operation="execute",
                    input_payload=payload,
                )
        finally:
            await conn.close()
        if reservation.state is not ReservationState.ACQUIRED:
            return reservation
        conn = await asyncpg.connect(omega_console_live_dsn)
        try:
            async with conn.transaction():
                await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
                await mark_remote_attempt_started(
                    conn,
                    workspace_id=str(workspace_id),
                    reservation_id=reservation.id,
                    effective_key=reservation.effective_key,
                    adapter="Adapter",
                    target="replicon",
                )
        finally:
            await conn.close()
        conn = await asyncpg.connect(omega_console_live_dsn)
        try:
            async with conn.transaction():
                await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
                return await service._execute_external_writeback(
                    conn,
                    user=user,
                    item=_mutation_item(item),
                    template=template,
                    payload=payload,
                    reservation=reservation,
                    ip=None,
                    user_agent=None,
                )
        finally:
            await conn.close()

    await asyncio.gather(execute(), execute())
    check = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        row = await check.fetchrow(
            "SELECT idempotency_key, status FROM action_runs "
            "WHERE workspace_id=$1 AND item_id=$2 AND mode='execute'",
            workspace_id,
            item["id"],
        )
        assert row["status"] == "completed"
        assert Adapter.calls == [row["idempotency_key"]]
    finally:
        await check.close()
