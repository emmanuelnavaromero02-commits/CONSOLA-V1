from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from typing import Any


MODEL_VERSION = "bayesian_calibration.v1"
DEFAULT_PRIOR_ALPHA = 1.0
DEFAULT_PRIOR_BETA = 1.0
STATUS_VALUES = {"hit", "miss", "partial", "unknown"}


class CalibrationValidationError(ValueError):
    pass


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    )


def reproducibility_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _num(value: Any, *, field: str, required: bool = False) -> float | None:
    if value is None:
        if required:
            raise CalibrationValidationError(f"{field} is required")
        return None
    if isinstance(value, bool):
        raise CalibrationValidationError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CalibrationValidationError(f"{field} must be numeric") from exc
    if math.isnan(parsed) or math.isinf(parsed):
        raise CalibrationValidationError(f"{field} must be finite")
    return parsed


def _interval(value: Any) -> dict[str, float] | None:
    if value in (None, {}):
        return None
    if not isinstance(value, dict):
        raise CalibrationValidationError("predicted_interval must be an object")
    low = value.get("low", value.get("p10"))
    high = value.get("high", value.get("p90"))
    low_num = _num(low, field="predicted_interval.low", required=True)
    high_num = _num(high, field="predicted_interval.high", required=True)
    if low_num is None or high_num is None or low_num > high_num:
        raise CalibrationValidationError("predicted_interval must have low <= high")
    return {"low": low_num, "high": high_num}


def _probability(value: Any) -> float | None:
    parsed = _num(value, field="predicted_probability")
    if parsed is None:
        return None
    if parsed < 0 or parsed > 1:
        raise CalibrationValidationError("predicted_probability must be between 0 and 1")
    return parsed


def _status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status not in STATUS_VALUES:
        raise CalibrationValidationError("actual_status must be hit, miss, partial, or unknown")
    return status


def _binary_actual(status: str) -> float | None:
    if status == "hit":
        return 1.0
    if status == "miss":
        return 0.0
    if status == "partial":
        return 0.5
    return None


def _posterior_after(prior: dict[str, float], status: str) -> dict[str, float]:
    alpha = float(prior.get("alpha", DEFAULT_PRIOR_ALPHA))
    beta = float(prior.get("beta", DEFAULT_PRIOR_BETA))
    if alpha <= 0 or beta <= 0:
        raise CalibrationValidationError("prior alpha/beta must be positive")
    if status == "hit":
        alpha += 1.0
    elif status == "miss":
        beta += 1.0
    elif status == "partial":
        alpha += 0.5
        beta += 0.5
    return _posterior_payload(alpha, beta)


def _posterior_payload(alpha: float, beta: float) -> dict[str, float | dict[str, float] | str]:
    total = alpha + beta
    mean = alpha / total
    variance = (alpha * beta) / ((total * total) * (total + 1.0))
    margin = 1.96 * math.sqrt(max(variance, 0.0))
    low = max(0.0, mean - margin)
    high = min(1.0, mean + margin)
    return {
        "alpha": round(alpha, 6),
        "beta": round(beta, 6),
        "mean": round(mean, 6),
        "credible_interval": {
            "low": round(low, 6),
            "high": round(high, 6),
            "method": "beta_normal_approx",
        },
    }


def empty_state(
    *,
    calibration_group: str,
    model_version: str = MODEL_VERSION,
) -> dict[str, Any]:
    posterior = _posterior_payload(DEFAULT_PRIOR_ALPHA, DEFAULT_PRIOR_BETA)
    return {
        "calibration_group": calibration_group,
        "model_version": model_version,
        "prior": {"alpha": DEFAULT_PRIOR_ALPHA, "beta": DEFAULT_PRIOR_BETA},
        "posterior": posterior,
        "metrics": {
            "sample_count": 0,
            "hit_count": 0,
            "miss_count": 0,
            "partial_count": 0,
            "unknown_count": 0,
            "brier_sum": 0.0,
            "brier_count": 0,
            "probability_abs_error_sum": 0.0,
            "probability_count": 0,
            "abs_error_sum": 0.0,
            "squared_error_sum": 0.0,
            "continuous_count": 0,
            "coverage_hit_count": 0,
            "coverage_count": 0,
            "brier_score": None,
            "mae": None,
            "rmse": None,
            "coverage_p10_p90": None,
            "calibration_error": None,
            "confidence_score": 0.0,
        },
    }


