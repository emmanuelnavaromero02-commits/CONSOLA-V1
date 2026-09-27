from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains.pipeline.run_state import parse_iso_datetime, pipeline_run_extra
from app.domains.pipeline.status_transitions import (
    PIPELINE_TERMINAL_STATUSES,
    normalize_pipeline_status,
    postgres_monotonic_status,
)


RECOVERY_MESSAGE = "Recuperado por el sistema: tiempo de espera agotado"
RECOVERY_SCHEMA_VERSION = "pipeline-recovery/v1"
DEFAULT_THRESHOLD = timedelta(minutes=15)
DEFAULT_MISSING_GRACE = timedelta(minutes=2)

MANUAL_TRIGGER_DAG_RE = re.compile(r"^[a-z][a-z0-9_]*_extract(_all)?$")
SYNC_NOW_DAG_ID = "sync_now"
SYNC_NOW_ENTITY = "__sync_now__"
LEASE_GOVERNED_DAG_IDS = frozenset({"dataset_refresh_chain"})
CANDIDATE_STATUSES = ("queued", "running", "unknown", "scheduled", "up_for_retry")

NOT_APPLICABLE = "not_applicable"
TOO_RECENT = "too_recent"
LIVE = "live"
WAITING_TURN = "waiting_turn"
AIRFLOW_TERMINAL = "airflow_terminal"
MISSING_IN_AIRFLOW = "missing_in_airflow"
STALLED_QUEUED_PAUSED_DAG = "stalled_queued_paused_dag"
STALLED_QUEUED_NO_PROGRESS = "stalled_queued_no_progress"
STALLED_RUNNING_NO_TASKS = "stalled_running_no_tasks"
UNVERIFIABLE = "unverifiable"
AIRFLOW_ORPHAN = "airflow_orphan"

CLASSIFICATIONS = (
    NOT_APPLICABLE,
    TOO_RECENT,
    LIVE,
    WAITING_TURN,
    AIRFLOW_TERMINAL,
    MISSING_IN_AIRFLOW,
    STALLED_QUEUED_PAUSED_DAG,
    STALLED_QUEUED_NO_PROGRESS,
    STALLED_RUNNING_NO_TASKS,
    UNVERIFIABLE,
    AIRFLOW_ORPHAN,
)
STALLED_CLASSES = frozenset(
    {
        STALLED_QUEUED_PAUSED_DAG,
        STALLED_QUEUED_NO_PROGRESS,
        STALLED_RUNNING_NO_TASKS,
    }
)
MARK_FAILED_CLASSES = frozenset({MISSING_IN_AIRFLOW, *STALLED_CLASSES})
IN_PROGRESS_CLASSES = frozenset({TOO_RECENT, LIVE, WAITING_TURN})

ACTION_MARK_FAILED = "mark_failed"
ACTION_SYNC_TERMINAL = "sync_terminal"
ACTION_NONE = "none"
ACTION_NEUTRALIZE_AIRFLOW = "neutralize_airflow"

ACTIVE_TASK_STATES = frozenset(
    {
        "running",
        "queued",
        "scheduled",
        "up_for_retry",
        "up_for_reschedule",
        "deferred",
        "restarting",
    }
)
AIRFLOW_TERMINAL_RUN_STATES = frozenset({"success", "failed"})

_REASONS_ES = {
    "terminal": "La corrida ya terminó.",
    "sync_now": (
        "Sincronización agregada: su estado se calcula a partir de sus corridas hijas."
    ),
    "no_airflow_run": "La corrida no tiene identificador de Airflow.",
    "lease": "La corrida se gobierna por su propia concesión de ejecución.",
    TOO_RECENT: "Corrida reciente: sigue dentro del tiempo de espera normal.",
    LIVE: "Airflow confirma que la corrida sigue trabajando.",
    WAITING_TURN: "En espera de turno: otra corrida del mismo proceso está en curso.",
    AIRFLOW_TERMINAL: "Airflow ya terminó esta corrida; se sincroniza su estado final.",
    MISSING_IN_AIRFLOW: "Airflow no tiene registro de esta corrida.",
    STALLED_QUEUED_PAUSED_DAG: (
        "La corrida quedó en cola con el proceso pausado en Airflow."
    ),
    STALLED_QUEUED_NO_PROGRESS: (
        "La corrida lleva en cola más del tiempo límite sin que Airflow la inicie."
    ),
    STALLED_RUNNING_NO_TASKS: (
        "La corrida figura en ejecución, pero Airflow no tiene tareas activas."
    ),
    UNVERIFIABLE: "No se pudo verificar el estado en Airflow; no se modifica.",
    "scheduler_unhealthy": (
        "El planificador de Airflow no responde; no se modifica la corrida."
    ),
    "dag_missing": (
        "Airflow no reconoce el proceso de esta corrida; no se modifica."
    ),
    AIRFLOW_ORPHAN: (
        "La consola ya cerró esta corrida, pero Airflow la mantiene pendiente con "
        "el proceso pausado; se detiene en Airflow."
    ),
}


