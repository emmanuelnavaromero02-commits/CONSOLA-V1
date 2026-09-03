from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_TRACE: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "intelligence_publication_trace", default=None
)


@contextmanager
def capture_publication_trace() -> Iterator[dict[str, dict[str, Any]]]:
    trace: dict[str, dict[str, Any]] = {}
    token = _TRACE.set(trace)
    try:
        yield trace
    finally:
        _TRACE.reset(token)


def record_publication_read(dataset: str, relation: Any) -> None:
    trace = _TRACE.get()
    if trace is None:
        return
    binding = {
        "dataset": str(dataset),
        "materialization_run_id": str(relation.run_id),
        "receipt_id": str(relation.receipt_id or ""),
        "head_generation": int(relation.generation),
        "object_checksum": str(relation.object_checksum or ""),
        "evidence_digest": str(relation.evidence_digest or ""),
        "object_uri": str(relation.object_uri or ""),
        "object_version": str(relation.object_version or ""),
        "schema_digest": str(relation.schema_digest or ""),
        "row_count": relation.row_count,
        "published_at": (
            relation.published_at.isoformat() if relation.published_at else ""
        ),
    }
    row_count = binding["row_count"]
    required = {key: value for key, value in binding.items() if key != "row_count"}
    if (
        not all(required.values())
        or isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count < 0
    ):
        raise RuntimeError("published Gold authority is incomplete")
    previous = trace.get(str(dataset))
    if previous is not None and previous != binding:
        raise RuntimeError("published Gold head changed during Intelligence run")
    trace[str(dataset)] = binding


def current_publication_trace() -> dict[str, dict[str, Any]]:
    return dict(_TRACE.get() or {})


def publication_trace_active() -> bool:
    return _TRACE.get() is not None


def exact_bindings(
    trace: dict[str, dict[str, Any]], expected_datasets: list[str]
) -> list[dict[str, Any]]:
    expected = sorted(set(expected_datasets))
    if sorted(trace) != expected:
        raise RuntimeError("Intelligence publication trace is incomplete")
    return [trace[name] for name in expected]