def normalize_observation(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CalibrationValidationError("payload must be an object")
    status = _status(payload.get("actual_status"))
    predicted_probability = _probability(payload.get("predicted_probability"))
    predicted_value = _num(payload.get("predicted_value"), field="predicted_value")
    actual_value = _num(payload.get("actual_value"), field="actual_value")
    interval = _interval(payload.get("predicted_interval"))
    metric = str(payload.get("predicted_metric") or "").strip()
    if not metric or len(metric) > 120:
        raise CalibrationValidationError("predicted_metric is required")
    if status in {"hit", "miss", "partial"} and predicted_probability is None and predicted_value is None:
        raise CalibrationValidationError(
            "predicted_probability or predicted_value is required for scored outcomes"
        )
    return {
        "actual_status": status,
        "predicted_metric": metric,
        "predicted_probability": predicted_probability,
        "predicted_value": predicted_value,
        "predicted_interval": interval or {},
        "actual_value": actual_value,
    }


def apply_observation(state: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any]:
    observation = normalize_observation(payload)
    calibration_group = str(payload.get("calibration_group") or "global")
    model_version = str(payload.get("model_version") or MODEL_VERSION)
    current = deepcopy(state) if state else empty_state(
        calibration_group=calibration_group,
        model_version=model_version,
    )
    prior_before = dict(current.get("posterior") or _posterior_payload(1.0, 1.0))
    posterior = _posterior_after(prior_before, observation["actual_status"])
    metrics = dict(current.get("metrics") or {})
    _update_counts(metrics, observation["actual_status"])
    _update_probability_metrics(metrics, observation)
    _update_continuous_metrics(metrics, observation)
    _finalize_metrics(metrics)
    updated_state = {
        **current,
        "calibration_group": calibration_group,
        "model_version": model_version,
        "posterior": posterior,
        "metrics": metrics,
    }
    explanation = _explanation(prior_before, posterior, metrics, observation["actual_status"])
    result_hash = reproducibility_hash(
        {
            "model_version": MODEL_VERSION,
            "prior": prior_before,
            "posterior": posterior,
            "metrics": _public_metrics(metrics),
            "observation": observation,
        }
    )
    return {
        "observation": observation,
        "observation_prior": prior_before,
        "observation_posterior": posterior,
        "state": updated_state,
        "metrics": _public_metrics(metrics),
        "explanation": explanation,
        "reproducibility_hash": result_hash,
    }


def recompute_state(
    observations: list[dict[str, Any]],
    *,
    calibration_group: str,
    model_version: str = MODEL_VERSION,
) -> dict[str, Any]:
    state = empty_state(calibration_group=calibration_group, model_version=model_version)
    last_result: dict[str, Any] | None = None
    for observation in observations:
        last_result = apply_observation(state, observation)
        state = last_result["state"]
    if last_result is None:
        state["reproducibility_hash"] = reproducibility_hash(
            {"model_version": MODEL_VERSION, "state": state}
        )
    else:
        state["reproducibility_hash"] = last_result["reproducibility_hash"]
    return state


def _update_counts(metrics: dict[str, Any], status: str) -> None:
    metrics["sample_count"] = int(metrics.get("sample_count") or 0) + 1
    key = f"{status}_count"
    metrics[key] = int(metrics.get(key) or 0) + 1


def _update_probability_metrics(metrics: dict[str, Any], observation: dict[str, Any]) -> None:
    predicted_probability = observation.get("predicted_probability")
    actual = _binary_actual(observation["actual_status"])
    if predicted_probability is None or actual is None:
        return
    error = predicted_probability - actual
    metrics["brier_sum"] = float(metrics.get("brier_sum") or 0.0) + error * error
    metrics["brier_count"] = int(metrics.get("brier_count") or 0) + 1
    metrics["probability_abs_error_sum"] = float(
        metrics.get("probability_abs_error_sum") or 0.0
    ) + abs(error)
    metrics["probability_count"] = int(metrics.get("probability_count") or 0) + 1


def _update_continuous_metrics(metrics: dict[str, Any], observation: dict[str, Any]) -> None:
    predicted_value = observation.get("predicted_value")
    actual_value = observation.get("actual_value")
    if predicted_value is not None and actual_value is not None:
        error = actual_value - predicted_value
        metrics["abs_error_sum"] = float(metrics.get("abs_error_sum") or 0.0) + abs(error)
        metrics["squared_error_sum"] = float(metrics.get("squared_error_sum") or 0.0) + error * error
        metrics["continuous_count"] = int(metrics.get("continuous_count") or 0) + 1
    interval = observation.get("predicted_interval") or {}
    if actual_value is not None and interval:
        if float(interval["low"]) <= actual_value <= float(interval["high"]):
            metrics["coverage_hit_count"] = int(metrics.get("coverage_hit_count") or 0) + 1
        metrics["coverage_count"] = int(metrics.get("coverage_count") or 0) + 1


def _finalize_metrics(metrics: dict[str, Any]) -> None:
    brier_count = int(metrics.get("brier_count") or 0)
    probability_count = int(metrics.get("probability_count") or 0)
    continuous_count = int(metrics.get("continuous_count") or 0)
    coverage_count = int(metrics.get("coverage_count") or 0)
    if brier_count:
        metrics["brier_score"] = round(float(metrics.get("brier_sum") or 0.0) / brier_count, 6)
    if probability_count:
        metrics["calibration_error"] = round(
            float(metrics.get("probability_abs_error_sum") or 0.0) / probability_count,
            6,
        )
    if continuous_count:
        metrics["mae"] = round(float(metrics.get("abs_error_sum") or 0.0) / continuous_count, 6)
        metrics["rmse"] = round(
            math.sqrt(float(metrics.get("squared_error_sum") or 0.0) / continuous_count),
            6,
        )
    if coverage_count:
        metrics["coverage_p10_p90"] = round(
            int(metrics.get("coverage_hit_count") or 0) / coverage_count,
            6,
        )
    effective_count = (
        int(metrics.get("hit_count") or 0)
        + int(metrics.get("miss_count") or 0)
        + int(metrics.get("partial_count") or 0)
    )
    sample_factor = min(1.0, math.sqrt(max(effective_count, 0) / 25.0))
    calibration_error = float(metrics.get("calibration_error") or 0.0)
    coverage = metrics.get("coverage_p10_p90")
    coverage_penalty = abs(0.8 - float(coverage)) if coverage is not None else 0.0
    score = sample_factor * max(0.0, 1.0 - (0.5 * calibration_error) - (0.25 * coverage_penalty))
    metrics["confidence_score"] = round(max(0.0, min(1.0, score)), 6)


def _public_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "sample_count",
        "hit_count",
        "miss_count",
        "partial_count",
        "unknown_count",
        "brier_score",
        "mae",
        "rmse",
        "coverage_p10_p90",
        "calibration_error",
        "confidence_score",
    )
    return {key: metrics.get(key) for key in keys}


def _explanation(
    prior: dict[str, Any],
    posterior: dict[str, Any],
    metrics: dict[str, Any],
    status: str,
) -> str:
    return (
        f"Observed {status}; posterior mean moved from "
        f"{float(prior.get('mean') or 0.0):.3f} to {float(posterior.get('mean') or 0.0):.3f} "
        f"over {int(metrics.get('sample_count') or 0)} observations."
    )
