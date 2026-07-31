from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Number
from statistics import mean, median
from typing import Any, Callable


ERROR = "monte carlo produced a non-finite result"


def finite(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(ERROR) from exc
    if not math.isfinite(parsed):
        raise ValueError(ERROR)
    return parsed


def assert_finite_tree(value: Any) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, Number):
        finite(value)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            assert_finite_tree(item)
        return
    if isinstance(value, Sequence):
        for item in value:
            assert_finite_tree(item)


def metric_value(
    samples: dict[str, float],
    output_metric: str,
    *,
    default_value: Callable[[str], float],
) -> float:
    def value(name: str) -> float:
        return finite(samples.get(name, default_value(name)))

    baseline = value("baseline_value")
    expected_delta = value("expected_delta")
    revenue_growth = value("revenue_growth")
    delay_days = value("delay_days")
    approval_lag_days = value("approval_lag_days")
    cost_per_day = value("cost_per_day")
    probability_of_delay = value("probability_of_delay")
    adoption_rate = value("adoption_rate")
    recovery_rate = value("recovery_rate")
    manual_effort_hours = value("manual_effort_hours")
    hourly_cost = value("hourly_cost")
    fixed_cost = value("fixed_cost")

    delay_total = finite(delay_days + approval_lag_days)
    delay_cost = finite(finite(delay_total * cost_per_day) * probability_of_delay)
    effort_cost = finite(manual_effort_hours * hourly_cost)
    revenue_benefit = finite(revenue_growth * baseline)
    recovery_benefit = finite(finite(adoption_rate * recovery_rate) * baseline)
    benefit = finite(revenue_benefit + recovery_benefit)
    cost = finite(finite(delay_cost + effort_cost) + fixed_cost)
    delta = finite(finite(expected_delta + benefit) - cost)

    outputs = {
        "net_value": lambda: finite(baseline + delta),
        "delta": lambda: delta,
        "cost": lambda: cost,
        "delay_days": lambda: delay_total,
    }
    try:
        return outputs[output_metric]()
    except KeyError as exc:
        raise ValueError("unsupported output_metric") from exc


def quantile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("cannot summarize empty results")
    if len(sorted_values) == 1:
        return finite(sorted_values[0])
    pos = finite((len(sorted_values) - 1) * probability)
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return finite(sorted_values[int(pos)])
    delta = finite(sorted_values[upper] - sorted_values[lower])
    return finite(sorted_values[lower] + finite(delta * (pos - lower)))


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    idx = 0
    while idx < len(ordered):
        end = idx
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[idx][1]:
            end += 1
        avg_rank = finite((idx + end + 2) / 2.0)
        for pos in range(idx, end + 1):
            ranks[ordered[pos][0]] = avg_rank
        idx = end + 1
    return ranks


def _pearson(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = finite(mean(left))
    right_mean = finite(mean(right))
    numerator = finite(
        sum(
            finite((a - left_mean) * (b - right_mean))
            for a, b in zip(left, right, strict=True)
        )
    )
    left_den = finite(math.sqrt(sum(finite((a - left_mean) ** 2) for a in left)))
    right_den = finite(math.sqrt(sum(finite((b - right_mean) ** 2) for b in right)))
    if left_den == 0 or right_den == 0:
        return 0.0
    return finite(numerator / finite(left_den * right_den))


def sensitivity(
    samples_by_variable: dict[str, list[float]], outputs: list[float]
) -> list[dict[str, Any]]:
    output_ranks = _rank(outputs)
    drivers = []
    for name, values in samples_by_variable.items():
        coefficient = finite(_pearson(_rank(values), output_ranks))
        drivers.append(
            {
                "variable": name,
                "method": "spearman_rank",
                "coefficient": round(coefficient, 6),
                "abs_coefficient": round(finite(abs(coefficient)), 6),
            }
        )
    return sorted(drivers, key=lambda item: item["abs_coefficient"], reverse=True)


def summary(
    outputs: list[float],
    *,
    iterations: int,
    output_metric: str,
    threshold: float | None,
    breach_direction: str,
) -> dict[str, Any]:
    for output in outputs:
        finite(output)
    sorted_outputs = sorted(outputs)
    probability_breach = None
    if threshold is not None:
        matches = (
            sum(value >= threshold for value in outputs)
            if breach_direction == "above"
            else sum(value <= threshold for value in outputs)
        )
        probability_breach = finite(matches / iterations)
    average = finite(mean(outputs))
    result = {
        "iterations": iterations,
        "output_metric": output_metric,
        "mean": round(average, 6),
        "median": round(finite(median(outputs)), 6),
        "p10": round(quantile(sorted_outputs, 0.10), 6),
        "p50": round(quantile(sorted_outputs, 0.50), 6),
        "p90": round(quantile(sorted_outputs, 0.90), 6),
        "min": round(finite(sorted_outputs[0]), 6),
        "max": round(finite(sorted_outputs[-1]), 6),
        "probability_loss": round(
            finite(sum(value < 0 for value in outputs) / iterations), 6
        ),
        "probability_breach_threshold": (
            None if probability_breach is None else round(probability_breach, 6)
        ),
        "breach_threshold": threshold,
        "breach_direction": breach_direction,
        "expected_value": round(average, 6),
        "worst_case_band": [
            round(quantile(sorted_outputs, 0.01), 6),
            round(quantile(sorted_outputs, 0.10), 6),
        ],
        "confidence_band": [
            round(quantile(sorted_outputs, 0.10), 6),
            round(quantile(sorted_outputs, 0.90), 6),
        ],
    }
    assert_finite_tree(result)
    return result


__all__ = (
    "ERROR",
    "assert_finite_tree",
    "finite",
    "metric_value",
    "sensitivity",
    "summary",
)
