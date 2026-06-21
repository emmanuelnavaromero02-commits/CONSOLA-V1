from __future__ import annotations

from statistics import median
from typing import Any

from app.services.intelligence import monte_carlo
from app.services.intelligence.utils import num, sample_hash, time_key


RULESET_VERSION = "control_room_gold_signal.v1"
GENERIC_ORIGIN = "generic_gold_signal"
INTELLIGENCE_ORIGIN = "intelligence_signal"


def enrich_artifact(
    artifact: dict[str, Any],
    *,
    metric: dict[str, Any],
    control_origin: str = INTELLIGENCE_ORIGIN,
) -> dict[str, Any]:
    """Attach deterministic Control Room math metadata to an intelligence artifact."""

    signal = artifact.get("signal") if isinstance(artifact.get("signal"), dict) else {}
    baseline = artifact.get("baseline") if isinstance(artifact.get("baseline"), dict) else {}
    decision = (
        artifact.get("decision_intelligence")
        if isinstance(artifact.get("decision_intelligence"), dict)
        else {}
    )
    monte_carlo_payload = _monte_carlo_for_artifact(
        signal=signal,
        baseline=baseline,
        decision=decision,
        metric=metric,
    )
    bayesian_calibration = bayesian_calibration_payload(decision)
    priority = priority_breakdown(
        signal=signal,
        decision=decision,
        monte_carlo_payload=monte_carlo_payload,
    )
    provenance = math_provenance(
        signal=signal,
        baseline=baseline,
        decision=decision,
        metric=metric,
        control_origin=control_origin,
        priority=priority,
        monte_carlo_payload=monte_carlo_payload,
        bayesian_calibration=bayesian_calibration,
    )
    artifact["control_origin"] = control_origin
    artifact["capabilities"] = capabilities_for_metric(
        metric,
        monte_carlo_payload,
        bayesian_calibration,
    )
    artifact["math_provenance"] = provenance
    artifact["priority"] = priority
    artifact["monte_carlo"] = monte_carlo_payload
    artifact["bayesian_calibration"] = bayesian_calibration
    signal["control_origin"] = control_origin
    signal["math_provenance"] = provenance
    signal["priority"] = priority
    signal["monte_carlo"] = monte_carlo_payload
    signal["bayesian_calibration"] = bayesian_calibration
    return artifact


def bayesian_calibration_payload(decision: dict[str, Any]) -> dict[str, Any]:
    calibration = (
        decision.get("calibration")
        if isinstance(decision.get("calibration"), dict)
        else {}
    )
    raw_probability = calibration.get("raw_probability")
    if raw_probability is None:
        raw_probability = decision.get("anomaly_probability")
    applied = bool(calibration.get("calibration_applied"))
    return {
        "status": "calibrated" if applied else "not_calibrated",
        "reason": calibration.get("calibration_reason") or "not_available",
        "group": calibration.get("calibration_group"),
        "sample_count": calibration.get("sample_count", 0),
        "raw_probability": raw_probability,
        "calibrated_probability": calibration.get("calibrated_probability")
        if applied
        else None,
        "posterior_mean": calibration.get("posterior_mean"),
        "posterior_alpha": calibration.get("posterior_alpha"),
        "posterior_beta": calibration.get("posterior_beta"),
    }


def capabilities_for_metric(
    metric: dict[str, Any],
    monte_carlo_payload: dict[str, Any],
    bayesian_calibration: dict[str, Any],
) -> dict[str, Any]:
    return {
        "readiness": "gold_ready",
        "contract": "declared" if metric.get("_generic_inferred") is not True else "inferred",
        "intelligence": "available",
        "bayesian_calibration": bayesian_calibration.get("status", "not_calibrated"),
        "bayesian_calibration_reason": bayesian_calibration.get("reason"),
        "monte_carlo": monte_carlo_payload.get("status", "skipped"),
        "monte_carlo_mode": monte_carlo_payload.get("mode"),
        "action": "supervised_internal",
    }


