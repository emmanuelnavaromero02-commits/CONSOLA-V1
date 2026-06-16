from __future__ import annotations

import hashlib
import json
import math
import random
from statistics import mean, median
from typing import Any


MODEL_VERSION = "monte_carlo.v1"
DEFAULT_ITERATIONS = 1000
MAX_ITERATIONS = 10_000
SUPPORTED_DISTRIBUTIONS = {"fixed", "normal", "triangular", "uniform", "discrete"}
SUPPORTED_OUTPUT_METRICS = {"net_value", "delta", "cost", "delay_days"}


class MonteCarloValidationError(ValueError):
    pass


def _json_default(value: Any) -> Any:
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def reproducibility_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or value is None:
        raise MonteCarloValidationError(f"{field} must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise MonteCarloValidationError(f"{field} must be numeric") from exc
    if math.isnan(parsed) or math.isinf(parsed):
        raise MonteCarloValidationError(f"{field} must be finite")
    return parsed


def _clean_iterations(value: Any) -> int:
    if value is None:
        return DEFAULT_ITERATIONS
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MonteCarloValidationError("iterations must be an integer") from exc
    if parsed < 1 or parsed > MAX_ITERATIONS:
        raise MonteCarloValidationError(f"iterations must be between 1 and {MAX_ITERATIONS}")
    return parsed


def _validate_distribution(name: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise MonteCarloValidationError(f"{name} distribution must be an object")
    dist_type = str(spec.get("type") or spec.get("distribution") or "").strip().lower()
    if dist_type not in SUPPORTED_DISTRIBUTIONS:
        raise MonteCarloValidationError(f"{name} has unsupported distribution type")

    if dist_type == "fixed":
        return {"type": dist_type, "value": _finite_number(spec.get("value"), f"{name}.value")}
    if dist_type == "normal":
        stddev = _finite_number(spec.get("stddev", spec.get("sd")), f"{name}.stddev")
        if stddev < 0:
            raise MonteCarloValidationError(f"{name}.stddev must be >= 0")
        return {
            "type": dist_type,
            "mean": _finite_number(spec.get("mean"), f"{name}.mean"),
            "stddev": stddev,
        }
    if dist_type == "triangular":
        low = _finite_number(spec.get("low"), f"{name}.low")
        mode = _finite_number(spec.get("mode"), f"{name}.mode")
        high = _finite_number(spec.get("high"), f"{name}.high")
        if not low <= mode <= high:
            raise MonteCarloValidationError(f"{name} triangular requires low <= mode <= high")
        return {"type": dist_type, "low": low, "mode": mode, "high": high}
    if dist_type == "uniform":
        low = _finite_number(spec.get("low"), f"{name}.low")
        high = _finite_number(spec.get("high"), f"{name}.high")
        if low > high:
            raise MonteCarloValidationError(f"{name}.low must be <= high")
        return {"type": dist_type, "low": low, "high": high}

    raw_values = spec.get("values")
    if not isinstance(raw_values, list) or not raw_values:
        raise MonteCarloValidationError(f"{name}.values must be a non-empty list")
    values: list[dict[str, float]] = []
    for idx, item in enumerate(raw_values):
        if isinstance(item, dict):
            val = _finite_number(item.get("value"), f"{name}.values[{idx}].value")
            weight = _finite_number(item.get("weight", 1), f"{name}.values[{idx}].weight")
        else:
            val = _finite_number(item, f"{name}.values[{idx}]")
            weight = 1.0
        if weight <= 0:
            raise MonteCarloValidationError(f"{name}.values[{idx}].weight must be > 0")
        values.append({"value": val, "weight": weight})
    return {"type": dist_type, "values": values}


def validate_variables(input_variables: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(input_variables, dict) or not input_variables:
        raise MonteCarloValidationError("input_variables must be a non-empty object")
    if len(input_variables) > 50:
        raise MonteCarloValidationError("input_variables cannot contain more than 50 variables")
    cleaned: dict[str, dict[str, Any]] = {}
    for raw_name, spec in input_variables.items():
        name = str(raw_name or "").strip()
        if not name or len(name) > 80 or not name.replace("_", "").isalnum():
            raise MonteCarloValidationError("input variable names must be simple identifiers")
        cleaned[name] = _validate_distribution(name, spec)
    return cleaned


def _sample_distribution(rng: random.Random, spec: dict[str, Any]) -> float:
    dist_type = spec["type"]
    if dist_type == "fixed":
        return float(spec["value"])
    if dist_type == "normal":
        return float(rng.gauss(float(spec["mean"]), float(spec["stddev"])))
    if dist_type == "triangular":
        return float(rng.triangular(float(spec["low"]), float(spec["high"]), float(spec["mode"])))
    if dist_type == "uniform":
        return float(rng.uniform(float(spec["low"]), float(spec["high"])))

    values = spec["values"]
    total = sum(float(item["weight"]) for item in values)
    pick = rng.uniform(0, total)
    cumulative = 0.0
    for item in values:
        cumulative += float(item["weight"])
        if pick <= cumulative:
            return float(item["value"])
    return float(values[-1]["value"])


def _metric_value(samples: dict[str, float], output_metric: str) -> float:
    baseline = samples.get("baseline_value", 0.0)
    expected_delta = samples.get("expected_delta", 0.0)
    delay_days = samples.get("delay_days", 0.0)
    approval_lag_days = samples.get("approval_lag_days", 0.0)
    cost_per_day = samples.get("cost_per_day", 0.0)
    probability_of_delay = samples.get("probability_of_delay", 1.0)
    adoption_rate = samples.get("adoption_rate", 0.0)
    recovery_rate = samples.get("recovery_rate", 0.0)
    manual_effort_hours = samples.get("manual_effort_hours", 0.0)
    hourly_cost = samples.get("hourly_cost", 0.0)
    fixed_cost = samples.get("fixed_cost", 0.0)

    delay_total = delay_days + approval_lag_days
    delay_cost = delay_total * cost_per_day * probability_of_delay
    effort_cost = manual_effort_hours * hourly_cost
    benefit = adoption_rate * recovery_rate * baseline
    cost = delay_cost + effort_cost + fixed_cost
    delta = expected_delta + benefit - cost

    if output_metric == "net_value":
        return baseline + delta
    if output_metric == "delta":
        return delta
    if output_metric == "cost":
        return cost
    if output_metric == "delay_days":
        return delay_total
    raise MonteCarloValidationError("unsupported output_metric")


def _quantile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise MonteCarloValidationError("cannot summarize empty results")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * probability
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return sorted_values[int(pos)]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (pos - lower)


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    idx = 0
    while idx < len(ordered):
        end = idx
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[idx][1]:
            end += 1
        avg_rank = (idx + end + 2) / 2.0
        for pos in range(idx, end + 1):
            ranks[ordered[pos][0]] = avg_rank
        idx = end + 1
    return ranks


def _pearson(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = mean(left)
    right_mean = mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_den = math.sqrt(sum((a - left_mean) ** 2 for a in left))
    right_den = math.sqrt(sum((b - right_mean) ** 2 for b in right))
    if left_den == 0 or right_den == 0:
        return 0.0
    return numerator / (left_den * right_den)


def _sensitivity(samples_by_variable: dict[str, list[float]], outputs: list[float]) -> list[dict[str, Any]]:
    output_ranks = _rank(outputs)
    drivers = []
    for name, values in samples_by_variable.items():
        coefficient = _pearson(_rank(values), output_ranks)
        drivers.append(
            {
                "variable": name,
                "method": "spearman_rank",
                "coefficient": round(coefficient, 6),
                "abs_coefficient": round(abs(coefficient), 6),
            }
        )
    return sorted(drivers, key=lambda item: item["abs_coefficient"], reverse=True)


def run_single_simulation(payload: dict[str, Any]) -> dict[str, Any]:
    variables = validate_variables(payload.get("input_variables"))
    iterations = _clean_iterations(payload.get("iterations"))
    output_metric = str(payload.get("output_metric") or "net_value").strip()
    if output_metric not in SUPPORTED_OUTPUT_METRICS:
        raise MonteCarloValidationError("unsupported output_metric")
    seed = int(payload.get("seed", 0))
    threshold_raw = payload.get("breach_threshold")
    threshold = _finite_number(threshold_raw, "breach_threshold") if threshold_raw is not None else None
    breach_direction = str(payload.get("breach_direction") or "").strip().lower()
    if not breach_direction:
        breach_direction = "above" if output_metric in {"cost", "delay_days"} else "below"
    if breach_direction not in {"below", "above"}:
        raise MonteCarloValidationError("breach_direction must be below or above")

    rng = random.Random(seed)
    outputs: list[float] = []
    samples_by_variable: dict[str, list[float]] = {name: [] for name in variables}
    for _ in range(iterations):
        sample = {name: _sample_distribution(rng, spec) for name, spec in variables.items()}
        for name, value in sample.items():
            samples_by_variable[name].append(value)
        outputs.append(_metric_value(sample, output_metric))

    sorted_outputs = sorted(outputs)
    probability_breach = None
    if threshold is not None:
        if breach_direction == "above":
            probability_breach = sum(1 for value in outputs if value >= threshold) / iterations
        else:
            probability_breach = sum(1 for value in outputs if value <= threshold) / iterations

    summary = {
        "iterations": iterations,
        "output_metric": output_metric,
        "mean": round(mean(outputs), 6),
        "median": round(median(outputs), 6),
        "p10": round(_quantile(sorted_outputs, 0.10), 6),
        "p50": round(_quantile(sorted_outputs, 0.50), 6),
        "p90": round(_quantile(sorted_outputs, 0.90), 6),
        "min": round(sorted_outputs[0], 6),
        "max": round(sorted_outputs[-1], 6),
        "probability_loss": round(sum(1 for value in outputs if value < 0) / iterations, 6),
        "probability_breach_threshold": None
        if probability_breach is None
        else round(probability_breach, 6),
        "breach_threshold": threshold,
        "breach_direction": breach_direction,
        "expected_value": round(mean(outputs), 6),
        "worst_case_band": [round(_quantile(sorted_outputs, 0.01), 6), round(_quantile(sorted_outputs, 0.10), 6)],
        "confidence_band": [round(_quantile(sorted_outputs, 0.10), 6), round(_quantile(sorted_outputs, 0.90), 6)],
    }
    return {
        "distribution_summary": summary,
        "sensitivity": _sensitivity(samples_by_variable, outputs),
        "normalized_input_variables": variables,
    }


def _option_seed(base_seed: int, option_id: str, index: int) -> int:
    raw = f"{base_seed}:{index}:{option_id}".encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:16], 16)


def _risk_adjusted_score(summary: dict[str, Any]) -> float:
    expected = float(summary["expected_value"])
    spread = float(summary["p90"]) - float(summary["p10"])
    breach = float(summary.get("probability_breach_threshold") or 0)
    return expected - (spread * 0.25) - (breach * abs(expected if expected else 1.0))


def compare_options(payload: dict[str, Any]) -> dict[str, Any] | None:
    options = payload.get("options")
    if not options:
        return None
    if not isinstance(options, list) or len(options) > 10:
        raise MonteCarloValidationError("options must contain 1 to 10 entries")

    base_seed = int(payload.get("seed", 0))
    option_results: list[dict[str, Any]] = []
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            raise MonteCarloValidationError("each option must be an object")
        option_id = str(option.get("option_id") or "").strip()
        if not option_id or len(option_id) > 120:
            raise MonteCarloValidationError("option_id is required")
        option_payload = {
            **payload,
            "seed": _option_seed(base_seed, option_id, index),
            "input_variables": option.get("input_variables") or payload.get("input_variables"),
            "assumptions": option.get("assumptions") or payload.get("assumptions") or {},
            "options": None,
        }
        result = run_single_simulation(option_payload)
        summary = result["distribution_summary"]
        score = _risk_adjusted_score(summary)
        option_results.append(
            {
                "option_id": option_id,
                "label": option.get("label") or option_id,
                "seed": option_payload["seed"],
                "distribution_summary": summary,
                "sensitivity": result["sensitivity"][:10],
                "risk_adjusted_score": round(score, 6),
            }
        )

    ranked = sorted(option_results, key=lambda item: item["risk_adjusted_score"], reverse=True)
    return {
        "ranking": [
            {
                "rank": index + 1,
                "option_id": item["option_id"],
                "risk_adjusted_score": item["risk_adjusted_score"],
                "expected_value": item["distribution_summary"]["expected_value"],
                "probability_breach_threshold": item["distribution_summary"]["probability_breach_threshold"],
            }
            for index, item in enumerate(ranked)
        ],
        "options": ranked,
    }


def run_monte_carlo(
    payload: dict[str, Any], *, model_version: str | None = None
) -> dict[str, Any]:
    model_version = str(model_version or payload.get("model_version") or MODEL_VERSION)
    single = run_single_simulation(payload)
    option_comparison = compare_options(payload)
    if option_comparison:
        single["distribution_summary"] = option_comparison["options"][0]["distribution_summary"]
        single["sensitivity"] = option_comparison["options"][0]["sensitivity"]

    response = {
        "model_version": model_version,
        "distribution_summary": single["distribution_summary"],
        "sensitivity": single["sensitivity"],
        "option_comparison": option_comparison,
        "normalized_input_variables": single["normalized_input_variables"],
    }
    response["reproducibility_hash"] = reproducibility_hash(
        {
            "model_version": model_version,
            "payload": payload,
            "result": {
                "distribution_summary": response["distribution_summary"],
                "sensitivity": response["sensitivity"],
                "option_comparison": response["option_comparison"],
            },
        }
    )
    return response
