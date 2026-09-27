from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from app.domains.pipeline.status_transitions import (
    PIPELINE_TERMINAL_STATUSES,
    normalize_pipeline_status,
)
from app.domains.pipeline.stuck_run_recovery import (
    ACTION_MARK_FAILED,
    ACTION_NONE,
    ACTION_SYNC_TERMINAL,
    ACTIVE_TASK_STATES,
    AIRFLOW_TERMINAL,
    CANDIDATE_STATUSES,
    DEFAULT_MISSING_GRACE,
    DEFAULT_THRESHOLD,
    IN_PROGRESS_CLASSES,
    MARK_FAILED_CLASSES,
    MISSING_IN_AIRFLOW,
    NOT_APPLICABLE,
    RECOVERY_MESSAGE,
    STALLED_CLASSES,
    UNVERIFIABLE,
    AirflowRunTruth,
    RunVerdict,
    as_utc_datetime,
    classify_stuck_run,
    plan_digest,
    plan_entry,
    pre_airflow_verdict,
    recover_update_sql,
)
from app.services.db_scope import SET_SCOPE_SQL


logger = logging.getLogger(__name__)

McpInvoke = Callable[..., Awaitable[Any]]
GetDbPool = Callable[[], Awaitable[Any]]
RefreshRun = Callable[..., Awaitable[dict]]
RecordEvent = Callable[..., Awaitable[None]]

MODE_DRY_RUN = "dry_run"
MODE_APPLY = "apply"
MAX_CANDIDATES = 200
AUDIT_ACTION = "pipeline_run.recover_stuck"
DEFAULT_ALLOWED_CLASSES = frozenset({AIRFLOW_TERMINAL, *MARK_FAILED_CLASSES})
PRE_TRIGGER_ACTOR = "system:pre-trigger-recovery"


class PlanChanged(Exception):
    def __init__(self, plan_digest_value: str):
        super().__init__("recovery plan changed")
        self.plan_digest = plan_digest_value


@dataclass
class RecoveryReport:
    mode: str
    checked_at: datetime
    threshold_minutes: int
    plan_digest: str
    counts: dict[str, int]
    runs: list[dict[str, Any]]
    truncated: bool
    message_es: str
    verdicts: dict[str, RunVerdict] = field(default_factory=dict)


def _count_template() -> dict[str, int]:
    return {
        "candidates": 0,
        "recoverable": 0,
        "recovered": 0,
        "synced_terminal": 0,
        "live": 0,
        "unverifiable": 0,
        "not_applicable": 0,
        "conflicts": 0,
        "airflow_neutralized": 0,
        "airflow_neutralize_failed": 0,
    }


def _scope(user: Mapping[str, Any] | None) -> tuple[str, str]:
    data = user or {}
    tenant_id = str(data.get("active_tenant_id") or data.get("tenant_id") or "").strip()
    workspace_id = str(
        data.get("active_workspace_id") or data.get("workspace_id") or ""
    ).strip()
    if not tenant_id or not workspace_id:
        raise HTTPException(403, "active tenant/workspace is required")
    return tenant_id, workspace_id


