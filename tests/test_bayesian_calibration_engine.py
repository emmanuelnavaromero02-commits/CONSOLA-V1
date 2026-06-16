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
