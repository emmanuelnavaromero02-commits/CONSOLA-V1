from __future__ import annotations

import os
import uuid
from collections import Counter, defaultdict
from statistics import mean, median
from typing import Any

from fastapi import HTTPException

from app.services import auth, permissions
from app.services.intelligence.contracts import load_contracts, validate_metric
from app.services.intelligence.decision_intelligence import build_decision_intelligence
from app.services.intelligence.gold_fetcher import query_intelligence_dataset_rows
from app.services.intelligence.history import (
    PROBABILITY_BUCKETS,
    _bucket_for_probability,
    _can_read_workspace_wide,
    _owner_user_id,
    _success_value,
    scoped_db,
)
from app.services.intelligence.time_series import analyze_time_series
from app.services.intelligence.utils import (
    DatasetFetcher,
    json_dumps,
    num,
    period_key,
    public_json,
    time_key,
    workspace_scope,
)
from app.version import app_version


BACKTEST_MODES = {"historical_replay", "outcome_linked", "fixture_validation"}
SUPPORTED_SOURCE_SYSTEMS = {"replicon"}
DEFAULT_LABELS_REQUIRED = 10
DEFAULT_PREDICTION_THRESHOLD = 0.65
DEFAULT_HISTORICAL_LABEL_ROBUST_Z = 3.0


def new_backtest_ref() -> str:
    return f"backtest-{uuid.uuid4().hex}"


def _actor_id(user: dict) -> int | None:
    return _owner_user_id(user)


def _actor_clause(params: list[Any], user: dict, *, alias: str = "") -> str:
    if _can_read_workspace_wide(user):
        return ""
    actor_id = _actor_id(user)
    if actor_id is None:
        return " AND false"
    params.append(actor_id)
    prefix = f"{alias}." if alias else ""
    return f" AND {prefix}actor_user_id = ${len(params)}"


def _tenant_clause(params: list[Any], tenant_id: str | None, *, alias: str = "") -> str:
    if not tenant_id:
        return ""
    params.append(tenant_id)
    prefix = f"{alias}." if alias else ""
    return f" AND {prefix}tenant_id::text = ${len(params)}"


def _source_system(value: Any) -> str:
    source = str(value or "replicon").strip().lower()
    if source not in SUPPORTED_SOURCE_SYSTEMS:
        raise HTTPException(400, "unsupported backtest source_system")
    return source


def _mode(value: Any) -> str:
    mode = str(value or "historical_replay").strip().lower()
    if mode not in BACKTEST_MODES:
        raise HTTPException(400, "unsupported backtest mode")
    return mode