@dataclass(frozen=True)
class AirflowRunTruth:
    found: bool | None = None
    state: str | None = None
    queued_at: datetime | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    active_tasks: int | None = None
    last_task_activity_at: datetime | None = None
    dag_found: bool | None = None
    dag_paused: bool | None = None
    schedule_kind: str | None = None
    sibling_running: bool | None = None
    scheduler_healthy: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class RunVerdict:
    classification: str
    action: str
    neutralize_airflow: bool
    certain: bool
    reason_es: str


def _verdict(
    classification: str,
    *,
    reason_key: str | None = None,
    action: str = ACTION_NONE,
    neutralize: bool = False,
    certain: bool = True,
) -> RunVerdict:
    return RunVerdict(
        classification=classification,
        action=action,
        neutralize_airflow=neutralize,
        certain=certain,
        reason_es=_REASONS_ES[reason_key or classification],
    )


def as_utc_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_iso_datetime(str(value))
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age(now: datetime, value: Any) -> timedelta | None:
    moment = as_utc_datetime(value)
    if moment is None:
        return None
    return now - moment


def pre_airflow_verdict(row: Mapping[str, Any]) -> RunVerdict | None:
    """Verdicts that never need Airflow: terminal, aggregate sync, leases."""
    status = normalize_pipeline_status(row.get("status"))
    if status in PIPELINE_TERMINAL_STATUSES:
        return _verdict(NOT_APPLICABLE, reason_key="terminal")
    if (
        str(row.get("dag_id") or "") == SYNC_NOW_DAG_ID
        or str(row.get("entity") or "") == SYNC_NOW_ENTITY
    ):
        return _verdict(NOT_APPLICABLE, reason_key="sync_now")
    if (
        str(row.get("dag_id") or "") in LEASE_GOVERNED_DAG_IDS
        or row.get("lease_expires_at") is not None
    ):
        return _verdict(NOT_APPLICABLE, reason_key="lease")
    if not str(row.get("airflow_dag_run_id") or "").strip() or not str(
        row.get("dag_id") or ""
    ).strip():
        return _verdict(NOT_APPLICABLE, reason_key="no_airflow_run")
    return None


def _is_reserved(row: Mapping[str, Any]) -> bool:
    return bool(pipeline_run_extra(dict(row)).get("reserved"))


def classify_stuck_run(
    row: Mapping[str, Any],
    truth: AirflowRunTruth | None,
    *,
    now: datetime,
    threshold: timedelta = DEFAULT_THRESHOLD,
    missing_grace: timedelta = DEFAULT_MISSING_GRACE,
) -> RunVerdict:
    """Classify one console run against the Airflow truth observed for it.

    Time only gates how long to wait; every write decision rests on an
    Airflow observation. Without a reliable observation nothing changes.
    """
    now = as_utc_datetime(now) or datetime.now(timezone.utc)
    early = pre_airflow_verdict(row)
    if early is not None:
        return early
    if truth is None or truth.error:
        return _verdict(UNVERIFIABLE, certain=False)

    state = str(truth.state or "").strip().lower()
    if truth.found is True and state in AIRFLOW_TERMINAL_RUN_STATES:
        return _verdict(AIRFLOW_TERMINAL, action=ACTION_SYNC_TERMINAL)

    age = _age(now, row.get("started_at"))
    if truth.found is False:
        if truth.dag_found is not True:
            return _verdict(UNVERIFIABLE, reason_key="dag_missing", certain=False)
        grace = missing_grace if _is_reserved(row) else threshold
        if age is not None and age < grace:
            return _verdict(TOO_RECENT)
        return _verdict(MISSING_IN_AIRFLOW, action=ACTION_MARK_FAILED)

    if truth.found is not True:
        return _verdict(UNVERIFIABLE, certain=False)
    if age is not None and age < threshold:
        return _verdict(TOO_RECENT)

    if state == "running":
        if truth.active_tasks is None:
            return _verdict(UNVERIFIABLE, certain=False)
        if truth.active_tasks > 0:
            return _verdict(LIVE)
        last_activity = (
            truth.last_task_activity_at or truth.start_date or row.get("started_at")
        )
        idle = _age(now, last_activity)
        if idle is not None and idle < threshold:
            return _verdict(LIVE)
        if truth.dag_paused is True:
            # A paused DAG schedules no further tasks: the pause explains it.
            return _verdict(
                STALLED_RUNNING_NO_TASKS, action=ACTION_MARK_FAILED, neutralize=True
            )
        if truth.scheduler_healthy is not True:
            return _verdict(
                UNVERIFIABLE, reason_key="scheduler_unhealthy", certain=False
            )
        return _verdict(
            STALLED_RUNNING_NO_TASKS, action=ACTION_MARK_FAILED, neutralize=True
        )

    if state == "queued":
        if truth.dag_paused is True:
            return _verdict(
                STALLED_QUEUED_PAUSED_DAG, action=ACTION_MARK_FAILED, neutralize=True
            )
        if truth.dag_paused is not False:
            return _verdict(UNVERIFIABLE, certain=False)
        if truth.scheduler_healthy is not True:
            return _verdict(
                UNVERIFIABLE, reason_key="scheduler_unhealthy", certain=False
            )
        if truth.sibling_running is True:
            return _verdict(WAITING_TURN)
        if truth.sibling_running is not False:
            return _verdict(UNVERIFIABLE, certain=False)
        queued_for = _age(now, truth.queued_at)
        if queued_for is None:
            queued_for = age
        if queued_for is not None and queued_for < threshold:
            return _verdict(TOO_RECENT)
        return _verdict(
            STALLED_QUEUED_NO_PROGRESS, action=ACTION_MARK_FAILED, neutralize=True
        )

    return _verdict(UNVERIFIABLE, certain=False)


