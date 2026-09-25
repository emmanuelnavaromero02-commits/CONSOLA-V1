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

    current_status = normalize_pipeline_status(current)
    incoming_status = normalize_pipeline_status(incoming)
    if pipeline_status_rank(incoming_status) > pipeline_status_rank(current_status):
        return incoming_status
    return current_status


def pipeline_status_accepts(current: object | None, incoming: object | None) -> bool:

    current_status = normalize_pipeline_status(current)
    incoming_status = normalize_pipeline_status(incoming)
    return incoming_status == current_status or pipeline_status_rank(
        incoming_status
    ) > pipeline_status_rank(current_status)


def postgres_status_rank(expression: str) -> str:

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