def _labels_required(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_LABELS_REQUIRED
    return max(1, min(parsed, 10_000))


def _threshold(value: Any, default: float) -> float:
    parsed = num(value)
    if parsed is None:
        return default
    return max(0.0, min(float(parsed), 1.0 if default <= 1 else 100.0))


def _bool_label(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "y", "success", "positive", "hit", "anomaly"}:
        return True
    if text in {"0", "false", "no", "n", "failure", "negative", "miss", "normal"}:
        return False
    return None


def _metric_contract(
    source_system: str, metric_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    contracts = load_contracts({source_system})
    for contract in contracts:
        metrics = (
            contract.get("metrics") if isinstance(contract.get("metrics"), list) else []
        )
        for metric in metrics:
            if isinstance(metric, dict) and str(metric.get("id") or "") == metric_id:
                validate_metric(contract, metric)
                return contract, metric
    raise HTTPException(404, f"intelligence metric not found: {metric_id}")


def _history_values(
    rows: list[dict[str, Any]], value_field: str, window: int
) -> list[float]:
    values = [
        parsed
        for row in rows[-window:]
        if (parsed := num(row.get(value_field))) is not None
    ]
    return values


def _row_entity_key(metric: dict[str, Any], row: dict[str, Any]) -> str | None:
    entity = metric.get("entity") if isinstance(metric.get("entity"), dict) else {}
    id_field = str(entity.get("id_field") or "").strip()
    if id_field == "__all__":
        return "__all__"
    if not id_field:
        return None
    raw = row.get(id_field)
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw)


def _group_rows(
    metric: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    time_field = str(metric.get("time_field") or "")
    value_field = str(metric.get("value_field") or "")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if num(row.get(value_field)) is None:
            continue
        entity_key = _row_entity_key(metric, row)
        if entity_key is None:
            continue
        grouped[entity_key].append(row)
    return {
        key: sorted(items, key=lambda item: time_key(item.get(time_field)))
        for key, items in grouped.items()
    }


def _safe_period(row: dict[str, Any], metric: dict[str, Any]) -> str:
    return period_key(row, str(metric.get("time_field") or ""))


def _in_period_range(period: str, start: str | None, end: str | None) -> bool:
    if start and period < start:
        return False
    if end and period > end:
        return False
    return True


def _signal_for_point(
    *,
    contract: dict[str, Any],
    metric: dict[str, Any],
    latest: dict[str, Any],
    history_rows: list[dict[str, Any]],
    analysis: Any,
) -> tuple[dict[str, Any], list[float]]:
    value_field = str(metric["value_field"])
    time_field = str(metric["time_field"])
    baseline = (
        metric.get("baseline") if isinstance(metric.get("baseline"), dict) else {}
    )
    window = int(baseline.get("window") or 6)
    history_values = _history_values(history_rows, value_field, window)
    actual = num(latest.get(value_field))
    if actual is None:
        raise HTTPException(422, "latest point has no numeric value")
    expected = (
        analysis.expected_value
        if analysis is not None and analysis.expected_value is not None
        else (mean(history_values) if history_values else actual)
    )
    deviation = actual - expected
    deviation_pct = (
        0.0
        if expected == 0 and actual == 0
        else (1.0 if expected == 0 else deviation / abs(expected))
    )
    entity = metric.get("entity") if isinstance(metric.get("entity"), dict) else {}
    entity_key = _row_entity_key(metric, latest) or "__unknown__"
    label_field = str(entity.get("label_field") or entity.get("id_field") or "")
    entity_label = str(latest.get(label_field) or entity_key)
    metric_id = str(metric["id"])
    dataset = str(metric["dataset"])
    return (
        {
            "signal_id": f"backtest:{source_hash(contract.get('cartridge'), dataset, metric_id, entity_key, _safe_period(latest, metric))}",
            "cartridge_id": contract.get("cartridge"),
            "source_system": contract.get("cartridge"),
            "dataset": dataset,
            "source_dataset": dataset,
            "gold_table": f"gold_{dataset}",
            "domain": contract.get("domain") or "Operacion",
            "entity_kind": entity.get("kind") or "entity",
            "entity_id": entity_key,
            "entity_label": entity_label,
            "metric": metric_id,
            "metric_name": metric.get("name") or metric_id,
            "period_key": _safe_period(latest, metric),
            "actual_value": round(actual, 4),
            "expected_value": round(expected, 4),
            "deviation_value": round(deviation, 4),
            "deviation_pct": round(deviation_pct, 4),
            "freshness_at": _safe_period(latest, metric),
            "freshness_field": time_field,
        },
        history_values,
    )


def source_hash(*parts: Any) -> str:
    import hashlib
    import json

    raw = json.dumps(parts, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _historical_actual_label(
    *,
    signal: dict[str, Any],
    analysis: Any,
    metric: dict[str, Any],
    robust_z_threshold: float,
) -> bool | None:
    robust_z = analysis.robust_z if analysis is not None else None
    if robust_z is not None and abs(float(robust_z)) >= robust_z_threshold:
        return True
    deviation_pct = num(signal.get("deviation_pct"))
    if deviation_pct is None:
        return None
    rules = (
        metric.get("signal_rules")
        if isinstance(metric.get("signal_rules"), dict)
        else {}
    )
    warning = float(rules.get("warning_pct") or 0.20)
    critical = float(rules.get("critical_pct") or max(warning * 1.5, warning + 0.10))
    return abs(float(deviation_pct)) >= critical


def _fixture_label(row: dict[str, Any]) -> bool | None:
    for key in ("actual_label", "fixture_label", "label", "is_anomaly"):
        if key in row:
            return _bool_label(row.get(key))
    return None


def _result_flags(
    predicted: bool | None, actual: bool | None
) -> dict[str, bool | None]:
    if predicted is None or actual is None:
        return {
            "is_true_positive": None,
            "is_false_positive": None,
            "is_true_negative": None,
            "is_false_negative": None,
        }
    return {
        "is_true_positive": predicted is True and actual is True,
        "is_false_positive": predicted is True and actual is False,
        "is_true_negative": predicted is False and actual is False,
        "is_false_negative": predicted is False and actual is True,
    }


def _decision_fields(decision: dict[str, Any]) -> dict[str, Any]:
    expected = (
        decision.get("expected_impact")
        if isinstance(decision.get("expected_impact"), dict)
        else {}
    )
    quality = (
        decision.get("data_quality")
        if isinstance(decision.get("data_quality"), dict)
        else {}
    )
    return {
        "method": decision.get("method"),
        "anomaly_probability": num(decision.get("anomaly_probability")),
        "recommended_decision": decision.get("recommended_decision"),
        "uncertainty_level": decision.get("uncertainty_level"),
        "data_quality_status": quality.get("status"),
        "expected_impact_value": num(expected.get("value")),
    }


def _backtest_row(
    *,
    source_system: str,
    dataset: str | None,
    metric: str | None,
    gold_table: str | None,
    period: str | None,
    as_of_period: str | None,
    entity_key: str | None,
    decision: dict[str, Any],
    predicted_label: bool | None,
    actual_label: bool | None,
    label_source: str,
    snapshot_id: int | None = None,
    signal_id: str | None = None,
    control_room_item_id: str | None = None,
    measured_impact: float | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = _decision_fields(decision)
    error_abs = None
    expected_impact = fields.get("expected_impact_value")
    if expected_impact is not None and measured_impact is not None:
        error_abs = abs(float(measured_impact) - float(expected_impact))
    flags = _result_flags(predicted_label, actual_label)
    return {
        "snapshot_id": snapshot_id,
        "signal_id": signal_id,
        "control_room_item_id": control_room_item_id,
        "source_system": source_system,
        "source_dataset": dataset,
        "gold_table": gold_table,
        "metric": metric,
        "entity_key": entity_key,
        "period_key": period,
        "as_of_period": as_of_period,
        "method": fields.get("method"),
        "anomaly_probability": fields.get("anomaly_probability"),
        "predicted_label": predicted_label,
        "actual_label": actual_label,
        "label_source": label_source,
        "recommended_decision": fields.get("recommended_decision"),
        "uncertainty_level": fields.get("uncertainty_level"),
        "data_quality_status": fields.get("data_quality_status"),
        "expected_impact_value": expected_impact,
        "measured_impact": measured_impact,
        "error_abs": error_abs,
        **flags,
        "result": result or {},
    }


async def _historical_replay_rows(
    user: dict,
    request: dict[str, Any],
    *,
    fetcher: DatasetFetcher | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_system = _source_system(request.get("source_system"))
    metric_id = str(request.get("metric") or "").strip()
    if not metric_id:
        raise HTTPException(400, "metric is required")
    contract, metric = _metric_contract(source_system, metric_id)
    dataset = str(request.get("source_dataset") or metric.get("dataset") or "")
    rows = await (fetcher or query_intelligence_dataset_rows)(
        dataset, user, int(request.get("limit") or 5000)
    )
    if not rows:
        raise HTTPException(404, f"dataset unavailable: {dataset}")
    baseline = (
        metric.get("baseline") if isinstance(metric.get("baseline"), dict) else {}
    )
    minimum_history = max(3, int(baseline.get("minimum_history") or 3))
    prediction_threshold = _threshold(
        request.get("prediction_threshold"), DEFAULT_PREDICTION_THRESHOLD
    )
    robust_z_threshold = float(
        request.get("historical_label_robust_z") or DEFAULT_HISTORICAL_LABEL_ROBUST_Z
    )
    start_period = str(request.get("start_period") or "").strip() or None
    end_period = str(request.get("end_period") or "").strip() or None
    mode = _mode(request.get("mode"))
    results: list[dict[str, Any]] = []
    for entity_key, entity_rows in sorted(_group_rows(metric, rows).items()):
        for idx, latest in enumerate(entity_rows):
            history_rows = entity_rows[:idx]
            period = _safe_period(latest, metric)
            if not _in_period_range(period, start_period, end_period):
                continue
            if len(history_rows) < minimum_history:
                continue
            analysis = analyze_time_series(
                metric=metric, latest=latest, history_rows=history_rows
            )
            if analysis is None:
                continue
            signal, history_values = _signal_for_point(
                contract=contract,
                metric=metric,
                latest=latest,
                history_rows=history_rows,
                analysis=analysis,
            )
            decision = build_decision_intelligence(
                metric=metric,
                signal=signal,
                baseline={},
                latest=latest,
                history_values=history_values,
                time_series_analysis=analysis,
            )
            probability = num(decision.get("anomaly_probability"))
            predicted_label = (
                None if probability is None else probability >= prediction_threshold
            )
            if mode == "fixture_validation":
                actual_label = _fixture_label(latest)
                label_source = "fixture" if actual_label is not None else "unavailable"
            else:
                actual_label = _historical_actual_label(
                    signal=signal,
                    analysis=analysis,
                    metric=metric,
                    robust_z_threshold=robust_z_threshold,
                )
                label_source = (
                    "historical_rule" if actual_label is not None else "unavailable"
                )
            measured_impact = None
            if label_source == "historical_rule" and actual_label is not None:
                measured_impact = (
                    num(decision.get("expected_impact", {}).get("value"))
                    if actual_label
                    else 0.0
                )
            results.append(
                _backtest_row(
                    source_system=source_system,
                    dataset=dataset,
                    metric=metric_id,
                    gold_table=f"gold_{dataset}",
                    period=period,
                    as_of_period=period,
                    entity_key=entity_key,
                    decision=decision,
                    predicted_label=predicted_label,
                    actual_label=actual_label,
                    label_source=label_source,
                    signal_id=signal.get("signal_id"),
                    measured_impact=measured_impact,
                    result={
                        "no_lookahead": True,
                        "history_points_used": len(history_rows),
                        "history_max_period": _safe_period(history_rows[-1], metric)
                        if history_rows
                        else None,
                        "as_of_period": period,
                        "label_rule": (
                            "fixture_label_from_controlled_row"
                            if mode == "fixture_validation"
                            else f"abs(robust_z)>={robust_z_threshold} or abs(deviation_pct)>=critical_pct"
                        ),
                        "decision_intelligence": decision,
                    },
                )
            )
    config = {
        "source_system": source_system,
        "source_dataset": dataset,
        "metric": metric_id,
        "start_period": start_period,
        "end_period": end_period,
        "prediction_threshold": prediction_threshold,
        "historical_label_robust_z": robust_z_threshold,
        "minimum_history": minimum_history,
        "gold_table": f"gold_{dataset}",
        "mode_note": (
            "fixture labels are test-only and must not be mixed with production claims"
            if mode == "fixture_validation"
            else "historical labels are deterministic replay rules, not human outcomes"
        ),
    }
    return config, results


def _snapshot_decision(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("decision_intelligence")
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    decision = raw if isinstance(raw, dict) else {}
    if decision:
        return decision
    return {
        "method": row.get("method"),
        "anomaly_probability": row.get("anomaly_probability"),
        "recommended_decision": row.get("recommended_decision"),
        "uncertainty_level": row.get("uncertainty_level"),
        "expected_impact": {"value": row.get("expected_impact_value")},
        "data_quality": {"status": row.get("data_quality_status")},
    }


async def _outcome_linked_rows(
    user: dict, request: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tenant_id, workspace_id = workspace_scope(user)
    source_system = _source_system(request.get("source_system"))
    metric_id = str(request.get("metric") or "").strip() or None
    dataset = str(request.get("source_dataset") or "").strip() or None
    params: list[Any] = [workspace_id, source_system]
    tenant_clause = _tenant_clause(params, tenant_id, alias="s")
    metric_clause = ""
    if metric_id:
        params.append(metric_id)
        metric_clause = f" AND s.metric = ${len(params)}"
    dataset_clause = ""
    if dataset:
        params.append(dataset)
        dataset_clause = f" AND s.source_dataset = ${len(params)}"
    actor_clause = ""
    if not _can_read_workspace_wide(user):
        actor = _actor_id(user)
        if actor is None:
            actor_clause = " AND false"
        else:
            params.append(actor)
            actor_clause = f" AND s.owner_user_id = ${len(params)}"
    params.append(max(1, min(int(request.get("limit") or 5000), 10000)))
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        rows = await conn.fetch(
            f"""
            SELECT s.id AS snapshot_id, s.signal_id, s.control_room_item_id,
                   s.source_system, s.source_dataset, s.gold_table, s.metric,
                   s.entity_key, s.period_key, s.method, s.anomaly_probability,
                   s.recommended_decision, s.uncertainty_level, s.expected_impact_value,
                   s.data_quality_status, s.decision_intelligence, s.outcome_id,
                   s.outcome_status, s.measured_impact, s.outcome_observed_at,
                   o.actual_value AS outcome_actual_value,
                   o.predicted_value AS outcome_predicted_value,
                   o.prediction_error AS outcome_prediction_error,
                   o.metadata AS outcome_metadata
              FROM decision_intelligence_snapshots s
              LEFT JOIN prediction_outcomes o
                ON o.id = s.outcome_id
             WHERE s.workspace_id = $1
               AND s.source_system = $2
               {tenant_clause}
               {metric_clause}
               {dataset_clause}
               {actor_clause}
             ORDER BY s.created_at DESC, s.id DESC
             LIMIT ${len(params)}
            """,
            *params,
        )
    results: list[dict[str, Any]] = []
    for row_obj in rows:
        row = dict(row_obj)
        decision = _snapshot_decision(row)
        predicted_label = _bool_label(
            row.get("recommended_decision") in {"act_now", "investigate"}
        )
        probability = num(row.get("anomaly_probability"))
        if probability is not None:
            predicted_label = probability >= _threshold(
                request.get("prediction_threshold"), DEFAULT_PREDICTION_THRESHOLD
            )
        actual_raw = _success_value(row)
        actual_label = None if actual_raw is None else bool(actual_raw)
        label_source = "outcome" if actual_label is not None else "unavailable"
        measured = num(row.get("measured_impact"))
        if measured is None:
            measured = num(row.get("outcome_actual_value"))
        results.append(
            _backtest_row(
                source_system=source_system,
                dataset=row.get("source_dataset"),
                metric=row.get("metric"),
                gold_table=row.get("gold_table"),
                period=row.get("period_key"),
                as_of_period=row.get("period_key"),
                entity_key=row.get("entity_key"),
                decision=decision,
                predicted_label=predicted_label,
                actual_label=actual_label,
                label_source=label_source,
                snapshot_id=row.get("snapshot_id"),
                signal_id=row.get("signal_id"),
                control_room_item_id=row.get("control_room_item_id"),
                measured_impact=measured,
                result={
                    "label_rule": "linked_prediction_outcome",
                    "outcome_id": row.get("outcome_id"),
                    "outcome_status": row.get("outcome_status"),
                    "outcome_observed_at": row.get("outcome_observed_at"),
                    "snapshot_is_immutable": True,
                },
            )
        )
    config = {
        "source_system": source_system,
        "source_dataset": dataset,
        "metric": metric_id,
        "mode_note": "uses linked prediction_outcomes only; missing outcomes remain unavailable",
    }
    return config, results


def _avg(values: list[Any]) -> float | None:
    numeric = [float(item) for value in values if (item := num(value)) is not None]
    return round(mean(numeric), 4) if numeric else None


def summarize_results(
    results: list[dict[str, Any]],
    *,
    labels_required: int,
    dataset_unavailable: bool = False,
) -> dict[str, Any]:
    labels_required = _labels_required(labels_required)
    total_results = len(results)
    labeled = [
        row
        for row in results
        if row.get("actual_label") is not None
        and row.get("label_source") != "unavailable"
    ]
    total_labeled = len(labeled)
    insufficient = total_labeled < labels_required
    tp = sum(1 for row in labeled if row.get("is_true_positive") is True)
    fp = sum(1 for row in labeled if row.get("is_false_positive") is True)
    tn = sum(1 for row in labeled if row.get("is_true_negative") is True)
    fn = sum(1 for row in labeled if row.get("is_false_negative") is True)
    brier_rows = [
        row
        for row in labeled
        if row.get("anomaly_probability") is not None
        and row.get("actual_label") is not None
    ]
    by_method = Counter(str(row.get("method") or "unknown") for row in results)
    by_recommended = Counter(
        str(row.get("recommended_decision") or "unknown") for row in results
    )
    by_uncertainty = Counter(
        str(row.get("uncertainty_level") or "unknown") for row in results
    )
    by_quality = Counter(
        str(row.get("data_quality_status") or "unknown") for row in results
    )
    if dataset_unavailable:
        status = "dataset_unavailable"
        rationale = "Required Gold dataset was unavailable; no calibration metrics were calculated."
    elif insufficient:
        status = "insufficient_labeled_data"
        rationale = (
            f"Only {total_labeled} labeled result(s) are available; "
            f"{labels_required} are required before reporting calibration metrics."
        )
    else:
        status = "ok"
        rationale = "Calibration metrics use labeled backtest rows only; unavailable labels are excluded."
    precision = recall = brier = None
    buckets: list[dict[str, Any]] | None = []
    if not insufficient and not dataset_unavailable:
        precision = round(tp / (tp + fp), 4) if (tp + fp) else None
        recall = round(tp / (tp + fn), 4) if (tp + fn) else None
        if len(brier_rows) >= labels_required:
            brier = round(
                mean(
                    [
                        (
                            float(row["anomaly_probability"])
                            - (1.0 if row.get("actual_label") else 0.0)
                        )
                        ** 2
                        for row in brier_rows
                    ]
                ),
                6,
            )
            bucket_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in brier_rows:
                bucket = _bucket_for_probability(row.get("anomaly_probability"))
                if bucket:
                    bucket_rows[bucket].append(row)
            buckets = []
            for low, high, label in PROBABILITY_BUCKETS:
                rows = bucket_rows.get(label, [])
                positives = sum(1 for row in rows if row.get("actual_label") is True)
                buckets.append(
                    {
                        "bucket": label,
                        "range": [low, high],
                        "sample_count": len(rows),
                        "positive_rate": round(positives / len(rows), 4)
                        if rows
                        else None,
                        "avg_probability": _avg(
                            [row.get("anomaly_probability") for row in rows]
                        ),
                    }
                )
    return {
        "status": status,
        "total_results": total_results,
        "total_labeled": total_labeled,
        "min_labels_required": labels_required,
        "insufficient_labeled_data": insufficient and not dataset_unavailable,
        "by_method": dict(sorted(by_method.items())),
        "by_recommended_decision": dict(sorted(by_recommended.items())),
        "by_uncertainty_level": dict(sorted(by_uncertainty.items())),
        "by_data_quality": dict(sorted(by_quality.items())),
        "precision": precision,
        "recall": recall,
        "false_positive_count": fp,
        "false_negative_count": fn,
        "true_positive_count": tp,
        "true_negative_count": tn,
        "brier_score": brier,
        "average_expected_impact": _avg(
            [row.get("expected_impact_value") for row in results]
        ),
        "average_measured_impact": _avg(
            [row.get("measured_impact") for row in labeled]
        ),
        "calibration_buckets": buckets,
        "rationale": rationale,
    }


async def _insert_run(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    user: dict,
    run_ref: str,
    mode: str,
    source_system: str,
    config: dict[str, Any],
    labels_required: int,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        INSERT INTO backtest_runs (
            tenant_id, workspace_id, run_ref, run_mode, source_system, source_dataset,
            metric, method, labels_required, config, actor_user_id, app_version, deploy_ref
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12, $13)
        RETURNING *
        """,
        tenant_id,
        workspace_id,
        run_ref,
        mode,
        source_system,
        config.get("source_dataset"),
        config.get("metric"),
        config.get("method") or config.get("mode_note"),
        labels_required,
        json_dumps(config),
        _actor_id(user),
        app_version(),
        os.environ.get("DEPLOY_REF") or os.environ.get("GITHUB_SHA"),
    )
    return public_json(dict(row))


async def _insert_results(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    run_id: int,
    results: list[dict[str, Any]],
) -> None:
    for row in results:
        await conn.execute(
            """
            INSERT INTO backtest_results (
                tenant_id, workspace_id, backtest_run_id, snapshot_id, signal_id,
                control_room_item_id, source_system, source_dataset, gold_table,
                metric, entity_key, period_key, as_of_period, method,
                anomaly_probability, predicted_label, actual_label, label_source,
                recommended_decision, uncertainty_level, data_quality_status,
                expected_impact_value, measured_impact, error_abs,
                is_true_positive, is_false_positive, is_true_negative,
                is_false_negative, result
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9,
                $10, $11, $12, $13, $14,
                $15, $16, $17, $18,
                $19, $20, $21,
                $22, $23, $24,
                $25, $26, $27,
                $28, $29::jsonb
            )
            """,
            tenant_id,
            workspace_id,
            run_id,
            row.get("snapshot_id"),
            row.get("signal_id"),
            row.get("control_room_item_id"),
            row.get("source_system"),
            row.get("source_dataset"),
            row.get("gold_table"),
            row.get("metric"),
            row.get("entity_key"),
            row.get("period_key"),
            row.get("as_of_period"),
            row.get("method"),
            row.get("anomaly_probability"),
            row.get("predicted_label"),
            row.get("actual_label"),
            row.get("label_source") or "unavailable",
            row.get("recommended_decision"),
            row.get("uncertainty_level"),
            row.get("data_quality_status"),
            row.get("expected_impact_value"),
            row.get("measured_impact"),
            row.get("error_abs"),
            row.get("is_true_positive"),
            row.get("is_false_positive"),
            row.get("is_true_negative"),
            row.get("is_false_negative"),
            json_dumps(row.get("result") or {}),
        )


async def _finish_run(
    conn: Any,
    *,
    workspace_id: str,
    run_id: int,
    status: str,
    results: list[dict[str, Any]],
    labels_required: int,
    summary: dict[str, Any],
) -> dict[str, Any]:
    labels_available = int(summary.get("total_labeled") or 0)
    row = await conn.fetchrow(
        """
        UPDATE backtest_runs
           SET status = $3,
               periods_evaluated = $4,
               labels_available = $5,
               labels_required = $6,
               insufficient_labeled_data = $7,
               summary = $8::jsonb,
               completed_at = NOW(),
               updated_at = NOW()
         WHERE workspace_id = $1
           AND id = $2
         RETURNING *
        """,
        workspace_id,
        run_id,
        status,
        len(results),
        labels_available,
        labels_required,
        bool(summary.get("insufficient_labeled_data")),
        json_dumps(summary),
    )
    return public_json(dict(row))


async def run_backtest(
    user: dict,
    request: dict[str, Any] | None = None,
    *,
    fetcher: DatasetFetcher | None = None,
) -> dict[str, Any]:
    request = request or {}
    tenant_id, workspace_id = workspace_scope(user)
    mode = _mode(request.get("mode"))
    source_system = _source_system(request.get("source_system"))
    labels_required = _labels_required(request.get("labels_required"))
    run_ref = new_backtest_ref()
    config: dict[str, Any] = {
        "source_system": source_system,
        "mode": mode,
        "metric": request.get("metric"),
        "start_period": request.get("start_period"),
        "end_period": request.get("end_period"),
    }
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        run = await _insert_run(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user=user,
            run_ref=run_ref,
            mode=mode,
            source_system=source_system,
            config=config,
            labels_required=labels_required,
        )
        try:
            if mode == "outcome_linked":
                resolved_config, results = await _outcome_linked_rows(user, request)
            else:
                resolved_config, results = await _historical_replay_rows(
                    user,
                    {**request, "mode": mode},
                    fetcher=fetcher,
                )
            config.update(resolved_config)
            summary = summarize_results(results, labels_required=labels_required)
            status = str(summary["status"])
        except HTTPException as exc:
            if exc.status_code in {404, 503}:
                summary = summarize_results(
                    [],
                    labels_required=labels_required,
                    dataset_unavailable=True,
                )
                summary["error"] = exc.detail
                results = []
                status = "dataset_unavailable"
            else:
                summary = {
                    "status": "failed",
                    "total_results": 0,
                    "total_labeled": 0,
                    "min_labels_required": labels_required,
                    "insufficient_labeled_data": True,
                    "rationale": str(exc.detail),
                }
                results = []
                status = "failed"
        except Exception as exc:
            summary = {
                "status": "failed",
                "total_results": 0,
                "total_labeled": 0,
                "min_labels_required": labels_required,
                "insufficient_labeled_data": True,
                "rationale": str(exc),
            }
            results = []
            status = "failed"
        await conn.execute(
            "UPDATE backtest_runs SET config = config || $3::jsonb WHERE workspace_id = $1 AND id = $2",
            workspace_id,
            run["id"],
            json_dumps(config),
        )
        if results:
            await _insert_results(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                run_id=int(run["id"]),
                results=results,
            )
        finished = await _finish_run(
            conn,
            workspace_id=workspace_id,
            run_id=int(run["id"]),
            status=status,
            results=results,
            labels_required=labels_required,
            summary=summary,
        )
    return {
        "run": finished,
        "summary": summary,
        "results": public_json(results[: int(request.get("result_limit") or 100)]),
    }


async def list_backtests(user: dict, *, limit: int = 50) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    params: list[Any] = [workspace_id]
    tenant_clause = _tenant_clause(params, tenant_id)
    actor_clause = _actor_clause(params, user)
    params.append(max(1, min(int(limit or 50), 250)))
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        rows = await conn.fetch(
            f"""
            SELECT *
              FROM backtest_runs
             WHERE workspace_id = $1
               {tenant_clause}
               {actor_clause}
             ORDER BY started_at DESC, id DESC
             LIMIT ${len(params)}
            """,
            *params,
        )
    return {"backtests": [public_json(dict(row)) for row in rows]}


async def _find_backtest(
    conn: Any, user: dict, tenant_id: str | None, workspace_id: str, backtest_id: str
) -> Any:
    params: list[Any] = [workspace_id, backtest_id]
    tenant_clause = _tenant_clause(params, tenant_id)
    actor_clause = _actor_clause(params, user)
    numeric_lookup = ""
    if str(backtest_id).isdigit():
        params.append(int(backtest_id))
        numeric_lookup = f" OR id = ${len(params)}"
    return await conn.fetchrow(
        f"""
        SELECT *
          FROM backtest_runs
         WHERE workspace_id = $1
           AND (run_ref = $2{numeric_lookup})
           {tenant_clause}
           {actor_clause}
         LIMIT 1
        """,
        *params,
    )


async def get_backtest(user: dict, backtest_id: str) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await _find_backtest(conn, user, tenant_id, workspace_id, backtest_id)
        if not row:
            raise HTTPException(404, "backtest run not found")
    return {"backtest": public_json(dict(row))}


async def get_backtest_results(
    user: dict, backtest_id: str, *, limit: int = 500
) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await _find_backtest(conn, user, tenant_id, workspace_id, backtest_id)
        if not row:
            raise HTTPException(404, "backtest run not found")
        rows = await conn.fetch(
            """
            SELECT *
              FROM backtest_results
             WHERE workspace_id = $1
               AND backtest_run_id = $2
             ORDER BY id
             LIMIT $3
            """,
            workspace_id,
            row["id"],
            max(1, min(int(limit or 500), 5000)),
        )
    return {
        "backtest": public_json(dict(row)),
        "results": [public_json(dict(item)) for item in rows],
    }


async def latest_backtest_report(
    user: dict,
    *,
    min_labels_required: int = DEFAULT_LABELS_REQUIRED,
) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope(user)
    pool = await auth.pool()
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        exists = await conn.fetchval("SELECT to_regclass('public.backtest_runs')")
        if not exists:
            return {
                "status": "not_available",
                "latest_backtest_run": None,
                "backtest_summary": None,
                "insufficient_labeled_data": True,
            }
        params: list[Any] = [workspace_id]
        tenant_clause = _tenant_clause(params, tenant_id)
        actor_clause = _actor_clause(params, user)
        row = await conn.fetchrow(
            f"""
            SELECT *
              FROM backtest_runs
             WHERE workspace_id = $1
               {tenant_clause}
               {actor_clause}
             ORDER BY completed_at DESC NULLS LAST, id DESC
             LIMIT 1
            """,
            *params,
        )
    if not row:
        return {
            "status": "not_available",
            "latest_backtest_run": None,
            "backtest_summary": None,
            "insufficient_labeled_data": True,
        }
    data = public_json(dict(row))
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    labels_available = int(
        data.get("labels_available") or summary.get("total_labeled") or 0
    )
    labels_required = max(
        _labels_required(min_labels_required), int(data.get("labels_required") or 0)
    )
    return {
        "status": summary.get("status") or data.get("status"),
        "latest_backtest_run": data,
        "backtest_summary": summary,
        "insufficient_labeled_data": labels_available < labels_required,
        "next_required_data": {
            "labels_needed": max(0, labels_required - labels_available),
            "periods_needed": 0 if data.get("periods_evaluated") else labels_required,
            "metrics_needed": [] if data.get("metric") else ["metric"],
        },
    }


def calibration_next_required_data(
    *,
    total_with_outcome: int,
    min_outcomes_required: int,
    backtest: dict[str, Any] | None,
) -> dict[str, Any]:
    backtest_next = (
        backtest.get("next_required_data")
        if isinstance(backtest, dict)
        and isinstance(backtest.get("next_required_data"), dict)
        else {}
    )
    return {
        "outcomes_needed": max(0, int(min_outcomes_required) - int(total_with_outcome)),
        "periods_needed": int(backtest_next.get("periods_needed") or 0),
        "metrics_needed": backtest_next.get("metrics_needed") or [],
        "labels_needed": int(backtest_next.get("labels_needed") or 0),
    }
