from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Any

from app.services.intelligence import monte_carlo_contract
from app.services.intelligence import monte_carlo_finite
from app.services.intelligence import monte_carlo_options


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
    try:
        monte_carlo_finite.assert_finite_tree(value)
    except ValueError as exc:
        raise MonteCarloValidationError(str(exc)) from exc
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
        raise MonteCarloValidationError(
            f"iterations must be between 1 and {MAX_ITERATIONS}"
        )
    return parsed


def _validate_distribution(name: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise MonteCarloValidationError(f"{name} distribution must be an object")
    dist_type = str(spec.get("type") or spec.get("distribution") or "").strip().lower()
    if dist_type not in SUPPORTED_DISTRIBUTIONS:
        raise MonteCarloValidationError(f"{name} has unsupported distribution type")

    if dist_type == "fixed":
        return {
            "type": dist_type,
            "value": _finite_number(spec.get("value"), f"{name}.value"),
        }
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
            raise MonteCarloValidationError(
                f"{name} triangular requires low <= mode <= high"
            )
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
            weight = _finite_number(
                item.get("weight", 1), f"{name}.values[{idx}].weight"
            )
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
        raise MonteCarloValidationError(
            "input_variables cannot contain more than 50 variables"
        )
    cleaned: dict[str, dict[str, Any]] = {}
    for raw_name, spec in input_variables.items():
        name = str(raw_name or "").strip()
        if not name or len(name) > 80 or not name.replace("_", "").isalnum():
            raise MonteCarloValidationError(
                "input variable names must be simple identifiers"
            )
        if name in cleaned:
            raise MonteCarloValidationError(
                "input variable names must be unique after normalization"
            )
        cleaned[name] = _validate_distribution(name, spec)
    return cleaned


def _sample_distribution(rng: random.Random, spec: dict[str, Any]) -> float:
    dist_type = spec["type"]
    if dist_type == "fixed":
        return float(spec["value"])
    if dist_type == "normal":
        return float(rng.gauss(float(spec["mean"]), float(spec["stddev"])))
    if dist_type == "triangular":
        return float(
            rng.triangular(float(spec["low"]), float(spec["high"]), float(spec["mode"]))
        )
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


def run_single_simulation(payload: dict[str, Any]) -> dict[str, Any]:
    output_metric = str(payload.get("output_metric") or "net_value").strip()
    if output_metric not in SUPPORTED_OUTPUT_METRICS:
        raise MonteCarloValidationError("unsupported output_metric")
    variables = validate_variables(payload.get("input_variables"))
    try:
        variables = monte_carlo_contract.validate_for_output(variables, output_metric)
    except ValueError as exc:
        raise MonteCarloValidationError(str(exc)) from exc
    iterations = _clean_iterations(payload.get("iterations"))
    seed = int(payload.get("seed", 0))
    threshold_raw = payload.get("breach_threshold")
    threshold = (
        _finite_number(threshold_raw, "breach_threshold")
        if threshold_raw is not None
        else None
    )
    breach_direction = str(payload.get("breach_direction") or "").strip().lower()
    if not breach_direction:
        breach_direction = (
            "above" if output_metric in {"cost", "delay_days"} else "below"
        )
    if breach_direction not in {"below", "above"}:
        raise MonteCarloValidationError("breach_direction must be below or above")

    rng_by_variable = {name: random.Random(f"{seed}:{name}") for name in variables}
    outputs: list[float] = []
    samples_by_variable: dict[str, list[float]] = {name: [] for name in variables}
    for _ in range(iterations):
        try:
            sample = {
                name: _sample_distribution(rng_by_variable[name], spec)
                for name, spec in variables.items()
            }
            for name, value in sample.items():
                samples_by_variable[name].append(
                    monte_carlo_finite.finite(value)
                )
            outputs.append(
                monte_carlo_finite.metric_value(
                    sample,
                    output_metric,
                    default_value=monte_carlo_contract.default_value,
                )
            )
        except ArithmeticError as exc:
            raise MonteCarloValidationError(monte_carlo_finite.ERROR) from exc
        except ValueError as exc:
            raise MonteCarloValidationError(str(exc)) from exc
    try:
        summary = monte_carlo_finite.summary(
            outputs,
            iterations=iterations,
            output_metric=output_metric,
            threshold=threshold,
            breach_direction=breach_direction,
        )
        sensitivity = monte_carlo_finite.sensitivity(samples_by_variable, outputs)
    except ArithmeticError as exc:
        raise MonteCarloValidationError(monte_carlo_finite.ERROR) from exc
    except ValueError as exc:
        raise MonteCarloValidationError(str(exc)) from exc
    return {
        "distribution_summary": summary,
        "sensitivity": sensitivity,
        "normalized_input_variables": variables,
    }


def run_monte_carlo(payload: dict[str, Any]) -> dict[str, Any]:
    model_version = MODEL_VERSION
    server_payload = {
        key: value for key, value in payload.items() if key != "model_version"
    }
    try:
        monte_carlo_finite.assert_finite_tree(server_payload)
    except ValueError as exc:
        raise MonteCarloValidationError(str(exc)) from exc
    single = run_single_simulation(server_payload)
    try:
        option_comparison = monte_carlo_options.compare_options(
            server_payload,
            run_single=run_single_simulation,
            validation_error=MonteCarloValidationError,
            canonical_json=canonical_json,
        )
    except MonteCarloValidationError:
        raise
    except ValueError as exc:
        raise MonteCarloValidationError(str(exc)) from exc
    if option_comparison and option_comparison["status"] == "ranked":
        single["distribution_summary"] = option_comparison["options"][0][
            "distribution_summary"
        ]
        single["sensitivity"] = option_comparison["options"][0]["sensitivity"]

    response = {
        "model_version": model_version,
        "distribution_summary": single["distribution_summary"],
        "sensitivity": single["sensitivity"],
        "option_comparison": option_comparison,
        "normalized_input_variables": single["normalized_input_variables"],
    }
    try:
        monte_carlo_finite.assert_finite_tree(response)
    except ValueError as exc:
        raise MonteCarloValidationError(str(exc)) from exc
    response["reproducibility_hash"] = reproducibility_hash(
        {
            "model_version": model_version,
            "payload": server_payload,
            "result": {
                "distribution_summary": response["distribution_summary"],
                "sensitivity": response["sensitivity"],
                "option_comparison": response["option_comparison"],
            },
        }
    )
    return response
