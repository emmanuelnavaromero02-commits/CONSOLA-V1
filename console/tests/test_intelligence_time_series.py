from __future__ import annotations

from app.services import intelligence_engine
from app.services.intelligence.time_series import analyze_time_series


def _metric() -> dict:
    return {
        "id": "metric_value",
        "name": "Metric value",
        "dataset": "monthly_metric",
        "entity": {
            "kind": "account",
            "id_field": "account_id",
            "label_field": "account",
        },
        "time_field": "mes",
        "value_field": "value",
        "expected_behavior": "higher_is_good",
        "baseline": {"method": "moving_average", "minimum_history": 3, "window": 36},
        "impact": {"currency": "USD", "unit_value": 10},
        "signal_rules": {"warning_pct": 0.20, "critical_pct": 0.45},
    }


def _row(month_index: int, value: float, account_id: str = "a1") -> dict:
    year = 2024 + (month_index - 1) // 12
    month = ((month_index - 1) % 12) + 1
    return {
        "mes": f"{year}-{month:02d}-01",
        "account_id": account_id,
        "account": "Account One",
        "value": value,
    }


def test_linear_trend_does_not_create_strong_residual_anomaly():
    history = [_row(month, 100 + month * 5) for month in range(1, 13)]
    latest = _row(13, 100 + 13 * 5)

    result = analyze_time_series(metric=_metric(), latest=latest, history_rows=history)

    assert result is not None
    assert result.payload["method"] == "robust_residual_v0"
    assert result.payload["trend"]["method"] == "theil_sen_v0"
    assert result.payload["seasonality"]["status"] == "insufficient_seasonality"
    assert result.payload["residual"]["robust_z"] == 0
    assert result.probability is not None and result.probability < 0.5


def test_spike_above_linear_trend_produces_high_residual_z():
    history = [_row(month, 100 + month * 5) for month in range(1, 13)]
    latest = _row(13, 220)

    result = analyze_time_series(metric=_metric(), latest=latest, history_rows=history)

    assert result is not None
    assert result.payload["method"] == "robust_residual_v0"
    assert result.payload["residual"]["robust_z"] >= 10
    assert result.probability == 0.95


def test_monthly_seasonality_explains_expected_change_without_false_anomaly():
    history = []
    for month in range(1, 25):
        month_of_year = ((month - 1) % 12) + 1
        seasonal = 40 if month_of_year == 1 else 0
        history.append(_row(month, 100 + seasonal))
    latest = _row(25, 140)

    result = analyze_time_series(metric=_metric(), latest=latest, history_rows=history)

    assert result is not None
    assert result.payload["method"] == "seasonal_residual_mad_v0"
    assert result.payload["seasonality"]["status"] == "applied"
    assert result.payload["seasonality"]["component"] == 40
    assert result.payload["residual"]["robust_z"] == 0
    assert result.probability is not None and result.probability < 0.5


def test_spike_above_monthly_seasonality_produces_anomaly():
    history = []
    for month in range(1, 25):
        month_of_year = ((month - 1) % 12) + 1
        seasonal = 40 if month_of_year == 1 else 0
        history.append(_row(month, 100 + seasonal))
    latest = _row(25, 210)

    result = analyze_time_series(metric=_metric(), latest=latest, history_rows=history)

    assert result is not None
    assert result.payload["method"] == "seasonal_residual_mad_v0"
    assert result.payload["seasonality"]["status"] == "applied"
    assert result.payload["residual"]["robust_z"] >= 10
    assert result.probability == 0.95


def test_historical_outlier_does_not_break_residual_mad_baseline():
    history = [_row(month, 100) for month in range(1, 13)]
    history[5]["value"] = 1000
    latest = _row(13, 100)

    result = analyze_time_series(metric=_metric(), latest=latest, history_rows=history)

    assert result is not None
    assert result.payload["residual"]["robust_z"] == 0
    assert result.probability is not None and result.probability < 0.5


def test_mad_zero_uses_documented_floor_instead_of_infinite_score():
    history = [_row(month, 100) for month in range(1, 8)]
    latest = _row(8, 150)

    result = analyze_time_series(metric=_metric(), latest=latest, history_rows=history)

    assert result is not None
    assert result.payload["residual"]["robust_z"] == 50
    assert "MAD was zero" in result.payload["residual"]["basis"]


def test_insufficient_history_returns_null_probability_and_honest_basis():
    history = [_row(1, 100), _row(2, 110)]
    latest = _row(3, 130)

    result = analyze_time_series(metric=_metric(), latest=latest, history_rows=history)

    assert result is not None
    assert result.payload["method"] == "insufficient_history"
    assert result.payload["residual"]["robust_z"] is None
    assert result.probability is None
    assert "Only 2 temporal point" in result.payload["residual"]["basis"]


def test_missing_parseable_time_field_falls_back_to_robust_baseline():
    metric = _metric()
    rows = [
        {"mes": "period-a", "account_id": "a1", "account": "Account One", "value": 100},
        {"mes": "period-b", "account_id": "a1", "account": "Account One", "value": 105},
        {"mes": "period-c", "account_id": "a1", "account": "Account One", "value": 110},
        {"mes": "period-d", "account_id": "a1", "account": "Account One", "value": 180},
    ]

    artifacts, skipped = intelligence_engine.build_metric_artifacts(
        {"cartridge": "test", "domain": "Ops"},
        metric,
        rows,
    )

    assert skipped == []
    decision = artifacts[0]["decision_intelligence"]
    assert decision["method"] == "robust_baseline_v0"
    assert decision["time_series"] is None


def test_residual_z_can_trigger_signal_when_percent_deviation_is_small():
    metric = _metric()
    rows = [
        _row(1, 10_000),
        _row(2, 10_000),
        _row(3, 10_000),
        _row(4, 10_020),
    ]

    artifacts, skipped = intelligence_engine.build_metric_artifacts(
        {"cartridge": "test", "domain": "Ops"},
        metric,
        rows,
    )

    assert skipped == []
    assert artifacts
    signal = artifacts[0]["signal"]
    decision = artifacts[0]["decision_intelligence"]
    assert signal["deviation_pct"] < 0.01
    assert decision["time_series"]["residual"]["robust_z"] >= 20
    assert decision["method"] == "robust_residual_v0"


def test_future_artifacts_remain_reserved_state_space():
    metric = _metric()
    metric["prediction"] = {
        "enabled": True,
        "method": "trend_delta",
        "horizon_days": [7],
        "minimum_history": 3,
    }
    rows = [
        _row(1, 100),
        _row(2, 110),
        _row(3, 120),
        _row(4, 220),
    ]

    artifacts, skipped = intelligence_engine.build_metric_artifacts(
        {"cartridge": "test", "domain": "Ops"},
        metric,
        rows,
        horizon_days=[7],
    )

    assert skipped == []
    predictive = [
        item
        for item in artifacts
        if item["signal"]["signal_subtype"].startswith("future_")
    ]
    assert predictive
    assert (
        predictive[0]["decision_intelligence"]["method"]
        == "future_reserved_state_space"
    )
    assert predictive[0]["decision_intelligence"]["time_series"] is None
