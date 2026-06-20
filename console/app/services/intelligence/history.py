from __future__ import annotations

import os
import json
import uuid
from collections import Counter, defaultdict
from statistics import mean
from typing import Any

from fastapi import HTTPException

from app.services import auth, permissions
from app.services.db_scope import scoped_db
from app.services.intelligence.utils import (
    coerce_json_metadata,
    json_dumps,
    num,
    public_json,
    workspace_scope,
)
from app.version import app_version


RUN_MODES = {"manual", "scheduled", "backtest", "smoke", "gold_refresh"}
SUCCESS_STATUSES = {
    "success",
    "succeeded",
    "positive",
    "resolved",
    "completed",
    "hit",
    "true_positive",
}
FAILURE_STATUSES = {
    "failure",
    "failed",
    "negative",
    "miss",
    "false_positive",
    "false_negative",
}
PROBABILITY_BUCKETS = (
    (0.0, 0.2, "0.0-0.2"),
    (0.2, 0.4, "0.2-0.4"),
    (0.4, 0.6, "0.4-0.6"),
    (0.6, 0.8, "0.6-0.8"),
    (0.8, 1.0, "0.8-1.0"),
)

def _actor_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip()
    if text.isdigit():
        parsed = int(text)
        return parsed if parsed > 0 else None
    return None


def _owner_user_id(user: dict) -> int | None:
    return _actor_id(user.get("id"))


def _can_read_workspace_wide(user: dict) -> bool:
    role = permissions.user_role(user)
    scoped = permissions.workspace_role(user)
    return role in {"admin", "owner", "super_admin"} or scoped in {
        "workspace_admin",
        "tenant_admin",
    }


def normalize_run_mode(value: Any) -> str:
    mode = str(value or "manual").strip().lower()
    return mode if mode in RUN_MODES else "manual"


def new_run_ref() -> str:
    return f"intel-run-{uuid.uuid4().hex}"


def _tenant_clause(params: list[Any], tenant_id: str | None, *, alias: str = "") -> str:
    if not tenant_id:
        return ""
    params.append(tenant_id)
    prefix = f"{alias}." if alias else ""
    return f" AND {prefix}tenant_id::text = ${len(params)}"


def _owner_clause(params: list[Any], user: dict, *, alias: str = "") -> str:
    if _can_read_workspace_wide(user):
        return ""
    owner_id = _owner_user_id(user)
    if owner_id is None:
        return " AND false"
    params.append(owner_id)
    prefix = f"{alias}." if alias else ""
    return f" AND {prefix}owner_user_id = ${len(params)}"


