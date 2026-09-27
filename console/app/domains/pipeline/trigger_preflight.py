from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from app.domains.pipeline import stuck_run_recovery_service as recovery_service
from app.domains.pipeline.stuck_run_recovery import (
    AIRFLOW_ORPHAN,
    AIRFLOW_TERMINAL,
    MANUAL_TRIGGER_DAG_RE,
    MISSING_IN_AIRFLOW,
    STALLED_QUEUED_PAUSED_DAG,
    STALLED_RUNNING_NO_TASKS,
    as_utc_datetime,
)


logger = logging.getLogger(__name__)

PREFLIGHT_CACHE_TTL_SECONDS = 30.0
DEFAULT_STALE_AFTER_SECONDS = 900
UNPAUSE_AUDIT_ACTION = "pipeline.dag.unpause_for_manual_trigger"
PREFLIGHT_RECOVERY_ACTOR = "system:trigger-preflight"
PREFLIGHT_RECOVERY_CLASSES = frozenset(
    {
        AIRFLOW_TERMINAL,
        MISSING_IN_AIRFLOW,
        STALLED_QUEUED_PAUSED_DAG,
        STALLED_RUNNING_NO_TASKS,
        AIRFLOW_ORPHAN,
    }
)

McpInvoke = Callable[..., Awaitable[Any]]
RecoverDag = Callable[[str], Awaitable[Any]]
ConsoleRunIds = Callable[[str, Sequence[str]], Awaitable[set[str]]]
RecordEvent = Callable[..., Awaitable[None]]

_CONFLICTS: dict[str, tuple[str, str]] = {
    "dag_unavailable": (
        "The extraction DAG is not available in Airflow.",
        "El proceso de extracción no está disponible en Airflow en este momento. "
        "Intenta de nuevo en unos minutos o avisa a un administrador.",
    ),
    "dag_unpause_failed": (
        "The extraction DAG could not be resumed in Airflow.",
        "No se pudo reactivar el proceso de extracción en Airflow. "
        "Intenta de nuevo en unos minutos.",
    ),
    "dag_paused_by_operator": (
        "The scheduled DAG is paused by the platform operator.",
        "Este proceso tiene una programación automática y un operador de la "
        "plataforma lo pausó. La consola no lo reactiva; pide a un administrador "
        "que lo reanude.",
    ),
    "stale_runs_require_recovery": (
        "Earlier runs of this DAG are stuck in the queue.",
        "Hay corridas anteriores de este proceso atascadas en cola. Revisa y cierra "
        "las corridas atascadas antes de lanzar una nueva extracción.",
    ),
    "foreign_backlog_requires_platform_recovery": (
        "Runs outside this workspace are stuck in the DAG queue.",
        "El proceso tiene corridas atascadas que no pertenecen a este espacio de "
        "trabajo. Un administrador de la plataforma debe liberarlas antes de "
        "reactivarlo.",
    ),
    "auto_unpause_disabled": (
        "The DAG is paused and automatic resume is disabled.",
        "El proceso está en pausa y la reactivación automática está desactivada. "
        "Pide a un administrador que lo reanude.",
    ),
}
UNPAUSED_MESSAGE_ES = (
    "El proceso de extracción estaba en pausa en Airflow; se reactivó para "
    "ejecutar tu solicitud."
)

_READY_CACHE: dict[tuple[str, str, str], float] = {}


@dataclass(frozen=True)
class DagPreflight:
    dag_id: str
    schedule_kind: str
    was_paused: bool | None
    unpaused: bool
    reuse_run_id: str | None = None
    message_es: str | None = None
    checked: bool = True

    def automation(self) -> dict[str, Any]:
        return {
            "was_paused": self.was_paused,
            "unpaused": self.unpaused,
            "message_es": self.message_es,
        }


def reset_preflight_cache() -> None:
    _READY_CACHE.clear()


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def manual_dag_auto_unpause_enabled() -> bool:
    return _flag("PIPELINE_MANUAL_DAG_AUTO_UNPAUSE", True)


def auto_recovery_neutralize_enabled() -> bool:
    return _flag("PIPELINE_AUTO_RECOVERY_NEUTRALIZE", False)


