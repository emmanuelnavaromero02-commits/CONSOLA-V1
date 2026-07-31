from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.services.intelligence.calibration_authoritative_evidence import (
    resolve_authoritative_observation,
)


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
    where = [
        "workspace_id = $1",
        "calibration_group = $2",
        "model_version = $3",
        "provenance_status = 'verified'",
        "provenance_reason = 'durable_binary_evaluation'",
        "authoritative_calibration_group = calibration_group",
    ]
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
    tenant_id: str | None = None,
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
        try:
            rebuilt = await resolve_authoritative_observation(
                conn,
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                payload={
                    "source_type": str(row.get("source_type") or ""),
                    "source_id": str(row.get("source_id") or ""),
                },
                allow_manual=allow_manual,
            )
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                reason = str(detail.get("reason") or "authoritative_evidence_invalid")
            elif str(row.get("source_type") or "") == "manual_fixture":
                reason = "manual_ancestor"
            else:
                reason = "authoritative_evidence_invalid"
            skipped[reason] += 1
            continue
        if rebuilt.get("calibration_group") != group or row.get(
            "authoritative_calibration_group"
        ) not in (None, group):
            skipped["authoritative_group_mismatch"] += 1
            continue
        trusted.append(rebuilt)
    processed_total = len(trusted)
    skipped_total = sum(skipped.values())
    coverage_complete = (
        processed_total > 0 and processed_total == eligible_total and skipped_total == 0
    )
    metrics = {
        "eligible_total": eligible_total,
        "processed_total": processed_total,
        "skipped_total": skipped_total,
        "skipped_by_reason": dict(sorted(skipped.items())),
        "complete": coverage_complete,
        "provenance_complete": coverage_complete,
        "binary_evaluation_complete": coverage_complete
        and all(row.get("actual_status") in {"hit", "miss"} for row in trusted),
    }
    if not coverage_complete:
        metrics["reason"] = (
            "no_trusted_observations"
            if not trusted
            else "authoritative_recompute_incomplete"
        )
    if metrics["processed_total"] + metrics["skipped_total"] != eligible_total:
        raise BatchFailure(
            "batch_accounting_mismatch", eligible_total, operational_limit
        )
    return trusted, metrics
