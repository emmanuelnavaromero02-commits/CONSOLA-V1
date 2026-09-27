from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains.pipeline.run_state import (
    pipeline_downstream_status,
    pipeline_entity_run_payload,
    pipeline_run_extra,
)
from app.domains.pipeline.status_transitions import (
    PIPELINE_TERMINAL_STATUSES,
    normalize_pipeline_status,
)
from app.domains.pipeline.stuck_run_recovery import RECOVERY_MESSAGE, as_utc_datetime


AGGREGATE_ENTITY = "__extract_all__"
PHASES = ("connecting", "extracting", "building", "ready")
MATERIALIZATION_TRANSITIONS = frozenset({"gold_materialized", "anomalies_detected"})
ACTIVE_DOWNSTREAM_STATUSES = frozenset({"queued", "running"})
STALLABLE_STATUSES = frozenset({"queued", "scheduled"})
SUCCESS_STATUSES = frozenset({"success", "noop"})
PARTIAL_STATUSES = frozenset({"partial", "blocked", "skipped", "skipped_explicit"})
MAX_ERROR_CHARS = 500


PARTIAL_CLASSIFICATION_CODES = frozenset(
    {"SUCCESSFACTORS_METADATA_BLOCKED", "SUCCESSFACTORS_PERMISSION"}
)


def _terminal(status: str) -> bool:
    return status in PIPELINE_TERMINAL_STATUSES


def _status(row: Mapping[str, Any]) -> str:
    status = normalize_pipeline_status(row.get("status"))
    classification = pipeline_run_extra(dict(row)).get("classification")
    if (
        _terminal(status)
        and isinstance(classification, dict)
        and classification.get("code") in PARTIAL_CLASSIFICATION_CODES
    ):
        return "partial"
    return status


def _outcome(status: str) -> str | None:
    if status in SUCCESS_STATUSES:
        return "success"
    if status in PARTIAL_STATUSES:
        return "partial"
    if _terminal(status):
        return "failed"
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _transition(row: Mapping[str, Any]) -> str:
    return str(pipeline_run_extra(dict(row)).get("transition") or "")


def is_entity_child(row: Mapping[str, Any]) -> bool:
    entity = str(row.get("entity") or "")
    return bool(entity) and not entity.startswith("__") and (
        _transition(row) not in MATERIALIZATION_TRANSITIONS
    )


def _downstream_active(row: Mapping[str, Any]) -> bool:
    extra = pipeline_run_extra(dict(row))
    return any(
        pipeline_downstream_status(extra, key) in ACTIVE_DOWNSTREAM_STATUSES
        for key in ("silver_refresh", "gold_refresh")
    )


def _error_text(row: Mapping[str, Any]) -> str | None:
    payload = pipeline_entity_run_payload(dict(row))
    error = payload.get("error")
    if not error:
        return None
    text = str(error).strip()
    return text[:MAX_ERROR_CHARS] or None


def _observed_running(row: Mapping[str, Any]) -> bool:
    observation = pipeline_run_extra(dict(row)).get("airflow_observation")
    return isinstance(observation, dict) and str(observation.get("state") or "") == "running"


