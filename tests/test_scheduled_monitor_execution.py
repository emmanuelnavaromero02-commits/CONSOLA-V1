from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _reservation() -> dict[str, object]:
    return {"id": 41, "fencing_token": 7}


def _result() -> dict[str, object]:
    return {
        "run_id": 91,
        "deterministic_monitor": True,
        "monitor": {"status": "ready"},
    }


async def _execute(**overrides):
    from app.services.scheduled_monitor_execution import (
        execute_reserved_scheduled_monitor,
    )

    kwargs = {
        "agent": SimpleNamespace(
            id="agent-1", tenant_id="tenant-1", workspace_id="workspace-1"
        ),
        "message": "run",
        "reservation": _reservation(),
        "scheduled_fire_at": "2026-08-02T00:00:00+00:00",
        "metadata": {"source": "test"},
        "lease_seconds": 30,
        "heartbeat_interval_seconds": 0.01,
        "execution_timeout_seconds": 0.2,
    }
    kwargs.update(overrides)
    return await execute_reserved_scheduled_monitor(**kwargs)


@pytest.mark.anyio
async def test_long_monitor_renews_lease_until_success() -> None:
    heartbeats: list[dict] = []
    finishes: list[dict] = []
    renewed = asyncio.Event()

    async def heartbeat(**kwargs):
        heartbeats.append(kwargs)
        if len(heartbeats) == 2:
            renewed.set()

    async def run_monitor(*_args, **_kwargs):
        await renewed.wait()
        return _result()

    result = await _execute(
        run_scheduled_monitor=run_monitor,
        heartbeat_scheduled_run=heartbeat,
        finish_scheduled_run=lambda **kwargs: _record(finishes, kwargs),
    )

    assert result == _result()
    assert len(heartbeats) >= 2
    assert {call["fencing_token"] for call in heartbeats} == {7}
    assert [call["status"] for call in finishes] == ["ok"]
    assert finishes[0]["metadata"] == {
        "source": "test",
        "lease_guarded": True,
        "deterministic_monitor": True,
    }


async def _record(target: list[dict], value: dict) -> None:
    target.append(value)


@pytest.mark.anyio
async def test_initial_fence_failure_prevents_monitor_from_starting() -> None:
    from app.services.scheduled_monitor_execution import ScheduledMonitorLeaseLost

    started = False
    finishes: list[dict] = []

    async def run_monitor(*_args, **_kwargs):
        nonlocal started
        started = True
        return _result()

    async def heartbeat(**_kwargs):
        raise RuntimeError("stale fencing token")

    with pytest.raises(ScheduledMonitorLeaseLost):
        await _execute(
            run_scheduled_monitor=run_monitor,
            heartbeat_scheduled_run=heartbeat,
            finish_scheduled_run=lambda **kwargs: _record(finishes, kwargs),
        )

    assert started is False
    assert [call["status"] for call in finishes] == ["error"]
    assert finishes[0]["error_message"] == "scheduled_monitor_lease_lost"


