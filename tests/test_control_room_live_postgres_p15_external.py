from __future__ import annotations

import asyncio

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import control_room_service as service
from tests.control_room_live_action_binding_seed import persist_live_action_binding
from tests.test_control_room_live_postgres_p15 import (
    _linked_item,
    _seed_matching_dry_run,
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
    user = {
        **_user(tenant_id, workspace_id),
        "role": "super_admin",
        "allowed_cartridges": ["*"],
    }
    template_id = "prepare_hcm_access_review"
    setup = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        await setup.execute(
            "UPDATE control_room_items SET metadata=COALESCE(metadata, '{}'::jsonb) "
            "|| jsonb_build_object('connection', $3::jsonb) "
            "WHERE workspace_id=$1::uuid AND item_id=$2",
            workspace_id,
            item["id"],
            '{"base_url":"https://sap.example.test",'
            '"writeback_path":"/test/writeback"}',
        )
    finally:
        await setup.close()
    item, binding_id = await persist_live_action_binding(
        postgres_with_real_init_schema,
        omega_console_live_dsn,
        user=user,
        item_id=item["id"],
        template_id=template_id,
    )
    await _seed_matching_dry_run(
        postgres_with_real_init_schema,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
    )
    item["execution_status"] = "dry_run_validated"

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
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=6)

    async def load_same_snapshot(*_args, **_kwargs):
        await barrier.wait()
        return item

    async def pool_factory():
        return pool

    monkeypatch.setattr(service.auth, "pool", pool_factory)
    monkeypatch.setattr(service, "_item_for_mutation", load_same_snapshot)
    monkeypatch.setattr(service, "_external_writeback_enabled", lambda: True)

    async def execute():
        return await service.execute_item(
            item["id"],
            user,
            template_id=template_id,
            binding_id=binding_id,
            confirm_execute=True,
            idempotency_key=None,
        )

    try:
        outcomes = await asyncio.gather(execute(), execute(), return_exceptions=True)

        async def load_retry_snapshot(*_args, **_kwargs):
            return item

        monkeypatch.setattr(service, "_item_for_mutation", load_retry_snapshot)
        retry = await execute()
    finally:
        await pool.close()
    results = [value for value in outcomes if isinstance(value, dict)]
    conflicts = [value for value in outcomes if isinstance(value, HTTPException)]
    assert len(results) == 1 and results[0]["executed"] is True
    assert len(conflicts) == 1
    assert conflicts[0].detail["code"] == "action_in_progress"
    assert retry["idempotent"] is True
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