def math_provenance(
    *,
    signal: dict[str, Any],
    baseline: dict[str, Any],
    decision: dict[str, Any],
    metric: dict[str, Any],
    control_origin: str,
    priority: dict[str, Any],
    monte_carlo_payload: dict[str, Any],
    bayesian_calibration: dict[str, Any],
) -> dict[str, Any]:
    time_series = (
        decision.get("time_series")
        if isinstance(decision.get("time_series"), dict)
        else None
    )
    payload = {
        "ruleset_version": RULESET_VERSION,
        "control_origin": control_origin,
        "dataset": signal.get("dataset"),
        "metric": signal.get("metric"),
        "entity_id": signal.get("entity_id"),
        "period_key": signal.get("period_key"),
        "formula": (
            "baseline -> deviation -> robust residual/MAD -> probability -> "
            "priority -> supervised OMEGA action"
        ),
        "baseline": {
            "method": baseline.get("method"),
            "sample_count": baseline.get("sample_count"),
            "window": baseline.get("window"),
            "actual_value": baseline.get("actual_value"),
            "expected_value": baseline.get("expected_value"),
            "history_hash": sample_hash(baseline.get("history_values") or []),
        },
        "signal": {
            "severity": signal.get("severity"),
            "confidence": signal.get("confidence"),
            "deviation_value": signal.get("deviation_value"),
            "deviation_pct": signal.get("deviation_pct"),
            "signal_type": signal.get("signal_type"),
        },
        "decision_intelligence": {
            "method": decision.get("method"),
            "anomaly_probability": decision.get("anomaly_probability"),
            "recommended_decision": decision.get("recommended_decision"),
            "uncertainty_level": decision.get("uncertainty_level"),
        },
        "time_series": {
            "method": time_series.get("method"),
            "robust_z": ((time_series.get("residual") or {}).get("robust_z")),
            "seasonality_status": (
                (time_series.get("seasonality") or {}).get("status")
            ),
        }
        if time_series
        else None,
        "bayesian_calibration": bayesian_calibration,
        "monte_carlo": {
            "status": monte_carlo_payload.get("status"),
            "mode": monte_carlo_payload.get("mode"),
            "reason": monte_carlo_payload.get("reason"),
            "seed": monte_carlo_payload.get("seed"),
            "reproducibility_hash": monte_carlo_payload.get("reproducibility_hash"),
        },
        "priority": priority,
        "input_hash": sample_hash(
            {
                "signal": signal,
                "baseline": baseline,
                "metric_id": metric.get("id"),
                "ruleset_version": RULESET_VERSION,
            }
        ),
    }
    return payload


def priority_breakdown(
    *,
    signal: dict[str, Any],
    decision: dict[str, Any],
    monte_carlo_payload: dict[str, Any],
) -> dict[str, Any]:
    severity = str(signal.get("severity") or "low")
    severity_points = {
        "critical": 26.0,
        "high": 20.0,
        "medium": 12.0,
        "low": 5.0,
    }.get(severity, 5.0)
    confidence = _clamp(num(signal.get("confidence")) or 0.0, 0.0, 1.0)
    confidence_points = confidence * 24.0
    deviation_pct = abs(num(signal.get("deviation_pct")) or 0.0)
    deviation_points = min(24.0, deviation_pct * 34.0)
    expected_impact = _money_value(decision.get("expected_impact"))
    impact_points = min(14.0, expected_impact / 10_000.0)
    probability = num(decision.get("anomaly_probability"))
    probability_points = (_clamp(probability, 0.0, 1.0) * 8.0) if probability is not None else 0.0
    downside = _money_value(decision.get("downside_risk"))
    downside_points = min(7.0, downside / 25_000.0)
    delay = _delay_value(decision.get("cost_of_delay"))
    delay_points = min(5.0, delay / 2_000.0)
    mc_summary = monte_carlo_payload.get("distribution_summary")
    breach = (
        num(mc_summary.get("probability_breach_threshold"))
        if isinstance(mc_summary, dict)
        else None
    )
    monte_carlo_points = (_clamp(breach, 0.0, 1.0) * 6.0) if breach is not None else 0.0
    raw = (
        severity_points
        + confidence_points
        + deviation_points
        + impact_points
        + probability_points
        + downside_points
        + delay_points
        + monte_carlo_points
    )
    score = int(max(0, min(100, round(raw))))
    return {
        "score": score,
        "formula": (
            "severity + confidence + deviation + impact + probability + "
            "downside + cost_of_delay + monte_carlo_breach"
        ),
        "ruleset_version": RULESET_VERSION,
        "drivers": {
            "severity": round(severity_points, 4),
            "confidence": round(confidence_points, 4),
            "deviation": round(deviation_points, 4),
            "impact": round(impact_points, 4),
            "probability": round(probability_points, 4),
            "downside": round(downside_points, 4),
            "cost_of_delay": round(delay_points, 4),
            "monte_carlo_breach": round(monte_carlo_points, 4),
        },
    }


