from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VariableRule:
    minimum: float | None = None
    maximum: float | None = None
    default: float = 0.0


VARIABLES = {
    "baseline_value": VariableRule(default=0.0),
    "expected_delta": VariableRule(default=0.0),
    "revenue_growth": VariableRule(-1.0, 10.0, 0.0),
    "delay_days": VariableRule(0.0, None, 0.0),
    "approval_lag_days": VariableRule(0.0, None, 0.0),
    "cost_per_day": VariableRule(0.0, None, 0.0),
    "probability_of_delay": VariableRule(0.0, 1.0, 1.0),
    "adoption_rate": VariableRule(0.0, 1.0, 0.0),
    "recovery_rate": VariableRule(0.0, 1.0, 0.0),
    "manual_effort_hours": VariableRule(0.0, None, 0.0),
    "hourly_cost": VariableRule(0.0, None, 0.0),
    "fixed_cost": VariableRule(0.0, None, 0.0),
}

OUTPUT_VARIABLES = {
    "net_value": frozenset(VARIABLES),
    "delta": frozenset(VARIABLES),
    "cost": frozenset(
        {
            "delay_days",
            "approval_lag_days",
            "cost_per_day",
            "probability_of_delay",
            "manual_effort_hours",
            "hourly_cost",
            "fixed_cost",
        }
    ),
    "delay_days": frozenset({"delay_days", "approval_lag_days"}),
}


def default_value(name: str) -> float:
    return VARIABLES[name].default


def default_assumptions(output_metric: str, supplied: set[str]) -> dict[str, float]:
    return {
        name: VARIABLES[name].default
        for name in sorted(OUTPUT_VARIABLES[output_metric] - supplied)
    }


def _declared_values(spec: dict[str, Any]) -> list[float]:
    kind = spec["type"]
    if kind == "fixed":
        return [float(spec["value"])]
    if kind == "normal":
        return [float(spec["mean"])]
    if kind in {"triangular", "uniform"}:
        return [float(spec["low"]), float(spec["high"])]
    return [float(item["value"]) for item in spec["values"]]


def validate_for_output(
    variables: dict[str, dict[str, Any]], output_metric: str
) -> dict[str, dict[str, Any]]:
    allowed = OUTPUT_VARIABLES.get(output_metric)
    if allowed is None:
        raise ValueError("unsupported output_metric")
    unknown = sorted(set(variables) - set(VARIABLES))
    if unknown:
        raise ValueError(f"unknown input variable: {unknown[0]}")
    unused = sorted(set(variables) - set(allowed))
    if unused:
        raise ValueError(
            f"input variable {unused[0]} is not applicable to {output_metric}"
        )
    for name, spec in variables.items():
        rule = VARIABLES[name]
        for value in _declared_values(spec):
            if rule.minimum is not None and value < rule.minimum:
                raise ValueError(f"{name} must be >= {rule.minimum:g}")
            if rule.maximum is not None and value > rule.maximum:
                raise ValueError(f"{name} must be <= {rule.maximum:g}")
    return variables
