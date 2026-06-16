from __future__ import annotations

import pytest

from app.services.intelligence import calibration


def _payload(**overrides):
    payload = {
        "source_type": "manual_fixture",
        "source_id": "fixture-a",
        "predicted_metric": "net_value",
        "predicted_probability": 0.8,
        "predicted_value": 100.0,
        "predicted_interval": {"low": 80.0, "high": 120.0},
        "actual_value": 110.0,
        "actual_status": "hit",
        "calibration_group": "monte_carlo",
        "model_version": "cal.test.v1",
    }
    payload.update(overrides)
    return payload


def test_beta_binomial_prior_hit_miss_partial_and_unknown_updates():
    state = calibration.empty_state(calibration_group="monte_carlo")
    hit = calibration.apply_observation(state, _payload(actual_status="hit"))
    assert hit["state"]["posterior"]["alpha"] == 2.0
    assert hit["state"]["posterior"]["beta"] == 1.0

    miss = calibration.apply_observation(hit["state"], _payload(actual_status="miss"))
    assert miss["state"]["posterior"]["alpha"] == 2.0
    assert miss["state"]["posterior"]["beta"] == 2.0

    partial = calibration.apply_observation(miss["state"], _payload(actual_status="partial"))
    assert partial["state"]["posterior"]["alpha"] == 2.5
    assert partial["state"]["posterior"]["beta"] == 2.5

    unknown = calibration.apply_observation(partial["state"], _payload(actual_status="unknown"))
    assert unknown["state"]["posterior"]["alpha"] == 2.5
    assert unknown["state"]["posterior"]["beta"] == 2.5
    assert unknown["metrics"]["unknown_count"] == 1


def test_metrics_include_brier_mae_rmse_coverage_and_confidence():
    result = calibration.apply_observation(None, _payload())
    metrics = result["metrics"]

    assert metrics["sample_count"] == 1
    assert metrics["brier_score"] == pytest.approx(0.04)
    assert metrics["mae"] == pytest.approx(10.0)
    assert metrics["rmse"] == pytest.approx(10.0)
    assert metrics["coverage_p10_p90"] == pytest.approx(1.0)
    assert 0 <= metrics["confidence_score"] <= 1
    interval = result["state"]["posterior"]["credible_interval"]
    assert 0 <= interval["low"] <= interval["high"] <= 1
    assert interval["method"] == "beta_normal_approx"


def test_recompute_state_is_reproducible_for_same_observations():
    observations = [
        _payload(actual_status="hit", actual_value=110.0),
        _payload(actual_status="miss", actual_value=70.0),
        _payload(actual_status="partial", actual_value=95.0),
    ]

    first = calibration.recompute_state(
        observations,
        calibration_group="monte_carlo",
        model_version="cal.test.v1",
    )
    second = calibration.recompute_state(
        observations,
        calibration_group="monte_carlo",
        model_version="cal.test.v1",
    )

    assert first["posterior"] == second["posterior"]
    assert first["metrics"] == second["metrics"]
    assert first["reproducibility_hash"] == second["reproducibility_hash"]


def test_invalid_inputs_fail_closed():
    with pytest.raises(calibration.CalibrationValidationError):
        calibration.apply_observation(None, _payload(actual_status="maybe"))
    with pytest.raises(calibration.CalibrationValidationError):
        calibration.apply_observation(None, _payload(predicted_probability=1.5))
    with pytest.raises(calibration.CalibrationValidationError):
        calibration.apply_observation(
            None,
            _payload(predicted_interval={"low": 10, "high": 1}),
        )
    with pytest.raises(calibration.CalibrationValidationError):
        calibration.apply_observation(None, _payload(predicted_metric=""))


def test_live_calibration_without_state_returns_raw_probability():
    result = calibration.apply_calibration_to_probability(
        0.72,
        None,
        calibration_group="source_type:hubspot:forecast_weighted:v1",
    )

    assert result["raw_probability"] == pytest.approx(0.72)
    assert result["calibrated_probability"] == pytest.approx(0.72)
    assert result["calibration_applied"] is False
    assert result["calibration_reason"] == "missing_calibration_state"
    assert result["disclaimer"] == calibration.RAW_HEURISTIC_DISCLAIMER


def test_live_calibration_requires_enough_samples():
    state = {
        "calibration_group": "source_type:hubspot:forecast_weighted:v1",
        "posterior": {"alpha": 8.0, "beta": 2.0, "mean": 0.8},
        "metrics": {"sample_count": 4, "confidence_score": 1.0},
    }

    result = calibration.apply_calibration_to_probability(0.4, state, min_samples=10)

    assert result["calibrated_probability"] == pytest.approx(0.4)
    assert result["calibration_applied"] is False
    assert result["calibration_reason"] == "insufficient_calibration_data"
    assert result["posterior_mean"] == pytest.approx(0.8)