def orphan_verdict() -> RunVerdict:
    """An own Airflow run left pending in a paused DAG after the console closed it."""
    return _verdict(
        AIRFLOW_ORPHAN, action=ACTION_NEUTRALIZE_AIRFLOW, neutralize=True
    )


def recover_update_sql() -> str:
    """Compare-and-swap that closes one stuck run; never reopens or deletes.

    $1 run_id, $2 tenant_id, $3 workspace_id, $4 expected status (lower),
    $5 expected started_at, $6 expected fencing token, $7 reconciliation
    entry (jsonb), $8 recovery evidence (jsonb), $9 public error message.
    """
    monotonic = postgres_monotonic_status("status", "'failed'::text")
    return f"""
        UPDATE pipeline_runs
           SET status = {monotonic},
               finished_at = COALESCE(finished_at, NOW()),
               error_message = $9::text,
               lease_expires_at = NULL,
               fencing_token = fencing_token + 1,
               extra = COALESCE(extra, '{{}}'::jsonb) || jsonb_build_object(
                   'reconciliation',
                   COALESCE(
                       CASE WHEN jsonb_typeof(extra->'reconciliation') = 'array'
                            THEN extra->'reconciliation'
                            ELSE '[]'::jsonb END,
                       '[]'::jsonb
                   ) || jsonb_build_array(
                       $7::jsonb || jsonb_build_object('applied_at', NOW())
                   ),
                   'recovery',
                   $8::jsonb || jsonb_build_object(
                       'previous_error', error_message,
                       'applied_at', NOW()
                   )
               )
         WHERE run_id = $1
           AND tenant_id = $2::uuid
           AND workspace_id = $3::uuid
           AND LOWER(COALESCE(status, 'unknown')) = $4
           AND started_at IS NOT DISTINCT FROM $5::timestamptz
           AND fencing_token = $6
         RETURNING run_id, status, fencing_token, finished_at
    """


def _iso(value: Any) -> str | None:
    moment = as_utc_datetime(value)
    return moment.isoformat() if moment else None


def plan_entry(
    row: Mapping[str, Any], verdict: RunVerdict
) -> dict[str, Any]:
    return {
        "run_id": str(row.get("run_id") or ""),
        "status": normalize_pipeline_status(row.get("status")),
        "started_at": _iso(row.get("started_at")),
        "fencing_token": int(row.get("fencing_token") or 0),
        "classification": verdict.classification,
        "action": verdict.action,
    }


def plan_digest(entries: Iterable[Mapping[str, Any]]) -> str:
    """SHA-256 over the actionable plan an operator is asked to approve."""
    canonical = sorted(
        (
            {
                "run_id": str(entry.get("run_id") or ""),
                "status": normalize_pipeline_status(entry.get("status")),
                "started_at": entry.get("started_at"),
                "fencing_token": int(entry.get("fencing_token") or 0),
                "classification": str(entry.get("classification") or ""),
                "action": str(entry.get("action") or ""),
            }
            for entry in entries
            if str(entry.get("action") or ACTION_NONE) != ACTION_NONE
        ),
        key=lambda item: item["run_id"],
    )
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = (
    "ACTION_MARK_FAILED",
    "ACTION_NEUTRALIZE_AIRFLOW",
    "AIRFLOW_ORPHAN",
    "MANUAL_TRIGGER_DAG_RE",
    "ACTION_NONE",
    "ACTION_SYNC_TERMINAL",
    "AIRFLOW_TERMINAL",
    "AirflowRunTruth",
    "CANDIDATE_STATUSES",
    "CLASSIFICATIONS",
    "DEFAULT_MISSING_GRACE",
    "DEFAULT_THRESHOLD",
    "IN_PROGRESS_CLASSES",
    "LIVE",
    "MARK_FAILED_CLASSES",
    "MISSING_IN_AIRFLOW",
    "NOT_APPLICABLE",
    "RECOVERY_MESSAGE",
    "RECOVERY_SCHEMA_VERSION",
    "RunVerdict",
    "STALLED_CLASSES",
    "STALLED_QUEUED_NO_PROGRESS",
    "STALLED_QUEUED_PAUSED_DAG",
    "STALLED_RUNNING_NO_TASKS",
    "TOO_RECENT",
    "UNVERIFIABLE",
    "WAITING_TURN",
    "as_utc_datetime",
    "classify_stuck_run",
    "orphan_verdict",
    "plan_digest",
    "plan_entry",
    "pre_airflow_verdict",
    "recover_update_sql",
)
