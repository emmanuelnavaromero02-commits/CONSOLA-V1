"""Fail-closed status ordering for durable pipeline run state.

The registry is written by Console immediately after an Airflow trigger is
acknowledged, by Airflow tasks when product work completes, and by Console
again while polling Airflow.  Those writers can arrive in any order.  The
ordering below treats a more conclusive or more severe outcome as monotonic:
late acknowledgements cannot move a terminal run back to queued/running, and
an Airflow-level success can still be refined to a product-level partial or
failure.
"""

from __future__ import annotations


PIPELINE_STATUS_RANKS = {
    "unknown": 0,
    "queued": 10,
    "scheduled": 10,
    "up_for_retry": 15,
    "running": 20,
    "success": 30,
    "noop": 30,
    "skipped": 35,
    "skipped_explicit": 35,
    "partial": 40,
    "blocked": 50,
    "failed": 60,
    "error": 60,
    "upstream_failed": 60,
    "cancelled": 60,
    "removed": 60,
}

PIPELINE_TERMINAL_STATUSES = frozenset(
    status for status, rank in PIPELINE_STATUS_RANKS.items() if rank >= 30
)


def normalize_pipeline_status(value: object | None) -> str:
    return str(value or "unknown").strip().lower() or "unknown"


def pipeline_status_rank(value: object | None) -> int:
    return PIPELINE_STATUS_RANKS.get(normalize_pipeline_status(value), 0)


def advance_pipeline_status(current: object | None, incoming: object | None) -> str:
    """Return the monotonic state selected from concurrent observations.

    Equal-rank aliases keep the already persisted spelling.  Repeating the
    same state remains idempotent.  Unknown future values are therefore never
    allowed to erase a known state.
    """

    current_status = normalize_pipeline_status(current)
    incoming_status = normalize_pipeline_status(incoming)
    if pipeline_status_rank(incoming_status) > pipeline_status_rank(current_status):
        return incoming_status
    return current_status


def pipeline_status_accepts(current: object | None, incoming: object | None) -> bool:
    """Whether metadata belonging to ``incoming`` may replace current facts."""

    current_status = normalize_pipeline_status(current)
    incoming_status = normalize_pipeline_status(incoming)
    return incoming_status == current_status or pipeline_status_rank(
        incoming_status
    ) > pipeline_status_rank(current_status)


def postgres_status_rank(expression: str) -> str:
    """Render a rank expression for a trusted SQL identifier/expression."""

    cases = " ".join(
        f"WHEN '{status}' THEN {rank}" for status, rank in PIPELINE_STATUS_RANKS.items()
    )
    return f"(CASE LOWER(COALESCE({expression}, 'unknown')) {cases} ELSE 0 END)"


def postgres_status_advances(current: str, incoming: str) -> str:
    return f"{postgres_status_rank(incoming)} > {postgres_status_rank(current)}"


def postgres_status_accepts(current: str, incoming: str) -> str:
    advances = postgres_status_advances(current, incoming)
    same = f"LOWER(COALESCE({incoming}, 'unknown')) = LOWER(COALESCE({current}, 'unknown'))"
    return f"(({advances}) OR ({same}))"


def postgres_monotonic_status(current: str, incoming: str) -> str:
    advances = postgres_status_advances(current, incoming)
    return f"CASE WHEN {advances} THEN {incoming} ELSE {current} END"


__all__ = (
    "PIPELINE_STATUS_RANKS",
    "PIPELINE_TERMINAL_STATUSES",
    "advance_pipeline_status",
    "normalize_pipeline_status",
    "pipeline_status_accepts",
    "pipeline_status_rank",
    "postgres_monotonic_status",
    "postgres_status_accepts",
    "postgres_status_advances",
    "postgres_status_rank",
)
