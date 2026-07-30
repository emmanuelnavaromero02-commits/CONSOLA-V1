from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.services.intelligence.source_provenance import resolve_source_provenance


PAGE_SIZE = 500


@dataclass(frozen=True)
class BatchFailure(Exception):
    reason: str
    eligible_total: int
    operational_limit: int


def _filters(
    workspace_id: str,
    group: str,
    model_version: str,
    source_type: str | None,
    source_id: str | None,
) -> tuple[list[str], list[Any]]:
    params: list[Any] = [workspace_id, group, model_version]
    where = ["workspace_id = $1", "calibration_group = $2", "model_version = $3"]
    for value, column in ((source_type, "source_type"), (source_id, "source_id")):
        if value:
            params.append(value)
            where.append(f"{column} = ${len(params)}")
    return where, params


async def load_complete_batch(
    conn: Any,
    *,
    workspace_id: str,
    group: str,
    model_version: str,
    source_type: str | None,
    source_id: str | None,
    operational_limit: int,
    allow_manual: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    where, base_params = _filters(
        workspace_id, group, model_version, source_type, source_id
    )
    eligible_total = int(
        await conn.fetchval(
            f"SELECT COUNT(*) FROM calibration_observations WHERE {' AND '.join(where)}",
            *base_params,
        )
        or 0
    )
    if eligible_total > operational_limit:
        raise BatchFailure(
            "operational_limit_exceeded", eligible_total, operational_limit
        )

    candidates: list[dict[str, Any]] = []
    seen: set[Any] = set()
    cursor: tuple[Any, Any] | None = None
    while len(candidates) < eligible_total:
        params = list(base_params)
        page_where = list(where)
        if cursor is not None:
            params.extend(cursor)
            page_where.append(
                f"(observed_at, id) > (${len(params) - 1}, ${len(params)})"
            )
        params.append(min(PAGE_SIZE, eligible_total - len(candidates)))
        rows = await conn.fetch(
            f"""
            SELECT * FROM calibration_observations
             WHERE {' AND '.join(page_where)}
             ORDER BY observed_at ASC, id ASC
             LIMIT ${len(params)}
            """,
            *params,
        )
        if not rows:
            raise BatchFailure(
                "observation_set_changed", eligible_total, operational_limit
            )
        for row in rows:
            data = dict(row)
            row_id = data.get("id")
            if row_id in seen:
                raise BatchFailure(
                    "duplicate_observation", eligible_total, operational_limit
                )
            seen.add(row_id)
            candidates.append(data)
        last = candidates[-1]
        cursor = (last.get("observed_at"), last.get("id"))
    if len(candidates) != eligible_total:
        raise BatchFailure("observation_set_changed", eligible_total, operational_limit)
    final_total = int(
        await conn.fetchval(
            f"SELECT COUNT(*) FROM calibration_observations WHERE {' AND '.join(where)}",
            *base_params,
        )
        or 0
    )
    if final_total != eligible_total:
        raise BatchFailure("observation_set_changed", final_total, operational_limit)

    trusted: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    for row in candidates:
        result = await resolve_source_provenance(
            conn,
            workspace_id=workspace_id,
            source_type=str(row.get("source_type") or ""),
            source_id=str(row.get("source_id") or ""),
            allow_manual=allow_manual,
        )
        if result.trusted:
            trusted.append(row)
        else:
            skipped[result.reason] += 1
    metrics = {
        "eligible_total": eligible_total,
        "processed_total": len(trusted),
        "skipped_total": sum(skipped.values()),
        "skipped_by_reason": dict(sorted(skipped.items())),
        "complete": True,
        "provenance_complete": True,
    }
    if metrics["processed_total"] + metrics["skipped_total"] != eligible_total:
        raise BatchFailure(
            "batch_accounting_mismatch", eligible_total, operational_limit
        )
    return trusted, metrics
