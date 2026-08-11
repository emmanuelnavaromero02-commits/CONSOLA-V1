"""SQL fragments for monotonic writes to the shared pipeline run registry.

The SuccessFactors service is packaged independently from Console and
mcp-infra, so it keeps a small local copy of the status contract.  A
cross-service test fails if the three registries drift.
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
    "postgres_monotonic_status",
    "postgres_status_accepts",
)