@pytest.mark.anyio
async def test_background_heartbeat_failure_cancels_monitor_without_success() -> None:
    from app.services.scheduled_monitor_execution import ScheduledMonitorLeaseLost

    cancelled = asyncio.Event()
    finishes: list[dict] = []
    heartbeat_count = 0

    async def run_monitor(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def heartbeat(**_kwargs):
        nonlocal heartbeat_count
        heartbeat_count += 1
        if heartbeat_count > 1:
            raise RuntimeError("stale fencing token")

    with pytest.raises(ScheduledMonitorLeaseLost):
        await _execute(
            run_scheduled_monitor=run_monitor,
            heartbeat_scheduled_run=heartbeat,
            finish_scheduled_run=lambda **kwargs: _record(finishes, kwargs),
        )

    assert cancelled.is_set()
    assert heartbeat_count == 2
    assert [call["status"] for call in finishes] == ["error"]
    assert finishes[0]["error_message"] == "scheduled_monitor_lease_lost"


@pytest.mark.anyio
async def test_effect_guard_blocks_the_next_effect_after_fence_loss() -> None:
    from app.services.scheduled_monitor_execution import ScheduledMonitorLeaseLost

    heartbeat_count = 0
    effects: list[str] = []

    async def heartbeat(**_kwargs):
        nonlocal heartbeat_count
        heartbeat_count += 1
        if heartbeat_count > 1:
            raise RuntimeError("reservation reclaimed")

    async def run_monitor(*_args, lease_guard, **_kwargs):
        await lease_guard()
        effects.append("remote-effect")
        return _result()

    with pytest.raises(ScheduledMonitorLeaseLost):
        await _execute(
            run_scheduled_monitor=run_monitor,
            heartbeat_scheduled_run=heartbeat,
            finish_scheduled_run=lambda **_kwargs: _return(None),
        )

    assert heartbeat_count == 2
    assert effects == []


@pytest.mark.anyio
async def test_global_timeout_really_cancels_and_is_durably_error() -> None:
    from app.services.scheduled_monitor_execution import ScheduledMonitorTimedOut

    cancelled = asyncio.Event()
    finishes: list[dict] = []

    async def run_monitor(*_args, **_kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def heartbeat(**_kwargs):
        return None

    with pytest.raises(ScheduledMonitorTimedOut):
        await _execute(
            run_scheduled_monitor=run_monitor,
            heartbeat_scheduled_run=heartbeat,
            finish_scheduled_run=lambda **kwargs: _record(finishes, kwargs),
            execution_timeout_seconds=0.03,
        )

    assert cancelled.is_set()
    assert [call["status"] for call in finishes] == ["error"]
    assert finishes[0]["error_message"] == "scheduled_monitor_timeout"


@pytest.mark.anyio
async def test_stale_fence_cannot_finish_a_completed_monitor_as_success() -> None:
    from app.services.scheduled_monitor_execution import ScheduledMonitorLeaseLost

    finishes: list[dict] = []

    async def finish(**kwargs):
        finishes.append(kwargs)
        raise RuntimeError("reservation reclaimed")

    with pytest.raises(ScheduledMonitorLeaseLost):
        await _execute(
            run_scheduled_monitor=lambda *_args, **_kwargs: _return(_result()),
            heartbeat_scheduled_run=lambda **_kwargs: _return(None),
            finish_scheduled_run=finish,
        )

    assert [call["status"] for call in finishes] == ["ok"]


async def _return(value):
    return value


@pytest.mark.anyio
async def test_invalid_monitor_outcome_is_closed_as_error() -> None:
    from app.services.scheduled_monitor_execution import ScheduledMonitorOutcomeInvalid

    finishes: list[dict] = []

    with pytest.raises(ScheduledMonitorOutcomeInvalid):
        await _execute(
            run_scheduled_monitor=lambda *_args, **_kwargs: _return({"run_id": 1}),
            heartbeat_scheduled_run=lambda **_kwargs: _return(None),
            finish_scheduled_run=lambda **kwargs: _record(finishes, kwargs),
        )

    assert [call["status"] for call in finishes] == ["error"]
    assert finishes[0]["error_message"] == "scheduled_monitor_invalid_outcome"


def test_all_production_reservation_callers_use_the_guardian() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sources = (
        root / "console/app/main.py",
        root / "console/app/routers/v1/agents.py",
        root / "console/app/domains/pipeline/agentops_refresh.py",
    )
    for path in sources:
        source = path.read_text(encoding="utf-8")
        assert "execute_reserved_scheduled_monitor" in source, path
        assert "_agent_runtime.run_scheduled_monitor(" not in source, path
        assert "result = await run_scheduled_monitor(" not in source, path

    runtime = (root / "console/app/services/agent_runtime.py").read_text(
        encoding="utf-8"
    )
    guardian = (root / "console/app/services/scheduled_monitor_execution.py").read_text(
        encoding="utf-8"
    )
    assert "lease_guard=assert_lease" in guardian
    assert "if lease_guard is not None:\n            await lease_guard()" in runtime


def test_execution_timeout_and_heartbeat_are_strictly_inside_the_lease() -> None:
    from pathlib import Path

    from app.services.scheduled_monitor_execution import (
        AGENT_RUNNER_HTTP_TIMEOUT_SECONDS,
        SCHEDULED_MONITOR_TIMEOUT_SECONDS,
        SCHEDULED_RUN_HEARTBEAT_SECONDS,
        SCHEDULED_RUN_LEASE_SECONDS,
    )

    assert SCHEDULED_RUN_HEARTBEAT_SECONDS < SCHEDULED_MONITOR_TIMEOUT_SECONDS
    assert SCHEDULED_MONITOR_TIMEOUT_SECONDS < AGENT_RUNNER_HTTP_TIMEOUT_SECONDS
    assert AGENT_RUNNER_HTTP_TIMEOUT_SECONDS < SCHEDULED_RUN_LEASE_SECONDS
    root = Path(__file__).resolve().parents[1]
    scheduler = (root / "console/app/services/agent_scheduler.py").read_text()
    airflow = (root / "airflow/dags/agent_runner.py").read_text()
    assert "lease_seconds: int = 900" in scheduler
    assert "timeout=600" in airflow
