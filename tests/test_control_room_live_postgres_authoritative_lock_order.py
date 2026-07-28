from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from app.services import control_room_service as service
from app.services.control_room import business_authoritative_execution as authority
from tests.control_room_live_authoritative_race import (
    authoritative_snapshot,
    mutate_target,
    seed_authoritative_scope,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


async def _wait_until_blocked(observer: asyncpg.Connection, pid: int) -> bool:
    for _ in range(200):
        if await observer.fetchval("SELECT cardinality(pg_blocking_pids($1)) > 0", pid):
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_live_executor_first_serializes_a_then_preserves_target_b(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=4)
    scope = await seed_authoritative_scope(
        postgres_with_real_init_schema,
        pool,
        item_id=f"executor-first-{uuid4().hex}",
        template_id="create_followup_task",
    )
    before = await authoritative_snapshot(postgres_with_real_init_schema, scope)
    executor_locked = asyncio.Event()
    release_executor = asyncio.Event()
    original_lock = authority.lock_authoritative_business_item
    execute_task = None
    writer_task = None
    writer = None
    observer = None

    async def lock_then_pause(conn, *args, **kwargs):
        locked = await original_lock(conn, *args, **kwargs)
        if not executor_locked.is_set():
            executor_locked.set()
            await release_executor.wait()
        return locked

    try:
        monkeypatch.setattr(
            authority, "lock_authoritative_business_item", lock_then_pause
        )
        monkeypatch.setattr(service.auth, "pool", AsyncMock(return_value=pool))
        monkeypatch.setattr(
            service,
            "_item_for_mutation",
            AsyncMock(return_value=scope.item),
        )

        async def no_rows(*_args, **_kwargs):
            return []

        execute_task = asyncio.create_task(
            service.execute_item(
                scope.item_id,
                scope.user,
                template_id="create_followup_task",
                binding_id=scope.binding_id,
                confirm_execute=True,
                idempotency_key=f"executor-first-{uuid4().hex}",
                fetcher=no_rows,
            )
        )
        await asyncio.wait_for(executor_locked.wait(), timeout=5)

        writer = await asyncpg.connect(postgres_with_real_init_schema)
        observer = await asyncpg.connect(postgres_with_real_init_schema)
        writer_pid = int(await writer.fetchval("SELECT pg_backend_pid()"))

        async def write_b():
            async with writer.transaction():
                return await mutate_target(writer, scope, target_kind="entity")

        writer_task = asyncio.create_task(write_b())
        assert await _wait_until_blocked(observer, writer_pid)
        release_executor.set()
        result, target_b = await asyncio.gather(execute_task, writer_task)
        assert result["executed"] is True

        after = await authoritative_snapshot(postgres_with_real_init_schema, scope)
        assert after["item"] == target_b
        assert after["runs"] == [
            {"status": "completed", "error_code": None, "remote_attempt": None}
        ]
        expected_counts = {key: value + 1 for key, value in before["counts"].items()}
        assert after["counts"] == expected_counts
    finally:
        release_executor.set()
        for task in (execute_task, writer_task):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if writer is not None:
            await writer.close()
        if observer is not None:
            await observer.close()
        await pool.close()
