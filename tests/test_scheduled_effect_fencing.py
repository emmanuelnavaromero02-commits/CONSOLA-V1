from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.scheduled_monitor_execution import (
    ScheduledMonitorLeaseLost,
    execute_reserved_scheduled_monitor,
)


@pytest.mark.asyncio
async def test_lease_loss_cannot_wait_forever_for_a_cancellation_rebel() -> None:
    cancelled = asyncio.Event()
    release = asyncio.Event()
    heartbeat_calls = 0

    async def monitor(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            return {"run_id": 1, "deterministic_monitor": True, "monitor": {}}

    async def heartbeat(**_kwargs):
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        if heartbeat_calls > 1:
            raise RuntimeError("fence lost")

    async def finish(**_kwargs):
        return None

    task = asyncio.create_task(
        execute_reserved_scheduled_monitor(
            agent=SimpleNamespace(tenant_id="tenant-a", workspace_id="workspace-a"),
            message="probe",
            reservation={"id": 1, "fencing_token": 1},
            scheduled_fire_at=None,
            heartbeat_interval_seconds=0.01,
            execution_timeout_seconds=0.5,
            run_scheduled_monitor=monitor,
            heartbeat_scheduled_run=heartbeat,
            finish_scheduled_run=finish,
        )
    )
    try:
        await asyncio.wait_for(cancelled.wait(), timeout=0.3)
        await asyncio.sleep(0)
        assert task.done(), "lease loss must not wait indefinitely for cancellation"
        with pytest.raises(ScheduledMonitorLeaseLost):
            await task
    finally:
        release.set()
        if not task.done():
            with pytest.raises(ScheduledMonitorLeaseLost):
                await asyncio.wait_for(task, timeout=0.3)


def test_every_scheduled_effect_receives_server_owned_fence_capability() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "console/app/services/agent_runtime.py"
    ).read_text(encoding="utf-8")
    section = source.split("async def run_scheduled_monitor", 1)[1]
    assert "schedule_run_id" in section
    assert "fencing_token" in section
    assert "effect_authority" in section