def _extra(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("extra")
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


async def load_recovery_candidates(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    cartridge: str | None = None,
    dag_ids: Sequence[str] | None = None,
    exclude_run_ids: Collection[str] = (),
    max_age_seconds: int | None = None,
    limit: int = MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    clauses = [
        "tenant_id = $1::uuid",
        "workspace_id = $2::uuid",
        "LOWER(COALESCE(status, 'unknown')) = ANY($3::text[])",
    ]
    args: list[Any] = [tenant_id, workspace_id, list(CANDIDATE_STATUSES)]
    if cartridge:
        args.append(cartridge)
        clauses.append(f"cartridge_id = ${len(args)}")
    if dag_ids:
        args.append(list(dag_ids))
        clauses.append(f"dag_id = ANY(${len(args)}::text[])")
    if exclude_run_ids:
        args.append(list(exclude_run_ids))
        clauses.append(f"NOT (run_id = ANY(${len(args)}::text[]))")
    if max_age_seconds:
        args.append(int(max_age_seconds))
        clauses.append(
            f"started_at > NOW() - (${len(args)}::integer * INTERVAL '1 second')"
        )
    args.append(max(1, int(limit)) + 1)
    rows = await conn.fetch(
        f"""
        SELECT run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
               status, started_at, finished_at, error_message, extra,
               fencing_token, lease_expires_at, tenant_id::text AS tenant_id,
               workspace_id::text AS workspace_id
          FROM pipeline_runs
         WHERE {' AND '.join(clauses)}
         ORDER BY started_at ASC NULLS FIRST, run_id ASC
         LIMIT ${len(args)}
        """,
        *args,
    )
    return [dict(row) for row in rows]


def _describe_runs(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    runs = payload.get("runs") if isinstance(payload, Mapping) else None
    return [run for run in (runs or []) if isinstance(run, dict)]


def _foreign_running(payload: Mapping[str, Any]) -> int:
    foreign = payload.get("foreign") if isinstance(payload, Mapping) else None
    if not isinstance(foreign, Mapping):
        return 0
    try:
        return max(0, int(foreign.get("running") or 0))
    except (TypeError, ValueError):
        return 0


def _latest_task_activity(tasks: Sequence[Mapping[str, Any]]) -> datetime | None:
    moments = [
        moment
        for task in tasks
        for moment in (
            as_utc_datetime(task.get("start_date")),
            as_utc_datetime(task.get("end_date")),
        )
        if moment is not None
    ]
    return max(moments) if moments else None


async def observe_airflow_truth(
    rows: Sequence[Mapping[str, Any]],
    *,
    invoke: McpInvoke,
    user: dict | None,
    concurrency: int = 5,
    timeout: float = 20.0,
) -> dict[str, AirflowRunTruth]:
    """One Airflow observation per run; DAG description cached per DAG."""
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))
    describe_futures: dict[str, asyncio.Future] = {}

    async def _describe(dag_id: str) -> dict[str, Any]:
        try:
            result = await invoke(
                "infra", "airflow_describe_dag", {"dag_id": dag_id}, user=user
            )
        except Exception as exc:  # noqa: BLE001 - observation must never raise
            logger.debug("airflow_describe_dag failed for %s", dag_id, exc_info=True)
            return {"error": type(exc).__name__}
        if not isinstance(result, dict):
            return {"error": "invalid_payload"}
        return result

    def _describe_future(dag_id: str) -> asyncio.Future:
        future = describe_futures.get(dag_id)
        if future is None:
            future = asyncio.ensure_future(_describe(dag_id))
            describe_futures[dag_id] = future
        return future

    async def _observe(row: Mapping[str, Any]) -> AirflowRunTruth:
        dag_id = str(row.get("dag_id") or "")
        dag_run_id = str(row.get("airflow_dag_run_id") or "")
        async with semaphore:
            dag = await _describe_future(dag_id)
            try:
                status = await invoke(
                    "infra",
                    "airflow_get_run_status",
                    {"dag_id": dag_id, "dag_run_id": dag_run_id},
                    user=user,
                )
            except Exception as exc:  # noqa: BLE001
                return AirflowRunTruth(error=type(exc).__name__)
            if not isinstance(status, dict) or status.get("error"):
                return AirflowRunTruth(error="airflow_status_unavailable")
            state = str(status.get("state") or "").strip().lower() or None
            found = status.get("found")
            if found is None:
                found = bool(state) and state != "not_found"
            dag_ok = not dag.get("error")
            own_runs = _describe_runs(dag) if dag_ok else []
            listed = next(
                (run for run in own_runs if run.get("dag_run_id") == dag_run_id),
                None,
            )
            sibling_running: bool | None = None
            if dag_ok:
                sibling_running = _foreign_running(dag) > 0 or any(
                    str(run.get("state") or "").lower() == "running"
                    and run.get("dag_run_id") != dag_run_id
                    for run in own_runs
                )
            active_tasks: int | None = None
            last_activity: datetime | None = None
            if found and state == "running":
                try:
                    tasks_payload = await invoke(
                        "infra",
                        "airflow_list_task_instances",
                        {"dag_id": dag_id, "dag_run_id": dag_run_id},
                        user=user,
                    )
                except Exception:  # noqa: BLE001
                    tasks_payload = None
                if isinstance(tasks_payload, dict) and not tasks_payload.get("error"):
                    tasks = [
                        task
                        for task in tasks_payload.get("tasks") or []
                        if isinstance(task, dict)
                    ]
                    active_tasks = sum(
                        1
                        for task in tasks
                        if str(task.get("state") or "").lower() in ACTIVE_TASK_STATES
                    )
                    last_activity = _latest_task_activity(tasks)
            return AirflowRunTruth(
                found=bool(found),
                state=state,
                queued_at=as_utc_datetime((listed or {}).get("queued_at")),
                start_date=as_utc_datetime(status.get("start_date")),
                end_date=as_utc_datetime(status.get("end_date")),
                active_tasks=active_tasks,
                last_task_activity_at=last_activity,
                dag_found=(bool(dag.get("found")) if dag_ok else None),
                dag_paused=(
                    bool(dag.get("is_paused"))
                    if dag_ok and dag.get("is_paused") is not None
                    else None
                ),
                schedule_kind=(str(dag.get("schedule_kind")) if dag_ok else None),
                sibling_running=sibling_running,
                scheduler_healthy=(
                    bool(dag.get("scheduler_healthy"))
                    if dag_ok and dag.get("scheduler_healthy") is not None
                    else None
                ),
            )

    async def _bounded(row: Mapping[str, Any]) -> AirflowRunTruth:
        try:
            return await asyncio.wait_for(_observe(row), timeout=max(0.1, timeout))
        except asyncio.TimeoutError:
            return AirflowRunTruth(error="timeout")

    keys = [str(row.get("run_id") or "") for row in rows]
    try:
        results = await asyncio.gather(*(_bounded(row) for row in rows))
    finally:
        for future in describe_futures.values():
            if not future.done():
                future.cancel()
    return dict(zip(keys, results))


def _age_minutes(now: datetime, started_at: Any) -> int | None:
    moment = as_utc_datetime(started_at)
    if moment is None:
        return None
    return max(0, int((now - moment).total_seconds() // 60))


def _run_entry(
    row: Mapping[str, Any],
    verdict: RunVerdict,
    *,
    now: datetime,
    action: str,
    neutralize: bool,
) -> dict[str, Any]:
    started = as_utc_datetime(row.get("started_at"))
    return {
        "run_id": str(row.get("run_id") or ""),
        "dag_id": str(row.get("dag_id") or ""),
        "cartridge": str(row.get("cartridge_id") or ""),
        "entity": str(row.get("entity") or ""),
        "status_before": normalize_pipeline_status(row.get("status")),
        "status_after": None,
        "started_at": started,
        "age_minutes": _age_minutes(now, started),
        "created_today": bool(started and started.date() == now.date()),
        "classification": verdict.classification,
        "action": action,
        "neutralize_airflow": neutralize,
        "reason_es": verdict.reason_es,
    }


def _planned_action(
    verdict: RunVerdict,
    *,
    allowed_classes: Collection[str],
    neutralize_airflow: bool,
) -> tuple[str, bool]:
    if verdict.action == ACTION_NONE or verdict.classification not in allowed_classes:
        return ACTION_NONE, False
    if verdict.neutralize_airflow and not neutralize_airflow:
        return ACTION_NONE, False
    return verdict.action, verdict.neutralize_airflow


def _message_es(mode: str, counts: Mapping[str, int], truncated: bool) -> str:
    parts: list[str] = []
    if mode == MODE_DRY_RUN:
        if counts["recoverable"]:
            parts.append(
                f"{counts['recoverable']} de {counts['candidates']} corridas "
                "revisadas se pueden cerrar o sincronizar con Airflow."
            )
        elif counts["candidates"]:
            parts.append(
                f"Se revisaron {counts['candidates']} corridas; "
                "ninguna requiere recuperación."
            )
        else:
            parts.append("No hay corridas pendientes que revisar.")
    else:
        parts.append(
            f"Se cerraron {counts['recovered']} corridas atascadas y se "
            f"sincronizaron {counts['synced_terminal']} con su estado final en Airflow."
        )
        if counts["conflicts"]:
            parts.append(
                f"{counts['conflicts']} cambiaron mientras se revisaban y no se tocaron."
            )
        if counts["airflow_neutralize_failed"]:
            parts.append(
                f"{counts['airflow_neutralize_failed']} no se pudieron detener en "
                "Airflow y se dejaron sin cambios."
            )
    if counts["unverifiable"]:
        parts.append(
            f"{counts['unverifiable']} no se pudieron verificar en Airflow y no se modifican."
        )
    if truncated:
        parts.append("Se revisaron las 200 corridas más antiguas; repite la revisión.")
    return " ".join(parts)


async def _neutralize(
    row: Mapping[str, Any],
    truth: AirflowRunTruth | None,
    *,
    invoke: McpInvoke,
    user: dict | None,
) -> str:
    """Stop the Airflow run first; returns ok, gone, state_changed or failed."""
    state = str((truth.state if truth else None) or "").lower()
    if state not in {"queued", "running"}:
        return "failed"
    try:
        result = await invoke(
            "infra",
            "airflow_mark_dag_run_failed",
            {
                "dag_id": str(row.get("dag_id") or ""),
                "dag_run_id": str(row.get("airflow_dag_run_id") or ""),
                "expected_states": [state],
            },
            user=user,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "airflow neutralization failed for run %s", row.get("run_id"), exc_info=True
        )
        return "failed"
    if not isinstance(result, dict) or result.get("error"):
        return "failed"
    if result.get("marked"):
        return "ok"
    if result.get("found") is False:
        return "gone"
    if result.get("reason") == "state_changed":
        return "state_changed"
    return "failed"


async def _apply_mark_failed(
    row: Mapping[str, Any],
    verdict: RunVerdict,
    truth: AirflowRunTruth | None,
    *,
    get_db_pool: GetDbPool,
    record_event: RecordEvent,
    user: dict | None,
    actor: str,
    neutralized: bool,
    digest: str,
) -> dict[str, Any] | None:
    reconciliation = {
        "reason": verdict.classification,
        "actor": actor,
        "source": "console.stuck_run_recovery",
        "airflow_state": truth.state if truth else None,
        "airflow_neutralized": neutralized,
    }
    recovery = {
        "reason": verdict.classification,
        "airflow_state": truth.state if truth else None,
        "dag_paused": truth.dag_paused if truth else None,
        "actor": actor,
    }
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                SET_SCOPE_SQL, str(row["tenant_id"]), str(row["workspace_id"])
            )
            updated = await conn.fetchrow(
                recover_update_sql(),
                str(row["run_id"]),
                str(row["tenant_id"]),
                str(row["workspace_id"]),
                normalize_pipeline_status(row.get("status")),
                row.get("started_at"),
                int(row.get("fencing_token") or 0),
                json.dumps(reconciliation, sort_keys=True),
                json.dumps(recovery, sort_keys=True),
                RECOVERY_MESSAGE,
            )
            if updated is None:
                return None
            await record_event(
                connection=conn,
                critical=True,
                user_id=(user or {}).get("id"),
                email=(user or {}).get("email") or actor,
                action=AUDIT_ACTION,
                resource_type="pipeline_run",
                resource_id=str(row["run_id"]),
                status="success",
                metadata={
                    "severity": "critical",
                    "actor": actor,
                    "tenant_id": str(row["tenant_id"]),
                    "workspace_id": str(row["workspace_id"]),
                    "dag_id": str(row.get("dag_id") or ""),
                    "cartridge_id": str(row.get("cartridge_id") or ""),
                    "entity": str(row.get("entity") or ""),
                    "classification": verdict.classification,
                    "previous_status": normalize_pipeline_status(row.get("status")),
                    "expected_fencing_token": int(row.get("fencing_token") or 0),
                    "airflow_state": truth.state if truth else None,
                    "airflow_neutralized": neutralized,
                    "plan_digest": digest,
                },
            )
            return dict(updated)


async def _default_record_event(**kwargs: Any) -> None:
    from app.services import audit_service

    await audit_service.record_event(**kwargs)


async def recover_stuck_runs(
    user: dict,
    *,
    mode: str,
    actor: str,
    invoke: McpInvoke,
    get_db_pool: GetDbPool,
    refresh_dag_run_status: RefreshRun,
    cartridge: str | None = None,
    dag_ids: Sequence[str] | None = None,
    threshold: timedelta = DEFAULT_THRESHOLD,
    allowed_classes: Collection[str] | None = None,
    neutralize_airflow: bool = True,
    expected_plan_digest: str | None = None,
    exclude_run_ids: Collection[str] = (),
    record_event: RecordEvent | None = None,
    now: datetime | None = None,
    max_candidates: int = MAX_CANDIDATES,
    max_age_seconds: int | None = None,
    concurrency: int = 5,
    observe_timeout: float = 20.0,
) -> RecoveryReport:
    if mode not in {MODE_DRY_RUN, MODE_APPLY}:
        raise ValueError("mode must be dry_run or apply")
    tenant_id, workspace_id = _scope(user)
    allowed = frozenset(allowed_classes or DEFAULT_ALLOWED_CLASSES)
    limit = max(1, min(int(max_candidates), MAX_CANDIDATES))
    checked_at = as_utc_datetime(now) or datetime.now(timezone.utc)

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
            rows = await load_recovery_candidates(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                cartridge=cartridge,
                dag_ids=dag_ids,
                exclude_run_ids=exclude_run_ids,
                max_age_seconds=max_age_seconds,
                limit=limit,
            )
    truncated = len(rows) > limit
    rows = rows[:limit]

    needs_airflow = [row for row in rows if pre_airflow_verdict(row) is None]
    truths = (
        await observe_airflow_truth(
            needs_airflow,
            invoke=invoke,
            user=user,
            concurrency=concurrency,
            timeout=observe_timeout,
        )
        if needs_airflow
        else {}
    )

    counts = _count_template()
    counts["candidates"] = len(rows)
    planned: list[tuple[dict[str, Any], RunVerdict, str, bool, dict[str, Any]]] = []
    verdicts: dict[str, RunVerdict] = {}
    for row in rows:
        run_id = str(row.get("run_id") or "")
        verdict = classify_stuck_run(
            row,
            truths.get(run_id),
            now=checked_at,
            threshold=threshold,
            missing_grace=DEFAULT_MISSING_GRACE,
        )
        verdicts[run_id] = verdict
        action, neutralize = _planned_action(
            verdict, allowed_classes=allowed, neutralize_airflow=neutralize_airflow
        )
        if verdict.classification in IN_PROGRESS_CLASSES:
            counts["live"] += 1
        elif verdict.classification == UNVERIFIABLE:
            counts["unverifiable"] += 1
        elif verdict.classification == NOT_APPLICABLE:
            counts["not_applicable"] += 1
        if action != ACTION_NONE:
            counts["recoverable"] += 1
        entry = _run_entry(row, verdict, now=checked_at, action=action, neutralize=neutralize)
        planned.append((row, verdict, action, neutralize, entry))

    digest = plan_digest(
        {**plan_entry(row, verdict), "action": action}
        for row, verdict, action, _neutralize, _entry in planned
    )

    if mode == MODE_APPLY:
        if expected_plan_digest is not None and expected_plan_digest != digest:
            raise PlanChanged(digest)
        record = record_event or _default_record_event
        for row, verdict, action, neutralize, entry in planned:
            if action == ACTION_NONE:
                continue
            run_id = str(row.get("run_id") or "")
            truth = truths.get(run_id)
            if action == ACTION_SYNC_TERMINAL:
                try:
                    refreshed = await refresh_dag_run_status(dict(row), user)
                except Exception:  # noqa: BLE001
                    logger.warning("terminal sync failed for run %s", run_id, exc_info=True)
                    counts["conflicts"] += 1
                    continue
                status_after = normalize_pipeline_status((refreshed or {}).get("status"))
                if status_after in PIPELINE_TERMINAL_STATUSES:
                    counts["synced_terminal"] += 1
                    entry["status_after"] = status_after
                else:
                    counts["conflicts"] += 1
                continue
            if action != ACTION_MARK_FAILED:
                continue
            neutralized = False
            if neutralize:
                outcome = await _neutralize(row, truth, invoke=invoke, user=user)
                if outcome == "ok":
                    counts["airflow_neutralized"] += 1
                    neutralized = True
                elif outcome == "state_changed":
                    counts["conflicts"] += 1
                    continue
                elif outcome != "gone":
                    counts["airflow_neutralize_failed"] += 1
                    continue
            try:
                updated = await _apply_mark_failed(
                    row,
                    verdict,
                    truth,
                    get_db_pool=get_db_pool,
                    record_event=record,
                    user=user,
                    actor=actor,
                    neutralized=neutralized,
                    digest=digest,
                )
            except Exception:  # noqa: BLE001
                logger.warning("stuck run recovery failed for run %s", run_id, exc_info=True)
                counts["conflicts"] += 1
                continue
            if updated is None:
                counts["conflicts"] += 1
                continue
            counts["recovered"] += 1
            entry["status_after"] = normalize_pipeline_status(updated.get("status"))

    return RecoveryReport(
        mode="applied" if mode == MODE_APPLY else MODE_DRY_RUN,
        checked_at=checked_at,
        threshold_minutes=max(1, int(threshold.total_seconds() // 60)),
        plan_digest=digest,
        counts=counts,
        runs=[entry for *_rest, entry in planned],
        truncated=truncated,
        message_es=_message_es(mode, counts, truncated),
        verdicts=verdicts,
    )


async def console_run_ids(
    user: dict | None,
    *,
    dag_id: str,
    run_ids: Sequence[str],
    get_db_pool: GetDbPool,
) -> set[str]:
    """Airflow run ids that have a console row in the caller's scope."""
    if not run_ids:
        return set()
    tenant_id, workspace_id = _scope(user)
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
            rows = await conn.fetch(
                """
                SELECT airflow_dag_run_id
                  FROM pipeline_runs
                 WHERE tenant_id = $1::uuid
                   AND workspace_id = $2::uuid
                   AND dag_id = $3
                   AND airflow_dag_run_id = ANY($4::text[])
                """,
                tenant_id,
                workspace_id,
                dag_id,
                list(run_ids)[:MAX_CANDIDATES],
            )
    return {str(row["airflow_dag_run_id"]) for row in rows}


def pre_trigger_allowed_classes(neutralize: bool) -> frozenset[str]:
    base = {AIRFLOW_TERMINAL, MISSING_IN_AIRFLOW}
    if neutralize:
        base |= STALLED_CLASSES
    return frozenset(base)


async def recover_before_reservation(
    user: dict | None,
    *,
    cartridge: str,
    dag_ids: Sequence[str],
    invoke: McpInvoke,
    get_db_pool: GetDbPool,
    refresh_dag_run_status: RefreshRun,
    neutralize: bool = False,
    timeout: float = 8.0,
    threshold: timedelta = DEFAULT_THRESHOLD,
    max_age_seconds: int | None = None,
    max_candidates: int = 50,
) -> RecoveryReport | None:
    """Close runs Airflow already settled before a slot lookup; fail open."""
    if not user:
        return None
    try:
        return await asyncio.wait_for(
            recover_stuck_runs(
                user,
                mode=MODE_APPLY,
                actor=PRE_TRIGGER_ACTOR,
                invoke=invoke,
                get_db_pool=get_db_pool,
                refresh_dag_run_status=refresh_dag_run_status,
                cartridge=cartridge,
                dag_ids=dag_ids,
                threshold=threshold,
                allowed_classes=pre_trigger_allowed_classes(neutralize),
                neutralize_airflow=neutralize,
                max_candidates=max_candidates,
                max_age_seconds=max_age_seconds,
                observe_timeout=max(1.0, timeout - 1.0),
            ),
            timeout=max(0.5, timeout),
        )
    except asyncio.TimeoutError:
        logger.warning("pre-trigger stuck run recovery timed out for %s", cartridge)
    except HTTPException:
        logger.debug("pre-trigger stuck run recovery skipped", exc_info=True)
    except Exception:  # noqa: BLE001 - recovery must never block a trigger
        logger.warning("pre-trigger stuck run recovery failed", exc_info=True)
    return None


async def default_invoke(server: str, tool: str, args: dict, *, user: dict | None = None):
    from app.services import mcp_registry

    return await mcp_registry.invoke(server, tool, args, user=user)


async def default_get_db_pool():
    from app.services import db_pool

    return await db_pool.get_db_pool()


async def default_refresh_dag_run_status(row: dict, user: dict | None = None) -> dict:
    from app.domains.pipeline import recording, run_state
    from app.services.security_context import build_security_context

    return await recording.refresh_dag_run_status(
        row,
        user,
        mcp_invoke=default_invoke,
        get_db_pool=default_get_db_pool,
        build_security_context=build_security_context,
        normalize_airflow_state=run_state.normalize_airflow_state,
        parse_iso_datetime=run_state.parse_iso_datetime,
        duration_seconds=run_state.duration_seconds,
        logger_debug=logger.debug,
    )


__all__ = (
    "DEFAULT_ALLOWED_CLASSES",
    "MAX_CANDIDATES",
    "MODE_APPLY",
    "MODE_DRY_RUN",
    "PlanChanged",
    "RecoveryReport",
    "console_run_ids",
    "default_get_db_pool",
    "default_invoke",
    "default_refresh_dag_run_status",
    "load_recovery_candidates",
    "observe_airflow_truth",
    "pre_trigger_allowed_classes",
    "recover_before_reservation",
    "recover_stuck_runs",
)
