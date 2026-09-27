from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from app.domains.pipeline import stuck_run_recovery_service as recovery_service
from app.domains.pipeline import trigger_preflight
from app.domains.pipeline.stuck_run_recovery import (
    AIRFLOW_TERMINAL,
    MISSING_IN_AIRFLOW,
    STALLED_CLASSES,
)
from app.services.db_scope import system_platform_db


logger = logging.getLogger(__name__)

LOCK_KEY = "omega:pipeline_run_janitor"
PLATFORM_PURPOSE = "pipeline_run_janitor.workspaces"
JANITOR_ACTOR = "system:pipeline-janitor"
MODES = ("off", "sync", "recover")
DEFAULT_MODE = "sync"
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
INITIAL_DELAY_SECONDS = 30
WORKSPACE_LIMIT = 500


def janitor_mode() -> str:
    raw = str(os.environ.get("PIPELINE_RUN_JANITOR_MODE") or DEFAULT_MODE).strip().lower()
    return raw if raw in MODES else DEFAULT_MODE


def janitor_interval_seconds() -> int:
    raw = os.environ.get("PIPELINE_RUN_JANITOR_INTERVAL_SECONDS")
    try:
        value = int(str(raw).strip()) if raw is not None and str(raw).strip() else DEFAULT_INTERVAL_SECONDS
    except ValueError:
        value = DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, value)


def janitor_allowed_classes(mode: str, *, neutralize: bool) -> frozenset[str]:
    if mode == "recover":
        allowed = {AIRFLOW_TERMINAL, MISSING_IN_AIRFLOW}
        if neutralize:
            allowed |= STALLED_CLASSES
        return frozenset(allowed)
    return frozenset({AIRFLOW_TERMINAL})


def system_user(tenant_id: str, workspace_id: str) -> dict[str, Any]:
    return {
        "id": 0,
        "email": JANITOR_ACTOR,
        "role": "admin",
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "allowed_cartridges": ["*"],
    }


RecoverFn = Callable[..., Awaitable[Any]]


async def run_janitor_tick(
    *,
    mode: str | None = None,
    get_db_pool: Callable[[], Awaitable[Any]] | None = None,
    recover: RecoverFn | None = None,
    invoke: Callable[..., Awaitable[Any]] | None = None,
    refresh_dag_run_status: Callable[..., Awaitable[dict]] | None = None,
    neutralize: bool | None = None,
    threshold_seconds: int | None = None,
    workspace_limit: int = WORKSPACE_LIMIT,
) -> dict[str, Any]:
    """One leader-only pass over every workspace; never raises per workspace."""
    mode = mode or janitor_mode()
    if mode == "off":
        return {"status": "skipped", "reason": "disabled"}
    get_pool = get_db_pool or recovery_service.default_get_db_pool
    recover_fn = recover or recovery_service.recover_stuck_runs
    neutralize = (
        trigger_preflight.auto_recovery_neutralize_enabled()
        if neutralize is None
        else neutralize
    )
    neutralize = bool(neutralize and mode == "recover")
    threshold = timedelta(
        seconds=threshold_seconds or trigger_preflight.stuck_run_threshold_seconds()
    )
    allowed = janitor_allowed_classes(mode, neutralize=neutralize)

    pool = await get_pool()
    summary = {
        "status": "ok",
        "mode": mode,
        "workspaces": 0,
        "recovered": 0,
        "synced_terminal": 0,
        "failed_workspaces": 0,
    }
    async with pool.acquire() as lock_conn:
        leader = await lock_conn.fetchval(
            "SELECT pg_try_advisory_lock(hashtext($1))", LOCK_KEY
        )
        if not leader:
            return {"status": "skipped", "reason": "not_leader", "mode": mode}
        try:
            # The workspace list is the only cross-workspace read; each
            # workspace is then recovered under its own tenant/workspace scope.
            async with system_platform_db(pool, purpose=PLATFORM_PURPOSE) as platform:
                rows = await platform.fetch(
                    """
                    SELECT w.id::text AS workspace_id, w.tenant_id::text AS tenant_id
                      FROM workspaces w
                     WHERE w.tenant_id IS NOT NULL
                     ORDER BY w.created_at ASC, w.name ASC
                     LIMIT $1
                    """,
                    max(1, int(workspace_limit)),
                )
            for row in rows:
                summary["workspaces"] += 1
                user = system_user(row["tenant_id"], row["workspace_id"])
                try:
                    report = await recover_fn(
                        user,
                        mode=recovery_service.MODE_APPLY,
                        actor=JANITOR_ACTOR,
                        invoke=invoke or recovery_service.default_invoke,
                        get_db_pool=get_pool,
                        refresh_dag_run_status=(
                            refresh_dag_run_status
                            or recovery_service.default_refresh_dag_run_status
                        ),
                        threshold=threshold,
                        allowed_classes=allowed,
                        neutralize_airflow=neutralize,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - one workspace never stops the pass
                    summary["failed_workspaces"] += 1
                    logger.warning(
                        "pipeline janitor failed for workspace %s",
                        row["workspace_id"],
                        exc_info=True,
                    )
                    continue
                counts = getattr(report, "counts", None) or {}
                summary["recovered"] += int(counts.get("recovered") or 0)
                summary["synced_terminal"] += int(counts.get("synced_terminal") or 0)
        finally:
            await lock_conn.execute("SELECT pg_advisory_unlock(hashtext($1))", LOCK_KEY)
    if summary["failed_workspaces"]:
        summary["status"] = "partial"
    return summary


async def _wait(stop_event: asyncio.Event | None, seconds: float) -> bool:
    if stop_event is None:
        await asyncio.sleep(seconds)
        return False
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def janitor_loop(
    stop_event: asyncio.Event | None = None,
    *,
    tick: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    initial_delay: float = INITIAL_DELAY_SECONDS,
) -> None:
    mode = janitor_mode()
    if mode == "off":
        logger.info("pipeline run janitor disabled")
        return
    interval = janitor_interval_seconds()
    run_tick = tick or (lambda: run_janitor_tick(mode=mode))
    if await _wait(stop_event, initial_delay):
        return
    while True:
        try:
            result = await run_tick()
            if result.get("recovered") or result.get("synced_terminal"):
                logger.info("pipeline run janitor: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("pipeline run janitor tick failed", exc_info=True)
        if await _wait(stop_event, interval):
            return


__all__ = (
    "JANITOR_ACTOR",
    "LOCK_KEY",
    "janitor_allowed_classes",
    "janitor_interval_seconds",
    "janitor_loop",
    "janitor_mode",
    "run_janitor_tick",
    "system_user",
)