def _row_public(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in ("metadata", "request"):
        if key in data:
            data[key] = coerce_json_metadata(data.get(key))
    for key in ("datasets_evaluated", "errors", "decision_intelligence"):
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = [] if key in {"datasets_evaluated", "errors"} else {}
        data[key] = value
    return public_json(data)


def _skip_counts(skipped: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(item.get("status") or "unknown") for item in skipped)


def datasets_from_contracts(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    datasets: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for contract in contracts:
        metrics = (
            contract.get("metrics") if isinstance(contract.get("metrics"), list) else []
        )
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            dataset = str(metric.get("dataset") or "").strip()
            metric_id = str(metric.get("id") or "").strip()
            if not dataset or not metric_id:
                continue
            key = (str(contract.get("cartridge") or ""), dataset, metric_id)
            if key in seen:
                continue
            seen.add(key)
            datasets.append(
                {
                    "cartridge": contract.get("cartridge"),
                    "domain": contract.get("domain"),
                    "dataset": dataset,
                    "metric": metric_id,
                }
            )
    return datasets


async def start_intelligence_run(
    user: dict,
    *,
    request: dict[str, Any] | None,
    source_system: str | None,
    run_mode: str,
    datasets_evaluated: list[dict[str, Any]] | None = None,
    run_ref: str | None = None,
) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    owner_user_id = _owner_user_id(user)
    pool = await auth.pool()
    safe_run_ref = str(run_ref or "").strip() or new_run_ref()
    request_metadata = (
        request.get("metadata")
        if isinstance(request, dict) and isinstance(request.get("metadata"), dict)
        else {}
    )
    metadata = {"created_by": user.get("email")}
    metadata.update(request_metadata)
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO intelligence_runs (
                run_ref, tenant_id, workspace_id, source_system, run_mode, status,
                datasets_evaluated, request, app_version, deploy_ref, owner_user_id, metadata
            )
            VALUES ($1, $2, $3, $4, $5, 'running', $6::jsonb, $7::jsonb, $8, $9, $10, $11::jsonb)
            RETURNING *
            """,
            safe_run_ref,
            tenant_id,
            workspace_id,
            source_system,
            normalize_run_mode(run_mode),
            json_dumps(datasets_evaluated or []),
            json_dumps(request or {}),
            app_version(),
            os.environ.get("DEPLOY_REF") or os.environ.get("GITHUB_SHA"),
            owner_user_id,
            json_dumps(metadata),
        )
    return _row_public(row)


async def get_run_by_ref(user: dict, run_ref: str) -> dict[str, Any] | None:
    tenant_id, workspace_id = workspace_scope(user)
    clean_ref = str(run_ref or "").strip()
    if not clean_ref:
        return None
    params: list[Any] = [workspace_id, clean_ref]
    tenant_clause = _tenant_clause(params, tenant_id)
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            f"""
            SELECT *
              FROM intelligence_runs
             WHERE workspace_id = $1
               AND run_ref = $2
               {tenant_clause}
             LIMIT 1
            """,
            *params,
        )
    return _row_public(row) if row else None


async def finish_intelligence_run(
    user: dict,
    *,
    run_id: int,
    status: str,
    artifacts: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    datasets_evaluated: list[dict[str, Any]],
    duration_ms: int,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    counts = _skip_counts(skipped)
    safe_status = (
        status if status in {"completed", "failed", "not_ready"} else "completed"
    )
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            UPDATE intelligence_runs
               SET status = $3,
                   datasets_evaluated = $4::jsonb,
                   signals_generated = $5,
                   signals_skipped = $6,
                   dataset_unavailable_count = $7,
                   insufficient_history_count = $8,
                   errors = $9::jsonb,
                   metadata = metadata || $10::jsonb,
                   completed_at = NOW(),
                   updated_at = NOW()
             WHERE workspace_id = $1
               AND id = $2
             RETURNING *
            """,
            workspace_id,
            run_id,
            safe_status,
            json_dumps(datasets_evaluated),
            len(artifacts),
            len(skipped),
            counts.get("dataset_unavailable", 0),
            counts.get("insufficient_history", 0),
            json_dumps(errors or []),
            json_dumps({"duration_ms": duration_ms, "skipped_counts": dict(counts)}),
        )
    return _row_public(row)


async def fail_intelligence_run(
    user: dict,
    *,
    run_id: int,
    error: str,
    duration_ms: int,
) -> dict[str, Any] | None:
    tenant_id, workspace_id = workspace_scope(user)
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            UPDATE intelligence_runs
               SET status = 'failed',
                   errors = $3::jsonb,
                   metadata = metadata || $4::jsonb,
                   completed_at = NOW(),
                   updated_at = NOW()
             WHERE workspace_id = $1
               AND id = $2
             RETURNING *
            """,
            workspace_id,
            run_id,
            json_dumps([{"error": error}]),
            json_dumps({"duration_ms": duration_ms}),
        )
    return _row_public(row) if row else None


async def persist_decision_intelligence_snapshot(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    user: dict,
    artifact: dict[str, Any],
    intelligence_run_id: int | None,
    run_ref: str | None,
) -> int | None:
    signal = artifact.get("signal") if isinstance(artifact.get("signal"), dict) else {}
    decision = signal.get("decision_intelligence")
    if not isinstance(decision, dict):
        candidate = artifact.get("decision_intelligence")
        decision = candidate if isinstance(candidate, dict) else {}
    if not signal.get("signal_id"):
        return None
    expected_impact = (
        decision.get("expected_impact")
        if isinstance(decision.get("expected_impact"), dict)
        else {}
    )
    data_quality = (
        decision.get("data_quality")
        if isinstance(decision.get("data_quality"), dict)
        else {}
    )
    source_dataset = signal.get("source_dataset") or signal.get("dataset")
    evidence_pack = (
        artifact.get("evidence_pack")
        if isinstance(artifact.get("evidence_pack"), dict)
        else {}
    )
    evidence_pack_id = evidence_pack.get("id") or signal.get("evidence_pack_id")
    row = await conn.fetchrow(
        """
        INSERT INTO decision_intelligence_snapshots (
            tenant_id, workspace_id, signal_id, control_room_item_id, evidence_pack_id,
            intelligence_run_id, run_ref, source_system, source_dataset, gold_table,
            metric, entity_kind, entity_id, entity_key, period_key, freshness_at,
            decision_intelligence, recommended_decision, anomaly_probability,
            uncertainty_level, expected_impact_value, expected_impact_currency,
            data_quality_status, method, owner_user_id, metadata
        )
        VALUES (
            $1, $2, $3, $4, $5,
            $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16,
            $17::jsonb, $18, $19,
            $20, $21, $22,
            $23, $24, $25, $26::jsonb
        )
        RETURNING id
        """,
        tenant_id,
        workspace_id,
        signal["signal_id"],
        signal["signal_id"],
        evidence_pack_id,
        intelligence_run_id,
        run_ref,
        signal.get("source_system") or signal.get("cartridge_id"),
        source_dataset,
        signal.get("gold_table") or f"gold_{source_dataset}",
        signal.get("metric"),
        signal.get("entity_kind"),
        signal.get("entity_id"),
        signal.get("entity_label") or signal.get("entity_id"),
        signal.get("period_key"),
        signal.get("freshness_at") or signal.get("period_key"),
        json_dumps(decision),
        decision.get("recommended_decision"),
        num(decision.get("anomaly_probability")),
        decision.get("uncertainty_level"),
        num(expected_impact.get("value")),
        expected_impact.get("currency"),
        data_quality.get("status"),
        decision.get("method"),
        _owner_user_id(user),
        json_dumps(
            {
                "signal_subtype": signal.get("signal_subtype") or "observed",
                "prediction_horizon_days": signal.get("prediction_horizon_days"),
                "freshness_field": signal.get("freshness_field"),
                "baseline_id": signal.get("baseline_id"),
                "snapshot_kind": "intelligence_signal",
            }
        ),
    )
    return int(row["id"]) if row else None


async def link_outcome_to_snapshot(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    signal_id: str,
    outcome_row: Any,
    body: dict[str, Any] | None = None,
) -> bool:
    outcome = dict(outcome_row)
    metadata = coerce_json_metadata(outcome.get("metadata"))
    body = body or {}
    measured_impact = num(body.get("measured_impact"))
    if measured_impact is None:
        measured_impact = num(metadata.get("measured_impact"))
    if measured_impact is None:
        measured_impact = num(outcome.get("actual_value"))
    evidence = body.get("evidence") if isinstance(body.get("evidence"), dict) else {}
    outcome_status = (
        str(
            body.get("outcome_status")
            or metadata.get("outcome_status")
            or evidence.get("outcome_status")
            or (
                "observed"
                if measured_impact is not None
                or outcome.get("actual_value") is not None
                else "reported"
            )
        )
        .strip()
        .lower()
    )
    row = await conn.fetchrow(
        """
        WITH candidate AS (
            SELECT id
              FROM decision_intelligence_snapshots
             WHERE workspace_id = $1
               AND signal_id = $2
             ORDER BY
               CASE WHEN outcome_id IS NULL THEN 0 ELSE 1 END,
               created_at DESC
             LIMIT 1
        )
        UPDATE decision_intelligence_snapshots s
           SET outcome_id = $3,
               outcome_observed_at = COALESCE($4::timestamptz, $5::timestamptz),
               outcome_status = $6,
               measured_impact = $7,
               calibration_status = 'outcome_recorded',
               metadata = s.metadata || $8::jsonb,
               updated_at = NOW()
          FROM candidate
         WHERE s.id = candidate.id
         RETURNING s.id
        """,
        workspace_id,
        signal_id,
        outcome.get("id"),
        body.get("observed_at")
        or metadata.get("observed_at")
        or evidence.get("observed_at"),
        outcome.get("created_at"),
        outcome_status,
        measured_impact,
        json_dumps(
            {
                "outcome_linkage": {
                    "action_run_id": metadata.get("action_run_id")
                    or body.get("action_run_id"),
                    "decision_id": metadata.get("decision_id")
                    or body.get("decision_id"),
                    "linked_from": metadata.get("source") or "intelligence",
                }
            }
        ),
    )
    return bool(row)


async def list_runs(user: dict, *, limit: int = 50) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    params: list[Any] = [workspace_id]
    tenant_clause = _tenant_clause(params, tenant_id)
    owner_clause = _owner_clause(params, user)
    params.append(max(1, min(int(limit or 50), 250)))
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        rows = await conn.fetch(
            f"""
            SELECT *
              FROM intelligence_runs
             WHERE workspace_id = $1
               {tenant_clause}
               {owner_clause}
             ORDER BY started_at DESC, id DESC
             LIMIT ${len(params)}
            """,
            *params,
        )
    return {"runs": [_row_public(row) for row in rows]}


async def get_run(user: dict, run_id: str) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    params: list[Any] = [workspace_id, run_id]
    tenant_clause = _tenant_clause(params, tenant_id)
    owner_clause = _owner_clause(params, user)
    numeric_lookup = ""
    if str(run_id).isdigit():
        params.append(int(run_id))
        numeric_lookup = f" OR id = ${len(params)}"
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            f"""
            SELECT *
              FROM intelligence_runs
             WHERE workspace_id = $1
               AND (run_ref = $2{numeric_lookup})
               {tenant_clause}
               {owner_clause}
             LIMIT 1
            """,
            *params,
        )
        if not row:
            raise HTTPException(404, "intelligence run not found")
        snapshots = await conn.fetch(
            """
            SELECT *
              FROM decision_intelligence_snapshots
             WHERE workspace_id = $1
               AND intelligence_run_id = $2
             ORDER BY created_at DESC, id DESC
             LIMIT 500
            """,
            workspace_id,
            row["id"],
        )
    return {
        "run": _row_public(row),
        "snapshots": [_row_public(item) for item in snapshots],
    }


async def list_history(user: dict, *, limit: int = 100) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    params: list[Any] = [workspace_id]
    tenant_clause = _tenant_clause(params, tenant_id)
    owner_clause = _owner_clause(params, user)
    params.append(max(1, min(int(limit or 100), 500)))
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        rows = await conn.fetch(
            f"""
            SELECT *
              FROM decision_intelligence_snapshots
             WHERE workspace_id = $1
               {tenant_clause}
               {owner_clause}
             ORDER BY created_at DESC, id DESC
             LIMIT ${len(params)}
            """,
            *params,
        )
    return {"history": [_row_public(row) for row in rows]}


def _bucket_for_probability(value: Any) -> str | None:
    probability = num(value)
    if probability is None:
        return None
    probability = max(0.0, min(1.0, float(probability)))
    for low, high, label in PROBABILITY_BUCKETS:
        if low <= probability < high or (probability == 1.0 and high == 1.0):
            return label
    return None


def _success_value(row: dict[str, Any]) -> int | None:
    status = str(row.get("outcome_status") or "").strip().lower()
    if status in SUCCESS_STATUSES:
        return 1
    if status in FAILURE_STATUSES:
        return 0
    impact = num(row.get("measured_impact"))
    if impact is None:
        return None
    return 1 if impact >= 0 else 0


def _avg(values: list[Any]) -> float | None:
    numeric = [
        float(item) for item in (num(value) for value in values) if item is not None
    ]
    return round(mean(numeric), 4) if numeric else None


async def calibration_report(
    user: dict,
    *,
    min_outcomes_required: int = 10,
    limit: int = 5000,
) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    params: list[Any] = [workspace_id]
    tenant_clause = _tenant_clause(params, tenant_id)
    owner_clause = _owner_clause(params, user)
    params.append(max(100, min(int(limit or 5000), 10000)))
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, method, recommended_decision, data_quality_status,
                   anomaly_probability, expected_impact_value, measured_impact,
                   outcome_id, outcome_status, calibration_status, created_at
              FROM decision_intelligence_snapshots
             WHERE workspace_id = $1
               {tenant_clause}
               {owner_clause}
             ORDER BY created_at DESC, id DESC
             LIMIT ${len(params)}
            """,
            *params,
        )
    snapshots = [_row_public(row) for row in rows]
    total_snapshots = len(snapshots)
    with_outcome = [row for row in snapshots if row.get("outcome_id") is not None]
    total_with_outcome = len(with_outcome)
    min_required = max(1, int(min_outcomes_required or 10))
    insufficient = total_with_outcome < min_required
    success_values = [
        value
        for value in (_success_value(row) for row in with_outcome)
        if value is not None
    ]
    by_method = Counter(str(row.get("method") or "unknown") for row in snapshots)
    by_recommended = Counter(
        str(row.get("recommended_decision") or "unknown") for row in snapshots
    )
    by_quality = Counter(
        str(row.get("data_quality_status") or "unknown") for row in snapshots
    )
    report: dict[str, Any] = {
        "status": "insufficient_outcomes" if insufficient else "ready",
        "total_snapshots": total_snapshots,
        "total_with_outcome": total_with_outcome,
        "insufficient_outcomes": insufficient,
        "min_outcomes_required": min_required,
        "by_method": dict(sorted(by_method.items())),
        "by_recommended_decision": dict(sorted(by_recommended.items())),
        "by_data_quality": dict(sorted(by_quality.items())),
        "avg_expected_impact": _avg(
            [row.get("expected_impact_value") for row in snapshots]
        ),
        "avg_measured_impact": _avg(
            [row.get("measured_impact") for row in with_outcome]
        ),
        "rationale": (
            f"Only {total_with_outcome} outcomes are linked; at least {min_required} are required before showing calibration rates."
            if insufficient
            else "Calibration uses linked outcomes only; rows without outcomes remain pending."
        ),
    }
    try:
        from app.services.intelligence.backtesting import (
            calibration_next_required_data,
            latest_backtest_report,
        )

        backtest = await latest_backtest_report(
            user,
            min_labels_required=min_required,
        )
    except Exception as exc:
        backtest = {
            "status": "not_available",
            "latest_backtest_run": None,
            "backtest_summary": None,
            "insufficient_labeled_data": True,
            "error": str(exc),
        }
        calibration_next_required_data = None
    report["outcome_linked_summary"] = {
        "status": "insufficient_labeled_data" if insufficient else "ok",
        "total_results": total_snapshots,
        "total_labeled": total_with_outcome,
        "min_labels_required": min_required,
        "insufficient_labeled_data": insufficient,
        "label_source": "outcome",
        "rationale": report["rationale"],
    }
    report["backtest_summary"] = backtest.get("backtest_summary")
    report["latest_backtest_run"] = backtest.get("latest_backtest_run")
    report["insufficient_labeled_data"] = bool(
        insufficient or backtest.get("insufficient_labeled_data")
    )
    if calibration_next_required_data is None:
        report["next_required_data"] = {
            "outcomes_needed": max(0, min_required - total_with_outcome),
            "periods_needed": 0,
            "metrics_needed": [],
            "labels_needed": 0,
        }
    else:
        report["next_required_data"] = calibration_next_required_data(
            total_with_outcome=total_with_outcome,
            min_outcomes_required=min_required,
            backtest=backtest,
        )
    if insufficient:
        report["observed_success_rate"] = None
        report["calibration_buckets"] = []
        return report

    report["observed_success_rate"] = (
        round(sum(success_values) / len(success_values), 4) if success_values else None
    )
    bucket_rows: dict[str, list[int]] = defaultdict(list)
    for row in with_outcome:
        bucket = _bucket_for_probability(row.get("anomaly_probability"))
        success = _success_value(row)
        if bucket is not None and success is not None:
            bucket_rows[bucket].append(success)
    report["calibration_buckets"] = [
        {
            "bucket": label,
            "sample_count": len(values),
            "observed_success_rate": round(sum(values) / len(values), 4)
            if values
            else None,
        }
        for _, _, label in PROBABILITY_BUCKETS
        for values in [bucket_rows.get(label, [])]
        if values
    ]
    return report
