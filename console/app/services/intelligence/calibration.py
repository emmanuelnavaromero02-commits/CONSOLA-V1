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
DEFAULT_MIN_SAMPLES_FOR_CALIBRATION = 10
DEFAULT_MAX_ADJUSTMENT = 0.20
DEFAULT_MIN_PARENT_SAMPLES = 5
DEFAULT_MAX_PARENT_PRIOR_STRENGTH = 20
LIVE_CALIBRATION_GROUP_VERSION = "v1"
MAX_CALIBRATION_GROUP_LENGTH = 80
RAW_HEURISTIC_DISCLAIMER = "raw heuristic probability; insufficient calibration data"
CALIBRATED_DISCLAIMER = "calibrated with Bayesian posterior"
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


def _required_probability(value: Any, *, field: str = "raw_probability") -> float:
    parsed = _num(value, field=field, required=True)
    if parsed is None or parsed < 0 or parsed > 1:
        raise CalibrationValidationError(f"{field} must be between 0 and 1")
    return parsed


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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


def _fixed_prior() -> dict[str, Any]:
    return {
        "alpha": DEFAULT_PRIOR_ALPHA,
        "beta": DEFAULT_PRIOR_BETA,
        "prior_source": "fixed",
        "partial_pooling_applied": False,
    }