def build_run_progress(
    row: Mapping[str, Any],
    children: Sequence[Mapping[str, Any]] = (),
    *,
    now: datetime,
    stalled_after: timedelta = timedelta(seconds=900),
    run_id: str | None = None,
) -> dict[str, Any]:
    """Map durable pipeline rows to the four user-facing extraction phases.

    Every phase needs evidence in the rows: the queue, an observed run, a
    downstream refresh or finished child entities. Counts stay unknown
    (None) instead of defaulting to zero.
    """
    now = as_utc_datetime(now) or datetime.now(timezone.utc)
    status = _status(row)
    terminal = _terminal(status)
    outcome = _outcome(status)
    extra = pipeline_run_extra(dict(row))
    aggregate = str(row.get("entity") or "") == AGGREGATE_ENTITY

    entity_children = [child for child in children if is_entity_child(child)]
    done_children = [child for child in entity_children if _terminal(_status(child))]
    materializing = [
        child
        for child in children
        if _transition(child) in MATERIALIZATION_TRANSITIONS
    ]

    if aggregate:
        known_counts = [
            count
            for count in (_int_or_none(child.get("record_count")) for child in done_children)
            if count is not None
        ]
        own_count = _int_or_none(row.get("record_count")) if terminal else None
        record_count = own_count if own_count is not None else (
            sum(known_counts) if known_counts else None
        )
        entities_done: int | None = (
            len(done_children) if (done_children or terminal) else None
        )
        entities_total = _int_or_none(extra.get("selected"))
    else:
        record_count = _int_or_none(row.get("record_count"))
        entities_done = None
        entities_total = None

    downstream_active = _downstream_active(row) or any(
        _downstream_active(child) for child in entity_children
    )
    # Child rows are written as each entity finishes, so "every known child is
    # done" is not evidence on its own; the total must be known.
    all_entities_done = bool(
        entity_children
        and len(done_children) == len(entity_children)
        and entities_total is not None
        and len(done_children) >= entities_total
    )
    building_evidence = downstream_active or (
        aggregate
        and status == "running"
        and (bool(materializing) or all_entities_done)
    )

    if terminal and outcome in {"success", "partial"}:
        phase = "building" if downstream_active else "ready"
    elif terminal:
        if building_evidence or materializing:
            phase = "building"
        elif record_count is not None or done_children or _observed_running(row):
            phase = "extracting"
        else:
            phase = "connecting"
    elif status == "running":
        phase = "building" if building_evidence else "extracting"
    else:
        phase = "connecting"

    started_at = as_utc_datetime(row.get("started_at"))
    stalled = bool(
        not terminal
        and status in STALLABLE_STATUSES
        and started_at is not None
        and now - started_at >= stalled_after
    )
    recovered = isinstance(extra.get("recovery"), dict) or (
        str(row.get("error_message") or "") == RECOVERY_MESSAGE
    )
    return {
        "run_id": str(run_id or row.get("run_id") or ""),
        "entity": str(row.get("entity") or ""),
        "phase": phase,
        "phase_index": PHASES.index(phase) + 1,
        "status": status,
        "terminal": terminal,
        "outcome": outcome,
        "record_count": record_count,
        "entities_done": entities_done,
        "entities_total": entities_total,
        "error": _error_text(row) if outcome in {"failed", "partial"} else None,
        "recovered": recovered,
        "stalled": stalled,
        "started_at": started_at,
        "finished_at": as_utc_datetime(row.get("finished_at")),
    }


def resolve_requested_run(
    requested_id: str, rows: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """The row a client asked for: its own run id, else the aggregate parent."""
    for row in rows:
        if str(row.get("run_id") or "") == requested_id:
            return row
    for row in rows:
        if (
            str(row.get("airflow_dag_run_id") or "") == requested_id
            and str(row.get("entity") or "") == AGGREGATE_ENTITY
        ):
            return row
    for row in rows:
        if str(row.get("airflow_dag_run_id") or "") == requested_id:
            return row
    return None


def children_of(
    parent: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    if str(parent.get("entity") or "") != AGGREGATE_ENTITY:
        return []
    airflow_id = str(parent.get("airflow_dag_run_id") or parent.get("run_id") or "")
    parent_run = str(parent.get("run_id") or "")
    return [
        row
        for row in rows
        if str(row.get("airflow_dag_run_id") or "") == airflow_id
        and str(row.get("run_id") or "") != parent_run
    ]


def needs_airflow_refresh(
    row: Mapping[str, Any], *, now: datetime, min_age: timedelta = timedelta(seconds=5)
) -> bool:
    if normalize_pipeline_status(row.get("status")) not in {"queued", "running", "unknown"}:
        return False
    observation = pipeline_run_extra(dict(row)).get("airflow_observation")
    observed_at = (
        as_utc_datetime(observation.get("observed_at"))
        if isinstance(observation, dict)
        else None
    )
    if observed_at is None:
        return True
    return (as_utc_datetime(now) or datetime.now(timezone.utc)) - observed_at >= min_age


__all__ = (
    "AGGREGATE_ENTITY",
    "PHASES",
    "build_run_progress",
    "children_of",
    "is_entity_child",
    "needs_airflow_refresh",
    "resolve_requested_run",
)
