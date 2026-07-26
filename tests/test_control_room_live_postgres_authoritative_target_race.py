from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import control_room_service as service
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_execution_target import (
    execution_target_digest,
)
from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
)
from tests.control_room_live_authoritative_race import (
    authoritative_snapshot,
    mutate_target,
    seed_authoritative_scope,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


CASES = (
    (
        "create_followup_task",
        "entity",
        0,
        "acquire_authoritative_action_reservation",
        False,
    ),
    (
        "create_followup_task",
        "entity",
        1,
        "acquire_authoritative_action_reservation",
        False,
    ),
    (
        "prepare_hcm_access_review",
        "connection",
        0,
        "acquire_authoritative_action_reservation",
        False,
    ),
    (
        "prepare_hcm_access_review",
        "connection",
        1,
        "acquire_authoritative_action_reservation",
        False,
    ),
    (
        "prepare_hcm_access_review",
        "connection",
        0,
        "revalidate_authoritative_action",
        True,
    ),
    (
        "prepare_hcm_access_review",
        "connection",
        1,
        "revalidate_authoritative_action",
        True,
    ),
)


def _changed_item(item: dict, target_kind: str) -> dict:
    changed = deepcopy(item)
    if target_kind == "entity":
        changed["entity_id"] = f"{item['entity_id']}-B"
    else:
        metadata = changed.setdefault("metadata", {})
        metadata.setdefault("connection", {})["writeback_path"] = "/writeback/B"
    return changed


async def _blocked(observer: asyncpg.Connection, pid: int) -> bool:
    for _ in range(200):
        if await observer.fetchval("SELECT cardinality(pg_blocking_pids($1)) > 0", pid):
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "template_id,target_kind,tenant_index,gate_name,post_reservation",
    CASES,
    ids=(
        "internal-tenant-a",
        "internal-tenant-b",
        "external-before-reservation-tenant-a",
        "external-before-reservation-tenant-b",
        "external-before-remote-tenant-a",
        "external-before-remote-tenant-b",
    ),
)
async def test_live_execute_rejects_concurrent_authoritative_target_change(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    template_id: str,
    target_kind: str,
    tenant_index: int,
    gate_name: str,
    post_reservation: bool,
) -> None:
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=6)
    item_id = f"authoritative-target-{uuid4().hex}"
    execute_task = None
    writer = None
    observer = None
    transaction = None
    release = asyncio.Event()
    try:
        scopes = [
            await seed_authoritative_scope(
                postgres_with_real_init_schema,
                pool,
                item_id=item_id,
                template_id=template_id,
            )
            for _ in range(2)
        ]
        active = scopes[tenant_index]
        sibling = scopes[1 - tenant_index]
        before = await authoritative_snapshot(postgres_with_real_init_schema, active)
        sibling_before = await authoritative_snapshot(
            postgres_with_real_init_schema, sibling
        )

        changed = _changed_item(active.item, target_kind)
        template = ACTION_TEMPLATES[template_id]
        assert business_observation_fingerprint(active.item) == (
            business_observation_fingerprint(changed)
        )
        assert execution_target_digest(active.item, template) != (
            execution_target_digest(changed, template)
        )

        entered = asyncio.Event()
        execution_pid: int | None = None
        original_gate = getattr(service, gate_name)

        async def gated(conn, *args, **kwargs):
            nonlocal execution_pid
            execution_pid = int(await conn.fetchval("SELECT pg_backend_pid()"))
            entered.set()
            await release.wait()
            return await original_gate(conn, *args, **kwargs)

        monkeypatch.setattr(service, gate_name, gated)
        monkeypatch.setattr(service, "_external_writeback_enabled", lambda: True)

        class Adapter(service.BaseAdapter):
            supports_idempotency = True
            calls = 0

            async def execute(self, action_data, credentials, dry_run=True):
                type(self).calls += 1
                return service.ExecutionResult(True, "executed", "unexpected", {})

        monkeypatch.setattr(
            service.WriteBackAdapterFactory,
            "get_adapter",
            classmethod(lambda _cls, _template_type: Adapter()),
        )
        monkeypatch.setattr(
            service.auth,
            "pool",
            AsyncMock(return_value=pool),
        )
        monkeypatch.setattr(
            service,
            "_item_for_mutation",
            AsyncMock(return_value=active.item),
        )

        async def no_rows(*_args, **_kwargs):
            return []

        execute_task = asyncio.create_task(
            service.execute_item(
                active.item_id,
                active.user,
                template_id=template_id,
                binding_id=active.binding_id,
                confirm_execute=True,
                idempotency_key=f"race-{uuid4().hex}",
                fetcher=no_rows,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)

        writer = await asyncpg.connect(postgres_with_real_init_schema)
        observer = await asyncpg.connect(postgres_with_real_init_schema)
        transaction = writer.transaction()
        await transaction.start()
        target_b = await mutate_target(writer, active, target_kind=target_kind)
        release.set()
        assert execution_pid is not None
        assert await _blocked(observer, execution_pid)
        await transaction.commit()
        transaction = None
        await writer.close()
        writer = None
        await observer.close()
        observer = None

        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(execute_task, timeout=5)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "item_business_state_changed"
        assert Adapter.calls == 0

        after = await authoritative_snapshot(postgres_with_real_init_schema, active)
        sibling_after = await authoritative_snapshot(
            postgres_with_real_init_schema, sibling
        )
        assert after["item"] == target_b
        expected_counts = dict(before["counts"])
        if post_reservation:
            expected_counts["runs"] += 1
        assert after["counts"] == expected_counts
        if post_reservation:
            assert len(after["runs"]) == 1
            assert after["runs"][0]["status"] in {"failed", "pending"}
            assert not after["runs"][0]["remote_attempt"]
        else:
            assert after["runs"] == before["runs"] == []
        assert sibling_after == sibling_before
    finally:
        release.set()
        if transaction is not None:
            await transaction.rollback()
        if writer is not None:
            await writer.close()
        if observer is not None:
            await observer.close()
        if execute_task is not None:
            if not execute_task.done():
                execute_task.cancel()
            await asyncio.gather(execute_task, return_exceptions=True)
        await pool.close()