def test_live_calibration_moves_probability_up_down_and_respects_max_adjustment():
    high_state = {
        "calibration_group": "source_type:hubspot:forecast_weighted:v1",
        "posterior": {"alpha": 30.0, "beta": 2.0, "mean": 0.9375},
        "metrics": {"sample_count": 50, "confidence_score": 1.0},
    }
    low_state = {
        "calibration_group": "source_type:hubspot:forecast_weighted:v1",
        "posterior": {"alpha": 2.0, "beta": 30.0, "mean": 0.0625},
        "metrics": {"sample_count": 50, "confidence_score": 1.0},
    }

    raised = calibration.apply_calibration_to_probability(
        0.5,
        high_state,
        max_adjustment=0.2,
    )
    lowered = calibration.apply_calibration_to_probability(
        0.5,
        low_state,
        max_adjustment=0.2,
    )

    assert raised["calibration_applied"] is True
    assert raised["calibrated_probability"] == pytest.approx(0.7)
    assert lowered["calibration_applied"] is True
    assert lowered["calibrated_probability"] == pytest.approx(0.3)


def test_live_calibration_confidence_score_affects_weight():
    high_confidence = {
        "calibration_group": "source_type:hubspot:forecast_weighted:v1",
        "posterior": {"alpha": 18.0, "beta": 2.0, "mean": 0.9},
        "metrics": {"sample_count": 10, "confidence_score": 1.0},
    }
    low_confidence = {
        **high_confidence,
        "metrics": {"sample_count": 10, "confidence_score": 0.4},
    }

    strong = calibration.apply_calibration_to_probability(
        0.5,
        high_confidence,
        max_adjustment=0.5,
    )
    weak = calibration.apply_calibration_to_probability(
        0.5,
        low_confidence,
        max_adjustment=0.5,
    )

    assert strong["weight"] > weak["weight"]
    assert strong["calibrated_probability"] > weak["calibrated_probability"]


def test_live_calibration_rejects_invalid_probability():
    with pytest.raises(calibration.CalibrationValidationError):
        calibration.apply_calibration_to_probability(1.2, None)


def test_partial_pooling_uses_fixed_prior_without_sufficient_parent():
    assert calibration.derive_partial_pooling_prior(None) == {
        "alpha": 1.0,
        "beta": 1.0,
        "prior_source": "fixed",
        "partial_pooling_applied": False,
    }

    parent = {
        "calibration_group": "global:forecast_weighted:v1",
        "posterior": {"alpha": 5.0, "beta": 5.0, "mean": 0.5},
        "metrics": {"sample_count": 4},
    }
    prior = calibration.derive_partial_pooling_prior(parent, min_parent_samples=5)

    assert prior["prior_source"] == "fixed"
    assert prior["partial_pooling_applied"] is False


def test_partial_pooling_derives_capped_prior_from_parent():
    parent = {
        "calibration_group": "global:forecast_weighted:v1",
        "posterior": {"alpha": 71.0, "beta": 31.0, "mean": 71 / 102},
        "metrics": {"sample_count": 100},
    }

    prior = calibration.derive_partial_pooling_prior(
        parent,
        parent_calibration_group="global:forecast_weighted:v1",
        prior_source="global",
        max_parent_prior_strength=20,
    )

    assert prior["partial_pooling_applied"] is True
    assert prior["prior_source"] == "global"
    assert prior["parent_sample_count"] == 100
    assert prior["derived_prior_alpha"] == pytest.approx(14.921569)
    assert prior["derived_prior_beta"] == pytest.approx(7.078431)


def test_recompute_state_with_partial_pooling_prior_is_reproducible():
    parent = {
        "calibration_group": "global:forecast_weighted:v1",
        "posterior": {"alpha": 9.0, "beta": 3.0, "mean": 0.75},
        "metrics": {"sample_count": 12},
    }
    prior = calibration.derive_partial_pooling_prior(
        parent,
        parent_calibration_group="global:forecast_weighted:v1",
        prior_source="global",
    )
    observations = [_payload(actual_status="hit")]

    first = calibration.recompute_state(
        observations,
        calibration_group="source_type:hubspot:forecast_weighted:v1",
        model_version="cal.test.v1",
        prior=prior,
    )
    second = calibration.recompute_state(
        observations,
        calibration_group="source_type:hubspot:forecast_weighted:v1",
        model_version="cal.test.v1",
        prior=prior,
    )

    assert first["posterior"] == second["posterior"]
    assert first["metrics"] == second["metrics"]
    assert first["metrics"]["partial_pooling_applied"] is True
    assert first["metrics"]["parent_calibration_group"] == "global:forecast_weighted:v1"