def build_generic_gold_artifacts(
    *,
    cartridge_id: str,
    dataset: str,
    rows: list[dict[str, Any]],
    build_metric_artifacts: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metric = infer_generic_metric(dataset=dataset, rows=rows)
    if metric is None:
        return [], [
            {
                "cartridge_id": cartridge_id,
                "dataset": dataset,
                "metric": None,
                "status": "generic_gold_unusable",
                "reason": "dataset has no numeric metric columns to analyze",
            }
        ]
    contract = {
        "cartridge": cartridge_id,
        "domain": "Gold",
        "_generic_inferred": True,
    }
    artifacts, skipped = build_metric_artifacts(
        contract,
        metric,
        rows_for_generic_metric(metric, rows),
    )
    for artifact in artifacts:
        enrich_artifact(
            artifact,
            metric=metric,
            control_origin=GENERIC_ORIGIN,
        )
    for item in skipped:
        item["control_origin"] = GENERIC_ORIGIN
    return artifacts, skipped


def infer_generic_metric(
    *,
    dataset: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not rows:
        return None
    time_field = _infer_time_field(rows) or "__omega_period"
    normalized = _ensure_time_field(rows, time_field)
    value_field = _infer_value_field(normalized, time_field)
    if not value_field:
        return None
    entity_field = _infer_entity_field(normalized, time_field, value_field) or "__all__"
    return {
        "id": f"generic_{value_field}",
        "name": f"Gold metric {value_field}",
        "dataset": dataset,
        "entity": {
            "kind": "gold_entity" if entity_field != "__all__" else "dataset",
            "id_field": entity_field,
            "label_field": entity_field,
        },
        "time_field": time_field,
        "value_field": value_field,
        "expected_behavior": "watch",
        "baseline": {
            "method": "generic_robust_gold",
            "minimum_history": 2,
            "window": 8,
        },
        "prediction": {"enabled": False, "horizon_days": []},
        "impact": {"currency": "USD", "unit_value": 1},
        "signal_rules": {"warning_pct": 0.25, "critical_pct": 0.50},
        "hypotheses": [
            {
                "id": "generic_gold_deviation",
                "title": "Desviacion matematica en Gold",
                "rationale": (
                    "Control Room detecto una desviacion contra baseline "
                    "sin contrato especifico del cartucho."
                ),
            }
        ],
        "action_templates": [
            {
                "id": "review_generic_gold_signal",
                "label": "Revisar senal Gold",
                "action_kind": "owner_review",
                "impact_multiplier": 0.20,
                "cost": 80,
                "risk": 60,
                "time_cost": 50,
            },
            {
                "id": "monitor_generic_gold_signal",
                "label": "Mantener monitoreo",
                "action_kind": "monitor",
                "impact_multiplier": 0.0,
                "cost": 0,
                "risk": 30,
                "time_cost": 0,
            },
        ],
        "_generic_inferred": True,
        "_normalized_rows": normalized,
    }


def rows_for_generic_metric(metric: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return metric.get("_normalized_rows") if isinstance(metric.get("_normalized_rows"), list) else rows


def _monte_carlo_for_artifact(
    *,
    signal: dict[str, Any],
    baseline: dict[str, Any],
    decision: dict[str, Any],
    metric: dict[str, Any],
) -> dict[str, Any]:
    template = metric.get("simulation_template")
    if isinstance(template, dict):
        payload = _payload_from_template(
            template=template,
            signal=signal,
            baseline=baseline,
            decision=decision,
        )
        mode = "template_mode"
    else:
        payload = _payload_from_history(
            signal=signal,
            baseline=baseline,
            decision=decision,
        )
        mode = "derived_mode"
    if payload is None:
        return {
            "status": "skipped",
            "mode": mode,
            "reason": "insufficient_history" if mode == "derived_mode" else "missing_monte_carlo_inputs",
        }
    seed = int(sample_hash({"signal_id": signal.get("signal_id"), "mode": mode, "version": RULESET_VERSION})[:8], 16)
    payload.setdefault("seed", seed)
    payload.setdefault("iterations", 1000)
    payload.setdefault("output_metric", "net_value")
    payload.setdefault("model_version", monte_carlo.MODEL_VERSION)
    try:
        result = monte_carlo.run_monte_carlo(payload)
    except monte_carlo.MonteCarloValidationError as exc:
        return {
            "status": "skipped",
            "mode": mode,
            "reason": str(exc),
            "seed": payload.get("seed"),
        }
    return {
        "status": "completed",
        "mode": mode,
        "seed": payload.get("seed"),
        "iterations": payload.get("iterations"),
        "input_variables": result.get("normalized_input_variables"),
        "distribution_summary": result.get("distribution_summary"),
        "sensitivity": result.get("sensitivity"),
        "option_comparison": result.get("option_comparison"),
        "reproducibility_hash": result.get("reproducibility_hash"),
        "model_version": result.get("model_version"),
    }


def _payload_from_template(
    *,
    template: dict[str, Any],
    signal: dict[str, Any],
    baseline: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any] | None:
    variables = _resolve_value(
        template.get("input_variables"),
        signal=signal,
        baseline=baseline,
        decision=decision,
    )
    if not isinstance(variables, dict) or not variables:
        return None
    payload = {
        "input_variables": variables,
        "iterations": template.get("iterations", 1000),
        "output_metric": template.get("output_metric", "net_value"),
        "breach_threshold": _resolve_value(
            template.get("breach_threshold"),
            signal=signal,
            baseline=baseline,
            decision=decision,
        ),
        "breach_direction": template.get("breach_direction"),
        "assumptions": template.get("assumptions") or {},
    }
    return {key: value for key, value in payload.items() if value is not None}


def _payload_from_history(
    *,
    signal: dict[str, Any],
    baseline: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any] | None:
    history = [num(value) for value in baseline.get("history_values") or []]
    history_values = [value for value in history if value is not None]
    if len(history_values) < 2:
        return None
    center = median(history_values)
    deviations = [abs(value - center) for value in history_values]
    mad = median(deviations) if deviations else 0.0
    sigma = max(1.4826 * mad, abs(center) * 0.05, 1.0)
    actual = num(signal.get("actual_value")) or center
    expected = num(signal.get("expected_value")) or center
    expected_delta = actual - expected
    cost_per_day = _delay_value(decision.get("cost_of_delay"))
    breach_threshold = min(expected, center)
    return {
        "input_variables": {
            "baseline_value": {"type": "fixed", "value": expected},
            "expected_delta": {
                "type": "normal",
                "mean": expected_delta,
                "stddev": sigma,
            },
            "cost_per_day": {"type": "fixed", "value": cost_per_day},
            "delay_days": {"type": "triangular", "low": 0, "mode": 1, "high": 7},
            "probability_of_delay": {"type": "fixed", "value": 0.5},
        },
        "output_metric": "net_value",
        "breach_threshold": breach_threshold,
        "breach_direction": "below",
        "assumptions": {
            "mode": "derived_from_gold_history",
            "history_points": len(history_values),
            "ruleset_version": RULESET_VERSION,
        },
    }


def _resolve_value(
    value: Any,
    *,
    signal: dict[str, Any],
    baseline: dict[str, Any],
    decision: dict[str, Any],
) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return _lookup_path(
            value[1:],
            {"signal": signal, "baseline": baseline, "decision": decision},
        )
    if isinstance(value, dict):
        return {
            key: _resolve_value(
                item,
                signal=signal,
                baseline=baseline,
                decision=decision,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_value(
                item,
                signal=signal,
                baseline=baseline,
                decision=decision,
            )
            for item in value
        ]
    return value


def _lookup_path(path: str, values: dict[str, Any]) -> Any:
    current: Any = values
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _infer_time_field(rows: list[dict[str, Any]]) -> str | None:
    candidate_names = (
        "date",
        "fecha",
        "month",
        "mes",
        "period",
        "period_key",
        "snapshot",
        "created_at",
        "updated_at",
    )
    keys = list(rows[0].keys())
    for key in keys:
        lowered = key.lower()
        if any(name in lowered for name in candidate_names):
            return key
    for key in keys:
        values = [row.get(key) for row in rows[:20]]
        parsed = sum(1 for value in values if time_key(value)[0] == 2)
        if parsed >= max(2, len(values) // 2):
            return key
    return None


def _ensure_time_field(rows: list[dict[str, Any]], time_field: str) -> list[dict[str, Any]]:
    if time_field != "__omega_period":
        return rows
    normalized = []
    for index, row in enumerate(rows):
        copy = dict(row)
        copy[time_field] = f"{index + 1:08d}"
        normalized.append(copy)
    return normalized


def _infer_value_field(rows: list[dict[str, Any]], time_field: str) -> str | None:
    scores: list[tuple[float, str]] = []
    for key in rows[0].keys():
        if key == time_field:
            continue
        values = [num(row.get(key)) for row in rows]
        numeric = [value for value in values if value is not None]
        if len(numeric) < max(3, min(len(rows), 3)):
            continue
        spread = max(numeric) - min(numeric)
        scores.append((spread, key))
    if not scores:
        return None
    scores.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scores[0][1]


def _infer_entity_field(
    rows: list[dict[str, Any]],
    time_field: str,
    value_field: str,
) -> str | None:
    for key in rows[0].keys():
        if key in {time_field, value_field}:
            continue
        values = [row.get(key) for row in rows]
        non_empty = [str(value).strip() for value in values if str(value or "").strip()]
        if not non_empty:
            continue
        if any(num(value) is None for value in non_empty):
            return key
    return None


def _money_value(value: Any) -> float:
    if isinstance(value, dict):
        return abs(num(value.get("value")) or 0.0)
    return 0.0


def _delay_value(value: Any) -> float:
    if isinstance(value, dict):
        return abs(num(value.get("value_per_day")) or 0.0)
    return 0.0


def _clamp(value: float | None, lower: float, upper: float) -> float:
    parsed = float(value if value is not None else lower)
    return max(lower, min(upper, parsed))