def _normalize_prior(prior: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(prior or _fixed_prior())
    alpha = float(payload.get("alpha", DEFAULT_PRIOR_ALPHA))
    beta = float(payload.get("beta", DEFAULT_PRIOR_BETA))
    if alpha <= 0 or beta <= 0:
        raise CalibrationValidationError("prior alpha/beta must be positive")
    payload["alpha"] = round(alpha, 6)
    payload["beta"] = round(beta, 6)
    payload.setdefault("prior_source", "fixed")
    payload.setdefault("partial_pooling_applied", False)
    return payload


def _pooling_metrics(prior: dict[str, Any]) -> dict[str, Any]:
    return {
        "partial_pooling_applied": bool(prior.get("partial_pooling_applied")),
        "parent_calibration_group": prior.get("parent_calibration_group"),
        "parent_sample_count": int(prior.get("parent_sample_count") or 0),
        "derived_prior_alpha": prior.get("derived_prior_alpha"),
        "derived_prior_beta": prior.get("derived_prior_beta"),
        "prior_source": prior.get("prior_source") or "fixed",
    }


def empty_state(
    *,
    calibration_group: str,
    model_version: str = MODEL_VERSION,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prior_payload = _normalize_prior(prior)
    posterior = _posterior_payload(float(prior_payload["alpha"]), float(prior_payload["beta"]))
    return {
        "calibration_group": calibration_group,
        "model_version": model_version,
        "prior": prior_payload,
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
            **_pooling_metrics(prior_payload),
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
    model_version = MODEL_VERSION
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
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = empty_state(
        calibration_group=calibration_group,
        model_version=model_version,
        prior=prior,
    )
    last_result: dict[str, Any] | None = None
    for observation in observations:
        last_result = apply_observation(state, observation)
        state = last_result["state"]
    if last_result is None:
        state["reproducibility_hash"] = reproducibility_hash(
            {"model_version": model_version, "state": state}
        )
    else:
        state["reproducibility_hash"] = last_result["reproducibility_hash"]
    return state


def _state_metrics(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    metrics = state.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _state_posterior(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    posterior = state.get("posterior")
    return posterior if isinstance(posterior, dict) else {}


def derive_partial_pooling_prior(
    parent_state: dict[str, Any] | None,
    *,
    parent_calibration_group: str | None = None,
    prior_source: str = "global",
    min_parent_samples: int = DEFAULT_MIN_PARENT_SAMPLES,
    max_parent_prior_strength: int = DEFAULT_MAX_PARENT_PRIOR_STRENGTH,
) -> dict[str, Any]:
    metrics = _state_metrics(parent_state)
    sample_count = int(metrics.get("sample_count") or 0)
    if sample_count < min_parent_samples:
        return _fixed_prior()
    posterior = _state_posterior(parent_state)
    alpha = float(posterior.get("alpha") or 0.0)
    beta = float(posterior.get("beta") or 0.0)
    total = alpha + beta
    if alpha <= 0 or beta <= 0 or total <= 0:
        return _fixed_prior()
    parent_mean = float(posterior.get("mean") or (alpha / total))
    parent_mean = _clamp(parent_mean, 0.0, 1.0)
    strength = min(sample_count, max(0, int(max_parent_prior_strength)))
    derived_alpha = 1.0 + parent_mean * strength
    derived_beta = 1.0 + (1.0 - parent_mean) * strength
    return {
        "alpha": round(derived_alpha, 6),
        "beta": round(derived_beta, 6),
        "prior_source": prior_source,
        "partial_pooling_applied": True,
        "parent_calibration_group": parent_calibration_group
        or str((parent_state or {}).get("calibration_group") or ""),
        "parent_sample_count": sample_count,
        "parent_posterior_mean": round(parent_mean, 6),
        "max_parent_prior_strength": int(max_parent_prior_strength),
        "derived_prior_alpha": round(derived_alpha, 6),
        "derived_prior_beta": round(derived_beta, 6),
    }


def apply_calibration_to_probability(
    raw_probability: Any,
    calibration_state: dict[str, Any] | None,
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES_FOR_CALIBRATION,
    max_adjustment: float = DEFAULT_MAX_ADJUSTMENT,
    calibration_group: str | None = None,
) -> dict[str, Any]:
    raw = _required_probability(raw_probability)
    group = calibration_group or (
        str(calibration_state.get("calibration_group"))
        if isinstance(calibration_state, dict) and calibration_state.get("calibration_group")
        else None
    )
    if not calibration_state:
        return _calibration_metadata(
            raw=raw,
            calibrated=raw,
            group=group,
            sample_count=0,
            calibration_applied=False,
            reason="missing_calibration_state",
            disclaimer=RAW_HEURISTIC_DISCLAIMER,
            max_adjustment=max_adjustment,
        )
    metrics = _state_metrics(calibration_state)
    posterior = _state_posterior(calibration_state)
    sample_count = int(metrics.get("sample_count") or 0)
    confidence_score = _clamp(float(metrics.get("confidence_score") or 0.0), 0.0, 1.0)
    posterior_mean = posterior.get("mean")
    posterior_alpha = posterior.get("alpha")
    posterior_beta = posterior.get("beta")
    provenance_complete = metrics.get("complete") is True and metrics.get("provenance_complete") is True
    if not provenance_complete or sample_count < min_samples or posterior_mean is None:
        return _calibration_metadata(
            raw=raw,
            calibrated=raw,
            group=group,
            sample_count=sample_count,
            posterior=posterior,
            metrics=metrics,
            confidence_score=confidence_score,
            calibration_applied=False,
            reason="insufficient_calibration_data" if provenance_complete else "incomplete_calibration_provenance",
            disclaimer=RAW_HEURISTIC_DISCLAIMER,
            max_adjustment=max_adjustment,
        )

    posterior_value = _clamp(float(posterior_mean), 0.0, 1.0)
    sample_factor = _clamp(sample_count / max(float(min_samples * 2), 1.0), 0.0, 1.0)
    weight = _clamp(sample_factor * confidence_score, 0.0, 1.0)
    if weight <= 0:
        return _calibration_metadata(
            raw=raw,
            calibrated=raw,
            group=group,
            sample_count=sample_count,
            posterior=posterior,
            metrics=metrics,
            confidence_score=confidence_score,
            calibration_applied=False,
            reason="zero_calibration_weight",
            disclaimer=RAW_HEURISTIC_DISCLAIMER,
            max_adjustment=max_adjustment,
        )
    blended = raw * (1.0 - weight) + posterior_value * weight
    max_delta = abs(float(max_adjustment))
    delta = _clamp(blended - raw, -max_delta, max_delta)
    calibrated = _clamp(raw + delta, 0.0, 1.0)
    return _calibration_metadata(
        raw=raw,
        calibrated=calibrated,
        group=group,
        sample_count=sample_count,
        posterior={
            "mean": posterior_value,
            "alpha": posterior_alpha,
            "beta": posterior_beta,
        },
        metrics=metrics,
        confidence_score=confidence_score,
        weight=weight,
        calibration_applied=True,
        reason="bayesian_posterior_applied",
        disclaimer=CALIBRATED_DISCLAIMER,
        max_adjustment=max_adjustment,
    )


def _calibration_metadata(
    *,
    raw: float,
    calibrated: float,
    group: str | None,
    sample_count: int,
    calibration_applied: bool,
    reason: str,
    disclaimer: str,
    max_adjustment: float,
    posterior: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    confidence_score: float | None = None,
    weight: float = 0.0,
) -> dict[str, Any]:
    posterior = posterior or {}
    metrics = metrics or {}
    return {
        "raw_probability": round(raw, 6),
        "calibrated_probability": round(calibrated, 6),
        "calibration_applied": bool(calibration_applied),
        "calibration_reason": reason,
        "calibration_group": group,
        "calibration_source": "bayesian_posterior" if calibration_applied else "raw_heuristic",
        "sample_count": int(sample_count),
        "posterior_mean": posterior.get("mean"),
        "posterior_alpha": posterior.get("alpha"),
        "posterior_beta": posterior.get("beta"),
        "confidence_score": round(float(confidence_score or 0.0), 6),
        "weight": round(float(weight or 0.0), 6),
        "max_adjustment": round(abs(float(max_adjustment)), 6),
        "partial_pooling_applied": bool(metrics.get("partial_pooling_applied")),
        "parent_calibration_group": metrics.get("parent_calibration_group"),
        "parent_sample_count": int(metrics.get("parent_sample_count") or 0),
        "prior_source": metrics.get("prior_source") or "fixed",
        "disclaimer": disclaimer,
    }


def _group_component(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    cleaned = []
    for char in text:
        if char.isalnum() or char in {"_", "-"}:
            cleaned.append(char)
        else:
            cleaned.append("_")
    return "".join(cleaned).strip("_") or "unknown"


def _bounded_group(prefix: str, *components: Any) -> str:
    raw = ":".join([prefix, *(_group_component(item) for item in components)])
    if len(raw) <= MAX_CALIBRATION_GROUP_LENGTH:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    keep = MAX_CALIBRATION_GROUP_LENGTH - len(prefix) - len(digest) - 2
    body = "_".join(_group_component(item) for item in components)
    return f"{prefix}:{body[:keep].rstrip('_')}:{digest}"


def global_calibration_group(metric_id: Any) -> str:
    return _bounded_group("global", metric_id, LIVE_CALIBRATION_GROUP_VERSION)


def source_type_calibration_group(source_system: Any, metric_id: Any) -> str:
    return _bounded_group(
        "source_type",
        source_system,
        metric_id,
        LIVE_CALIBRATION_GROUP_VERSION,
    )


def live_calibration_groups(*, source_system: Any, metric_id: Any) -> list[str]:
    source_group = source_type_calibration_group(source_system, metric_id)
    global_group = global_calibration_group(metric_id)
    return [source_group, global_group] if source_group != global_group else [global_group]


def _update_counts(metrics: dict[str, Any], status: str) -> None:
    if status != "unknown":
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
        "partial_pooling_applied",
        "parent_calibration_group",
        "parent_sample_count",
        "derived_prior_alpha",
        "derived_prior_beta",
        "prior_source",
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
