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


def test_monte_carlo_model_version_is_server_owned():
    first = monte_carlo.run_monte_carlo(_payload(model_version="mc.test.v1"))
    second = monte_carlo.run_monte_carlo(_payload(model_version="mc.test.v2"))

    assert first["model_version"] == monte_carlo.MODEL_VERSION
    assert second["model_version"] == monte_carlo.MODEL_VERSION
    assert first["reproducibility_hash"] == second["reproducibility_hash"]
    with pytest.raises(TypeError):
        monte_carlo.run_monte_carlo(_payload(), model_version="mc.test.v999")


def test_monte_carlo_rejects_unknown_and_nonapplicable_variables():
    with pytest.raises(monte_carlo.MonteCarloValidationError, match="unknown"):
        monte_carlo.run_monte_carlo(
            _payload(input_variables={"revenue_growht": {"type": "fixed", "value": 1}})
        )
    with pytest.raises(monte_carlo.MonteCarloValidationError, match="unique"):
        monte_carlo.run_monte_carlo(
            _payload(
                input_variables={
                    "revenue_growth": {"type": "fixed", "value": 0.1},
                    " revenue_growth ": {"type": "fixed", "value": 0.2},
                }
            )
        )
    with pytest.raises(monte_carlo.MonteCarloValidationError, match="not applicable"):
        monte_carlo.run_monte_carlo(
            _payload(
                output_metric="delay_days",
                input_variables={"revenue_growth": {"type": "fixed", "value": 0.1}},
            )
        )


def test_revenue_growth_is_consumed_by_the_model():
    baseline = monte_carlo.run_monte_carlo(
        _payload(
            input_variables={
                "baseline_value": {"type": "fixed", "value": 100},
                "revenue_growth": {"type": "fixed", "value": 0},
            }
        )
    )
    growth = monte_carlo.run_monte_carlo(
        _payload(
            input_variables={
                "baseline_value": {"type": "fixed", "value": 100},
                "revenue_growth": {"type": "fixed", "value": 0.2},
            }
        )
    )
    assert growth["distribution_summary"]["expected_value"] == 120
    assert (
        growth["distribution_summary"]["expected_value"]
        > baseline["distribution_summary"]["expected_value"]
    )


def test_equivalent_options_are_ambiguous_in_any_order():
    options = [
        {
            "option_id": "a",
            "input_variables": {"baseline_value": {"type": "fixed", "value": 10}},
        },
        {
            "option_id": "b",
            "input_variables": {"baseline_value": {"type": "fixed", "value": 10}},
        },
    ]
    first = monte_carlo.run_monte_carlo(_payload(options=options))
    second = monte_carlo.run_monte_carlo(_payload(options=list(reversed(options))))
    for result in (first, second):
        assert result["option_comparison"]["status"] == "ambiguous"
        assert result["option_comparison"]["selected_option_id"] is None
        assert {item["rank"] for item in result["option_comparison"]["ranking"]} == {1}
        for option in result["option_comparison"]["options"]:
            assert option["normalized_input_variables"] == {
                "baseline_value": {"type": "fixed", "value": 10.0}
            }
            assert option["assumptions"]["model_default_values"]


def test_variable_sampling_is_independent_of_mapping_order():
    variables = {
        "baseline_value": {"type": "normal", "mean": 100, "stddev": 4},
        "expected_delta": {"type": "normal", "mean": 0.1, "stddev": 0.01},
    }
    first = monte_carlo.run_monte_carlo(_payload(input_variables=variables))
    second = monte_carlo.run_monte_carlo(
        _payload(input_variables=dict(reversed(list(variables.items()))))
    )

    assert first["distribution_summary"] == second["distribution_summary"]


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
            _payload(
                input_variables={"bad": {"type": "normal", "mean": 1, "stddev": -1}}
            )
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
    assert (
        comparison["ranking"][0]["risk_adjusted_score"]
        > comparison["ranking"][1]["risk_adjusted_score"]
    )


@pytest.mark.parametrize(
    ("output_metric", "variable", "better", "worse"),
    [
        ("cost", "fixed_cost", 10, 100),
        ("delay_days", "delay_days", 1, 10),
    ],
)
def test_cost_and_delay_option_rankings_minimize(
    output_metric, variable, better, worse
):
    result = monte_carlo.run_monte_carlo(
        _payload(
            output_metric=output_metric,
            breach_threshold=None,
            input_variables={variable: {"type": "fixed", "value": better}},
            options=[
                {
                    "option_id": "better",
                    "input_variables": {variable: {"type": "fixed", "value": better}},
                },
                {
                    "option_id": "worse",
                    "input_variables": {variable: {"type": "fixed", "value": worse}},
                },
            ],
        )
    )
    comparison = result["option_comparison"]
    assert comparison["selected_option_id"] == "better"
    assert comparison["ranking"][0]["option_id"] == "better"
    assert (
        result["distribution_summary"]
        == comparison["options"][0]["distribution_summary"]
    )


@pytest.mark.parametrize(
    ("name", "mean", "stddev"),
    [
        ("probability_of_delay", 0.5, 2),
        ("delay_days", 1, 10),
        ("revenue_growth", 0, 20),
    ],
)
def test_bounded_variables_reject_non_degenerate_normal(name, mean, stddev):
    with pytest.raises(monte_carlo.MonteCarloValidationError, match="bounded"):
        monte_carlo.run_monte_carlo(
            _payload(
                input_variables={
                    name: {"type": "normal", "mean": mean, "stddev": stddev}
                }
            )
        )


def test_unbounded_normal_remains_reproducible():
    payload = _payload(
        input_variables={"expected_delta": {"type": "normal", "mean": 3, "stddev": 2}}
    )
    assert monte_carlo.run_monte_carlo(payload) == monte_carlo.run_monte_carlo(payload)