def stuck_run_threshold_seconds() -> int:
    raw = os.environ.get("PIPELINE_STUCK_RUN_THRESHOLD_SECONDS")
    try:
        value = int(str(raw).strip()) if raw is not None and str(raw).strip() else 900
    except ValueError:
        value = 900
    return max(900, value)


def preflight_conflict(reason: str) -> HTTPException:
    message, public_message = _CONFLICTS[reason]
    wire_reason = "dag_unavailable" if reason == "dag_unpause_failed" else reason
    return HTTPException(
        409,
        detail={
            "reason": wire_reason,
            "message": message,
            "public_message": public_message,
        },
    )


def _scope_key(dag_id: str, user: Mapping[str, Any] | None) -> tuple[str, str, str]:
    data = user or {}
    return (
        dag_id,
        str(data.get("active_tenant_id") or data.get("tenant_id") or ""),
        str(data.get("active_workspace_id") or data.get("workspace_id") or ""),
    )


def _runs(info: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [run for run in info.get("runs") or [] if isinstance(run, dict)]


PENDING_STATES = frozenset({"queued", "running"})


def _run_is_stale(run: Mapping[str, Any], *, now: datetime, stale_after: int) -> bool:
    """A queued or running run triggered longer ago than the stale window."""
    if str(run.get("state") or "").lower() not in PENDING_STATES:
        return False
    if run.get("stale") is True:
        return True
    anchor = as_utc_datetime(run.get("queued_at")) or as_utc_datetime(
        run.get("start_date")
    )
    if anchor is None:
        return False
    return (now - anchor).total_seconds() >= stale_after


def _owned_by(run: Mapping[str, Any], user: Mapping[str, Any] | None) -> bool:
    """Own only when the run's conf names both the caller's tenant and workspace.

    Mirrors mcp-infra's run visibility rule, including case-insensitive ids.
    """
    _dag_id, tenant_id, workspace_id = _scope_key("", user)
    tenant_id, workspace_id = tenant_id.strip().lower(), workspace_id.strip().lower()
    conf = run.get("conf") if isinstance(run.get("conf"), dict) else {}
    return bool(
        tenant_id
        and workspace_id
        and str(conf.get("tenant_id") or "").strip().lower() == tenant_id
        and str(conf.get("workspace_id") or "").strip().lower() == workspace_id
    )


def _conf_matches(run: Mapping[str, Any], *, mode: str | None, target: str | None) -> bool:
    conf = run.get("conf") if isinstance(run.get("conf"), dict) else {}
    for key, wanted in (("mode", mode), ("target", target)):
        if wanted is None:
            continue
        observed = conf.get(key)
        if observed is not None and str(observed) != str(wanted):
            return False
    return True


def _reusable_run(
    runs: Sequence[Mapping[str, Any]],
    *,
    user: Mapping[str, Any] | None,
    paused: bool,
    now: datetime,
    stale_after: int,
    mode: str | None,
    target: str | None,
) -> str | None:
    """A live own run to attach to instead of queueing another one.

    On a paused DAG only a recent run qualifies: an old one cannot progress
    and must not be resumed by the next click.
    """
    live = []
    for run in runs:
        state = str(run.get("state") or "").lower()
        if state not in PENDING_STATES or not str(run.get("dag_run_id") or "").strip():
            continue
        if not _owned_by(run, user) or not _conf_matches(run, mode=mode, target=target):
            continue
        stale = _run_is_stale(run, now=now, stale_after=stale_after)
        if stale and (paused or state == "queued"):
            continue
        live.append(run)
    live.sort(key=lambda run: 0 if str(run.get("state")).lower() == "running" else 1)
    return str(live[0]["dag_run_id"]) if live else None


def _foreign_stale(info: Mapping[str, Any]) -> int:
    foreign = info.get("foreign") if isinstance(info.get("foreign"), dict) else {}
    total = 0
    for key in ("stale_queued", "stale_running"):
        try:
            total += max(0, int(foreign.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return total


async def _describe(
    dag_id: str, user: Mapping[str, Any] | None, *, invoke: McpInvoke, stale_after: int
) -> dict[str, Any] | None:
    try:
        info = await invoke(
            "infra",
            "airflow_describe_dag",
            {"dag_id": dag_id, "stale_after_seconds": stale_after},
            user=user,
        )
    except Exception:  # noqa: BLE001 - preflight fails open to the trigger path
        logger.warning("trigger preflight could not describe DAG %s", dag_id, exc_info=True)
        return None
    if not isinstance(info, dict) or info.get("error"):
        return None
    return info


async def _default_record_event(**kwargs: Any) -> None:
    from app.services import audit_service

    await audit_service.record_event(**kwargs)


async def ensure_dag_ready_for_manual_trigger(
    dag_id: str,
    user: dict[str, Any] | None,
    *,
    invoke: McpInvoke,
    recover: RecoverDag | None = None,
    auto_unpause: bool = True,
    auto_neutralize: bool = False,
    ttl_cache: float = PREFLIGHT_CACHE_TTL_SECONDS,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    mode: str | None = None,
    target: str | None = None,
    console_run_ids: ConsoleRunIds | None = None,
    record_event: RecordEvent | None = None,
    now: datetime | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> DagPreflight:
    """Make a manual extract DAG runnable before a console row is written.

    Manual (schedule=None) DAGs are resumed with an audit row; scheduled DAGs
    keep the operator's pause and the caller gets an honest 409. Airflow
    errors fail open so the trigger path reports its own outcome.
    """
    dag_id = str(dag_id or "").strip()
    if not MANUAL_TRIGGER_DAG_RE.fullmatch(dag_id):
        return DagPreflight(dag_id, "unknown", None, False, checked=False)
    aggregate = dag_id.endswith("_extract_all")
    key = _scope_key(dag_id, user)
    cached_at = _READY_CACHE.get(key)
    if not aggregate and cached_at is not None and clock() - cached_at < ttl_cache:
        return DagPreflight(dag_id, "manual", False, False)

    current = as_utc_datetime(now) or datetime.now(timezone.utc)
    info = await _describe(dag_id, user, invoke=invoke, stale_after=stale_after_seconds)
    if info is None:
        return DagPreflight(dag_id, "unknown", None, False, checked=False)
    if (
        info.get("found") is False
        or info.get("is_active") is False
        or bool(info.get("has_import_errors"))
    ):
        raise preflight_conflict("dag_unavailable")

    schedule_kind = str(info.get("schedule_kind") or "unknown")
    paused = bool(info.get("is_paused"))
    if schedule_kind != "manual":
        if paused:
            raise preflight_conflict("dag_paused_by_operator")
        return DagPreflight(dag_id, schedule_kind, False, False)

    runs = _runs(info)
    reuse_run_id = (
        _reusable_run(
            runs,
            user=user,
            paused=paused,
            now=current,
            stale_after=stale_after_seconds,
            mode=mode,
            target=target,
        )
        if aggregate
        else None
    )
    if not paused:
        if not aggregate:
            _READY_CACHE[key] = clock()
        return DagPreflight(dag_id, "manual", False, False, reuse_run_id=reuse_run_id)

    _READY_CACHE.pop(key, None)
    if info.get("running_truncated") or info.get("queued_truncated"):
        # More pending runs than one page: the backlog cannot be ruled out.
        raise preflight_conflict("foreign_backlog_requires_platform_recovery")
    stale_listed = [
        run
        for run in runs
        if _run_is_stale(run, now=current, stale_after=stale_after_seconds)
        and str(run.get("dag_run_id") or "") != (reuse_run_id or "")
    ]
    stale_own = [
        str(run.get("dag_run_id")) for run in stale_listed if _owned_by(run, user)
    ]
    foreign_stale = _foreign_stale(info) + len(stale_listed) - len(stale_own)
    if stale_own:
        known = (
            await console_run_ids(dag_id, stale_own)
            if console_run_ids is not None
            else set(stale_own)
        )
        recoverable = [run_id for run_id in stale_own if run_id in known]
        foreign_stale += len(stale_own) - len(recoverable)
        if recoverable:
            if not (auto_neutralize and recover is not None):
                raise preflight_conflict("stale_runs_require_recovery")
            await recover(dag_id)
            refreshed = await _describe(
                dag_id, user, invoke=invoke, stale_after=stale_after_seconds
            )
            if refreshed is None:
                raise preflight_conflict("stale_runs_require_recovery")
            remaining = {
                str(run.get("dag_run_id"))
                for run in _runs(refreshed)
                if _run_is_stale(run, now=current, stale_after=stale_after_seconds)
            }
            if remaining.intersection(recoverable):
                raise preflight_conflict("stale_runs_require_recovery")
    if foreign_stale:
        raise preflight_conflict("foreign_backlog_requires_platform_recovery")
    if not auto_unpause:
        raise preflight_conflict("auto_unpause_disabled")

    try:
        result = await invoke(
            "infra", "airflow_unpause_manual_dag", {"dag_id": dag_id}, user=user
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not resume manual DAG %s", dag_id, exc_info=True)
        raise preflight_conflict("dag_unpause_failed") from exc
    if not isinstance(result, dict) or not (
        result.get("unpaused") or result.get("was_paused") is False
    ):
        raise preflight_conflict("dag_unpause_failed")

    unpaused = bool(result.get("unpaused"))
    if unpaused:
        record = record_event or _default_record_event
        tenant_id, workspace_id = key[1] or None, key[2] or None
        try:
            await record(
                user_id=(user or {}).get("id"),
                email=(user or {}).get("email"),
                action=UNPAUSE_AUDIT_ACTION,
                resource_type="airflow_dag",
                resource_id=dag_id,
                status="success",
                metadata={
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "schedule_kind": schedule_kind,
                    "was_paused": True,
                    "source": "console.trigger_preflight",
                },
            )
        except Exception:  # noqa: BLE001 - the DAG is already resumed
            logger.error("unpause audit failed for DAG %s", dag_id, exc_info=True)
    if not aggregate:
        _READY_CACHE[key] = clock()
    return DagPreflight(
        dag_id,
        "manual",
        True,
        unpaused,
        reuse_run_id=reuse_run_id,
        message_es=UNPAUSED_MESSAGE_ES if unpaused else None,
    )


async def run_manual_trigger_preflight(
    dag_id: str,
    user: dict[str, Any] | None,
    *,
    invoke: McpInvoke,
    get_db_pool: Callable[[], Awaitable[Any]],
    refresh_dag_run_status: Callable[..., Awaitable[dict]],
    mode: str | None = None,
    target: str | None = None,
) -> DagPreflight:
    """Console entry point: env policy plus scoped recovery for own backlog."""
    threshold_seconds = stuck_run_threshold_seconds()

    async def _recover(target_dag_id: str) -> Any:
        return await recovery_service.recover_stuck_runs(
            user or {},
            mode=recovery_service.MODE_APPLY,
            actor=PREFLIGHT_RECOVERY_ACTOR,
            invoke=invoke,
            get_db_pool=get_db_pool,
            refresh_dag_run_status=refresh_dag_run_status,
            dag_ids=[target_dag_id],
            threshold=timedelta(seconds=threshold_seconds),
            allowed_classes=PREFLIGHT_RECOVERY_CLASSES,
            neutralize_airflow=True,
            orphan_scan=True,
        )

    async def _known(target_dag_id: str, run_ids: Sequence[str]) -> set[str]:
        try:
            return await recovery_service.console_run_ids(
                user,
                dag_id=target_dag_id,
                run_ids=run_ids,
                get_db_pool=get_db_pool,
            )
        except Exception:  # noqa: BLE001 - unknown ownership stays recoverable
            logger.warning("could not match Airflow runs to console rows", exc_info=True)
            return set(run_ids)

    return await ensure_dag_ready_for_manual_trigger(
        dag_id,
        user,
        invoke=invoke,
        recover=_recover,
        auto_unpause=manual_dag_auto_unpause_enabled(),
        auto_neutralize=auto_recovery_neutralize_enabled(),
        stale_after_seconds=threshold_seconds,
        mode=mode,
        target=target,
        console_run_ids=_known,
    )


__all__ = (
    "DagPreflight",
    "MANUAL_TRIGGER_DAG_RE",
    "UNPAUSE_AUDIT_ACTION",
    "auto_recovery_neutralize_enabled",
    "ensure_dag_ready_for_manual_trigger",
    "manual_dag_auto_unpause_enabled",
    "preflight_conflict",
    "reset_preflight_cache",
    "run_manual_trigger_preflight",
    "stuck_run_threshold_seconds",
)
