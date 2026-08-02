from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


SCHEDULED_RUN_LEASE_SECONDS = 900
SCHEDULED_RUN_HEARTBEAT_SECONDS = 60
SCHEDULED_MONITOR_TIMEOUT_SECONDS = 480
AGENT_RUNNER_HTTP_TIMEOUT_SECONDS = 600


class ScheduledMonitorExecutionError(RuntimeError):
    error_code = "scheduled_monitor_failed"


class ScheduledMonitorLeaseLost(ScheduledMonitorExecutionError):
    error_code = "scheduled_monitor_lease_lost"


class ScheduledMonitorTimedOut(ScheduledMonitorExecutionError):
    error_code = "scheduled_monitor_timeout"


class ScheduledMonitorOutcomeInvalid(ScheduledMonitorExecutionError):
    error_code = "scheduled_monitor_invalid_outcome"


async def _cancel_task(task: asyncio.Task[Any] | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _valid_monitor_result(result: Any) -> bool:
    return bool(
        isinstance(result, dict)
        and not isinstance(result.get("run_id"), bool)
        and isinstance(result.get("run_id"), int)
        and result.get("deterministic_monitor") is True
        and isinstance(result.get("monitor"), dict)
    )


async def execute_reserved_scheduled_monitor(
    *,
    agent: Any,
    message: str,
    reservation: dict[str, Any],
    scheduled_fire_at: str | None,
    metadata: dict[str, Any] | None = None,
    lease_seconds: int = SCHEDULED_RUN_LEASE_SECONDS,
    heartbeat_interval_seconds: float = SCHEDULED_RUN_HEARTBEAT_SECONDS,
    execution_timeout_seconds: float = SCHEDULED_MONITOR_TIMEOUT_SECONDS,
    run_scheduled_monitor: Callable[..., Awaitable[Any]] | None = None,
    heartbeat_scheduled_run: Callable[..., Awaitable[None]] | None = None,
    finish_scheduled_run: Callable[..., Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Run and close one reserved monitor while continuously owning its fence."""
    if (
        run_scheduled_monitor is None
        or heartbeat_scheduled_run is None
        or finish_scheduled_run is None
    ):
        from app.services import agent_runtime, agent_scheduler

        run_scheduled_monitor = (
            run_scheduled_monitor or agent_runtime.run_scheduled_monitor
        )
        heartbeat_scheduled_run = (
            heartbeat_scheduled_run or agent_scheduler.heartbeat_scheduled_run
        )
        finish_scheduled_run = (
            finish_scheduled_run or agent_scheduler.finish_scheduled_run
        )

    lease_seconds = int(lease_seconds)
    if lease_seconds <= 0:
        raise ValueError("scheduled monitor lease must be positive")
    if not 0 < heartbeat_interval_seconds < lease_seconds:
        raise ValueError("scheduled monitor heartbeat must be shorter than its lease")
    if not (
        0 < execution_timeout_seconds < lease_seconds
        and execution_timeout_seconds < AGENT_RUNNER_HTTP_TIMEOUT_SECONDS
    ):
        raise ValueError(
            "scheduled monitor timeout must be shorter than lease and HTTP timeout"
        )

    schedule_run_id = reservation.get("id")
    try:
        fencing_token = int(reservation["fencing_token"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ScheduledMonitorLeaseLost(
            "scheduled monitor reservation is invalid"
        ) from exc
    tenant_id = str(getattr(agent, "tenant_id", "") or "")
    workspace_id = str(getattr(agent, "workspace_id", "") or "")
    if not schedule_run_id or not tenant_id or not workspace_id:
        raise ScheduledMonitorLeaseLost(
            "scheduled monitor reservation scope is invalid"
        )

    finish_metadata = dict(metadata or {})
    finish_metadata["lease_guarded"] = True

    async def heartbeat_once() -> None:
        await heartbeat_scheduled_run(
            schedule_run_id=schedule_run_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            fencing_token=fencing_token,
            lease_seconds=lease_seconds,
        )

    async def assert_lease() -> None:
        try:
            await heartbeat_once()
        except Exception as exc:
            raise ScheduledMonitorLeaseLost(
                "scheduled monitor lease is unavailable"
            ) from exc

    async def finish(
        *, status: str, agent_run_id: int | None, error_code: str | None = None
    ) -> None:
        await finish_scheduled_run(
            schedule_run_id=schedule_run_id,
            agent_run_id=agent_run_id,
            status=status,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            fencing_token=fencing_token,
            error_message=error_code,
            metadata=finish_metadata,
        )

    try:
        await heartbeat_once()
    except Exception as exc:
        try:
            await finish(
                status="error",
                agent_run_id=None,
                error_code=ScheduledMonitorLeaseLost.error_code,
            )
        except Exception:
            pass
        raise ScheduledMonitorLeaseLost(
            "scheduled monitor lease is unavailable"
        ) from exc

    async def heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(heartbeat_interval_seconds)
            await assert_lease()

    monitor_task = asyncio.create_task(
        run_scheduled_monitor(
            agent,
            message,
            scheduled_fire_at=scheduled_fire_at,
            lease_guard=assert_lease,
        )
    )
    heartbeat_task = asyncio.create_task(heartbeat_loop())

    try:
        done, _pending = await asyncio.wait(
            {monitor_task, heartbeat_task},
            timeout=execution_timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise ScheduledMonitorTimedOut(
                "scheduled monitor exceeded its execution window"
            )
        if heartbeat_task in done:
            heartbeat_error = heartbeat_task.exception()
            raise ScheduledMonitorLeaseLost(
                "scheduled monitor lease was lost"
            ) from heartbeat_error
        result = monitor_task.result()
        if not _valid_monitor_result(result):
            raise ScheduledMonitorOutcomeInvalid(
                "scheduled monitor returned an invalid outcome"
            )
    except asyncio.CancelledError:
        await _cancel_task(monitor_task)
        await _cancel_task(heartbeat_task)
        try:
            await finish(
                status="cancelled",
                agent_run_id=None,
                error_code="scheduled_monitor_cancelled",
            )
        except Exception:
            pass
        raise
    except ScheduledMonitorExecutionError as exc:
        await _cancel_task(monitor_task)
        await _cancel_task(heartbeat_task)
        try:
            await finish(status="error", agent_run_id=None, error_code=exc.error_code)
        except Exception:
            if not isinstance(exc, ScheduledMonitorLeaseLost):
                raise ScheduledMonitorLeaseLost(
                    "scheduled monitor lease was lost while closing"
                ) from exc
        raise
    except Exception as exc:
        await _cancel_task(monitor_task)
        await _cancel_task(heartbeat_task)
        try:
            await finish(
                status="error",
                agent_run_id=None,
                error_code=ScheduledMonitorExecutionError.error_code,
            )
        except Exception as finish_exc:
            raise ScheduledMonitorLeaseLost(
                "scheduled monitor lease was lost while closing"
            ) from finish_exc
        raise
    else:
        await _cancel_task(heartbeat_task)
        finish_metadata["deterministic_monitor"] = True
        try:
            await finish(status="ok", agent_run_id=result["run_id"])
        except Exception as exc:
            raise ScheduledMonitorLeaseLost(
                "scheduled monitor lease was lost before completion"
            ) from exc
        return result
    finally:
        await _cancel_task(monitor_task)
        await _cancel_task(heartbeat_task)
