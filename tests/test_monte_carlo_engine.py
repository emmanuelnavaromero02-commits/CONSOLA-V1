from __future__ import annotations

import pytest

from app.services.intelligence import monte_carlo


def _payload(**overrides):
    payload = {
        "source_type": "manual_fixture",
        "source_id": "synthetic-case",
        "horizon_days": 30,
        "iterations": 500,
        "seed": 12345,
        "output_metric": "net_value",
        "breach_threshold": 900,
        "input_variables": {
            "baseline_value": {"type": "fixed", "value": 1000},
            "expected_delta": {"type": "normal", "mean": 50, "stddev": 5},
            "delay_days": {"type": "triangular", "low": 0, "mode": 2, "high": 8},
            "cost_per_day": {"type": "uniform", "low": 5, "high": 20},
            "probability_of_delay": {
                "type": "discrete",
                "values": [{"value": 0, "weight": 1}, {"value": 1, "weight": 3}],
            },
        },
    }
    payload.update(overrides)
    return payload


def test_monte_carlo_is_reproducible_for_same_seed_and_inputs():
    first = monte_carlo.run_monte_carlo(_payload())
    second = monte_carlo.run_monte_carlo(_payload())

    assert first["reproducibility_hash"] == second["reproducibility_hash"]
    assert first["distribution_summary"] == second["distribution_summary"]
    assert first["sensitivity"] == second["sensitivity"]


def test_monte_carlo_model_version_participates_in_result_and_hash():
    first = monte_carlo.run_monte_carlo(_payload(model_version="mc.test.v1"))
    second = monte_carlo.run_monte_carlo(_payload(model_version="mc.test.v2"))

    assert first["model_version"] == "mc.test.v1"
    assert second["model_version"] == "mc.test.v2"
    assert first["reproducibility_hash"] != second["reproducibility_hash"]


def test_monte_carlo_distribution_summary_contains_required_percentiles():
    result = monte_carlo.run_monte_carlo(_payload(output_metric="delta"))
    summary = result["distribution_summary"]

    assert summary["iterations"] == 500
    assert summary["output_metric"] == "delta"
    assert summary["p10"] <= summary["p50"] <= summary["p90"]
    assert summary["min"] <= summary["median"] <= summary["max"]
    assert 0 <= summary["probability_loss"] <= 1
    assert 0 <= summary["probability_breach_threshold"] <= 1
    assert summary["confidence_band"] == [summary["p10"], summary["p90"]]


def test_monte_carlo_accepts_distribution_alias_for_type():
    result = monte_carlo.run_monte_carlo(
        _payload(
            input_variables={
                "baseline_value": {"distribution": "fixed", "value": 100},
                "expected_delta": {"distribution": "uniform", "low": 1, "high": 2},
            }
        )
    )

    variables = result["normalized_input_variables"]
    assert variables["baseline_value"]["type"] == "fixed"
    assert variables["expected_delta"]["type"] == "uniform"


def test_monte_carlo_rejects_invalid_distributions_and_iteration_limits():
    with pytest.raises(monte_carlo.MonteCarloValidationError):
        monte_carlo.run_monte_carlo(
            _payload(input_variables={"bad": {"type": "normal", "mean": 1, "stddev": -1}})
        )

    with pytest.raises(monte_carlo.MonteCarloValidationError):
        monte_carlo.run_monte_carlo(_payload(iterations=monte_carlo.MAX_ITERATIONS + 1))


def test_monte_carlo_sensitivity_identifies_dominant_driver():
    result = monte_carlo.run_monte_carlo(
        _payload(
            iterations=800,
            input_variables={
                "baseline_value": {"type": "fixed", "value": 1000},
                "expected_delta": {"type": "uniform", "low": -500, "high": 500},
                "cost_per_day": {"type": "fixed", "value": 1},
                "delay_days": {"type": "fixed", "value": 1},
            },
        )
    )

    assert result["sensitivity"][0]["variable"] == "expected_delta"
    assert result["sensitivity"][0]["method"] == "spearman_rank"


def test_monte_carlo_compares_options_without_extra_dependencies():
    result = monte_carlo.run_monte_carlo(
        _payload(
            breach_threshold=None,
            input_variables={"baseline_value": {"type": "fixed", "value": 0}},
            options=[
                {
                    "option_id": "low",
                    "input_variables": {
                        "baseline_value": {"type": "fixed", "value": 100},
                        "expected_delta": {"type": "fixed", "value": 10},
                    },
                },
                {
                    "option_id": "high",
                    "input_variables": {
                        "baseline_value": {"type": "fixed", "value": 100},
                        "expected_delta": {"type": "fixed", "value": 100},
                    },
                },
            ],
        )
    )

    comparison = result["option_comparison"]
    assert comparison["ranking"][0]["option_id"] == "high"
    assert comparison["ranking"][0]["risk_adjusted_score"] > comparison["ranking"][1][
        "risk_adjusted_score"
    ]
