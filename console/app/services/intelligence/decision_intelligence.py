from __future__ import annotations

from math import isfinite
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.intelligence import calibration
from app.services.intelligence.time_series import TimeSeriesResult
from app.services.intelligence.utils import num


DecisionMethod = Literal[
    "robust_baseline_v0",
    "robust_residual_v0",
    "seasonal_residual_mad_v0",
    "insufficient_history",
    "deterministic_guardrail",
    "dataset_unavailable",
    "future_reserved_bayesian",
    "future_reserved_conformal",
    "future_reserved_state_space",
]
DecisionOptionName = Literal["act_now", "investigate", "wait", "monitor"]
Recommendation = Literal[
    "act_now", "investigate", "wait", "monitor", "insufficient_data"
]
Level = Literal["low", "medium", "high", "unknown"]
QualityStatus = Literal["sufficient", "thin", "insufficient"]
TimeSeriesMethod = Literal[
    "robust_residual_v0",
    "seasonal_residual_mad_v0",
    "insufficient_history",
    "insufficient_seasonality",
]
TrendMethod = Literal["theil_sen_v0", "median_slope_v0", "none"]
SeasonalityMethod = Literal["period_median_v0", "none"]
SeasonalityStatus = Literal["applied", "insufficient_seasonality", "not_configured"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfidenceInterval(_StrictModel):
    lower: float | None = None
    upper: float | None = None
    unit: str | None = None


class MoneyEstimate(_StrictModel):
    value: float | None = None
    currency: str | None = None
    basis: str


class DelayEstimate(_StrictModel):
    value_per_day: float | None = None
    currency: str | None = None
    basis: str


class DownsideRisk(_StrictModel):
    value: float | None = None
    currency: str | None = None
    basis: str


class ValueOfInformation(_StrictModel):
    level: Level
    rationale: str


class DecisionOption(_StrictModel):
    option: DecisionOptionName
    expected_utility: float | None = None
    utility_basis: str
    risk: Level
    explanation: str


class DataQuality(_StrictModel):
    history_points: int = Field(ge=0)
    minimum_required: int = Field(ge=1)
    status: QualityStatus
    missing_fields: list[str] = Field(default_factory=list)


class TimeSeriesTrend(_StrictModel):
    method: TrendMethod
    current_value: float | None = None
    slope_per_period: float | None = None
    basis: str


class TimeSeriesSeasonality(_StrictModel):
    method: SeasonalityMethod
    period: str | None = None
    component: float | None = None
    status: SeasonalityStatus
    basis: str


class TimeSeriesResidual(_StrictModel):
    value: float | None = None
    median: float | None = None
    mad: float | None = None
    robust_z: float | None = None
    basis: str


class TimeSeriesAnalysis(_StrictModel):
    method: TimeSeriesMethod
    history_points: int = Field(ge=0)
    periods_observed: int = Field(ge=0)
    time_field: str
    value_field: str
    entity_key: str | None = None
    trend: TimeSeriesTrend
    seasonality: TimeSeriesSeasonality
    residual: TimeSeriesResidual


class CalibrationMetadata(_StrictModel):
    raw_probability: float = Field(ge=0, le=1)
    calibrated_probability: float = Field(ge=0, le=1)
    calibration_applied: bool
    calibration_reason: str
    calibration_group: str | None = None
    calibration_source: str
    sample_count: int = Field(ge=0)
    posterior_mean: float | None = None
    posterior_alpha: float | None = None
    posterior_beta: float | None = None
    confidence_score: float = Field(ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    max_adjustment: float = Field(ge=0, le=1)
    partial_pooling_applied: bool = False
    parent_calibration_group: str | None = None
    parent_sample_count: int = Field(default=0, ge=0)
    prior_source: str = "fixed"
    disclaimer: str


class DecisionIntelligence(_StrictModel):
    method: DecisionMethod
    anomaly_probability: float | None = Field(default=None, ge=0, le=1)
    probability_basis: str
    uncertainty_level: Level
    confidence_interval: ConfidenceInterval
    expected_impact: MoneyEstimate
    cost_of_delay: DelayEstimate
    downside_risk: DownsideRisk
    value_of_information: ValueOfInformation
    recommended_decision: Recommendation
    recommended_next_step: str
    rationale: str
    options: list[DecisionOption]
    data_quality: DataQuality
    calibration: CalibrationMetadata | None = None
    time_series: TimeSeriesAnalysis | None = None

    @field_validator("options")
    @classmethod
    def _all_options_present(cls, value: list[DecisionOption]) -> list[DecisionOption]:
        present = {item.option for item in value}
        required = {"act_now", "investigate", "wait", "monitor"}
        missing = required - present
        if missing:
            raise ValueError(f"decision options missing: {sorted(missing)}")
        return value


def build_decision_intelligence(
    *,
    metric: dict[str, Any],
    signal: dict[str, Any],
    baseline: dict[str, Any],
    latest: dict[str, Any],
    history_values: list[float],
    time_series_analysis: TimeSeriesResult | None = None,
    calibration_state: dict[str, Any] | None = None,
    calibration_group: str | None = None,
) -> dict[str, Any]:
    del baseline
    minimum_required = _minimum_required(metric)
    missing_fields = _missing_fields(metric, latest)
    actual = _safe_float(signal.get("actual_value"))
    expected = _safe_float(signal.get("expected_value"))
    deviation = _safe_float(signal.get("deviation_value"))
    currency = _impact_currency(metric)
    impact = _expected_impact(metric, deviation)

    if len(history_values) < minimum_required or missing_fields:
        return build_insufficient_history_decision_intelligence(
            metric=metric,
            history_points=len(history_values),
            actual_value=actual,
            expected_value=expected,
            deviation_value=deviation,
            missing_fields=missing_fields,
            time_series=(
                time_series_analysis.payload
                if time_series_analysis is not None
                else None
            ),
        )

    if (
        time_series_analysis is not None
        and time_series_analysis.probability is not None
        and time_series_analysis.payload.get("method")
        in {"robust_residual_v0", "seasonal_residual_mad_v0"}
    ):
        return _build_time_series_decision_intelligence(
            metric=metric,
            signal=signal,
            history_values=history_values,
            time_series_analysis=time_series_analysis,
            calibration_state=calibration_state,
            calibration_group=calibration_group,
        )

    center = _median(history_values)
    mad = _median([abs(value - center) for value in history_values])
    robust_sigma = 1.4826 * mad
    basis_notes = [
        f"median={_round(center)}",
        f"mad={_round(mad)}",
        f"history_points={len(history_values)}",
    ]
    if robust_sigma <= 0:
        robust_sigma = max(abs(center) * 0.05, 1.0)
        basis_notes.append("mad_zero_floor_applied")
    robust_z = abs((actual if actual is not None else center) - center) / robust_sigma
    raw_probability = _probability_from_robust_z(robust_z)
    calibration_payload = _calibration_for_probability(
        raw_probability,
        calibration_state=calibration_state,
        calibration_group=calibration_group,
    )
    probability = float(calibration_payload["calibrated_probability"])
    uncertainty = _uncertainty_level(len(history_values), mad, center, missing_fields)
    interval = ConfidenceInterval(
        lower=_round(center - robust_sigma),
        upper=_round(center + robust_sigma),
        unit=str(metric.get("value_field") or metric.get("id") or "metric_value"),
    )
    delay = _cost_of_delay(impact, currency)
    downside = _downside_risk(impact, currency, uncertainty)
    voi = _value_of_information(uncertainty, impact)
    recommended = _recommended_decision(probability, impact, uncertainty)
    next_step = _recommended_next_step(recommended)
    options = _options(
        probability=probability,
        expected_impact=impact,
        cost_of_delay=delay.value_per_day,
        uncertainty=uncertainty,
        voi=voi.level,
    )
    if calibration_payload["calibration_applied"]:
        basis_notes.append(
            f"bayesian_calibration_group={calibration_payload.get('calibration_group')}"
        )
        rationale = (
            "Decision Intelligence v0 used a robust median/MAD baseline over "
            f"{len(history_values)} historical points. Raw anomaly probability from "
            f"robust_z={_round(robust_z)} was calibrated with a Bayesian posterior."
        )
    else:
        basis_notes.append(str(calibration_payload["disclaimer"]))
        rationale = (
            "Decision Intelligence v0 used a robust median/MAD baseline over "
            f"{len(history_values)} historical points. Anomaly probability is a coarse rarity score "
            f"from robust_z={_round(robust_z)}, not a calibrated Bayesian posterior."
        )
    payload = DecisionIntelligence(
        method="robust_baseline_v0",
        anomaly_probability=probability,
        probability_basis="; ".join(basis_notes),
        uncertainty_level=uncertainty,
        confidence_interval=interval,
        expected_impact=MoneyEstimate(
            value=_round(impact),
            currency=currency,
            basis=_impact_basis(metric, deviation),
        ),
        cost_of_delay=delay,
        downside_risk=downside,
        value_of_information=voi,
        recommended_decision=recommended,
        recommended_next_step=next_step,
        rationale=rationale,
        options=options,
        data_quality=DataQuality(
            history_points=len(history_values),
            minimum_required=minimum_required,
            status="sufficient",
            missing_fields=[],
        ),
        calibration=CalibrationMetadata(**calibration_payload),
        time_series=None,
    )
    return payload.model_dump(mode="json")


def build_insufficient_history_decision_intelligence(
    *,
    metric: dict[str, Any],
    history_points: int,
    actual_value: float | None = None,
    expected_value: float | None = None,
    deviation_value: float | None = None,
    missing_fields: list[str] | None = None,
    time_series: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del actual_value, expected_value
    minimum_required = _minimum_required(metric)
    currency = _impact_currency(metric)
    impact = _expected_impact(metric, deviation_value)
    delay = _cost_of_delay(impact, currency)
    voi = _value_of_information("high", impact)
    recommendation: Recommendation = "investigate" if impact >= 500 else "monitor"
    payload = DecisionIntelligence(
        method="insufficient_history",
        anomaly_probability=None,
        probability_basis=(
            f"Only {history_points} historical point(s) available; minimum_required={minimum_required}. "
            "No anomaly probability was estimated."
        ),
        uncertainty_level="high",
        confidence_interval=ConfidenceInterval(
            lower=None,
            upper=None,
            unit=str(metric.get("value_field") or metric.get("id") or "metric_value"),
        ),
        expected_impact=MoneyEstimate(
            value=_round(impact) if impact > 0 else None,
            currency=currency if impact > 0 else None,
            basis=_impact_basis(metric, deviation_value),
        ),
        cost_of_delay=delay,
        downside_risk=_downside_risk(impact, currency, "high"),
        value_of_information=voi,
        recommended_decision=recommendation,
        recommended_next_step=_recommended_next_step(recommendation),
        rationale=(
            "Decision Intelligence v0 refused to estimate probability because history is thin. "
            "Investigate or monitor before executing."
        ),
        options=_options(
            probability=None,
            expected_impact=impact,
            cost_of_delay=delay.value_per_day,
            uncertainty="high",
            voi=voi.level,
        ),
        data_quality=DataQuality(
            history_points=max(0, int(history_points)),
            minimum_required=minimum_required,
            status="insufficient"
            if history_points < max(2, minimum_required - 1)
            else "thin",
            missing_fields=missing_fields or [],
        ),
        time_series=time_series,
    )
    return payload.model_dump(mode="json")


def build_future_reserved_decision_intelligence(
    *,
    metric: dict[str, Any],
    signal: dict[str, Any],
    history_points: int,
) -> dict[str, Any]:
    deviation = _safe_float(signal.get("deviation_value"))
    currency = _impact_currency(metric)
    impact = _expected_impact(metric, deviation)
    delay = _cost_of_delay(impact, currency)
    voi = _value_of_information("high", impact)
    recommendation: Recommendation = "investigate" if impact >= 500 else "monitor"
    payload = DecisionIntelligence(
        method="future_reserved_state_space",
        anomaly_probability=None,
        probability_basis=(
            "This is a predictive signal. Decision Intelligence v0 does not estimate "
            "forecast anomaly probability; state-space forecasting is reserved for a future method."
        ),
        uncertainty_level="high",
        confidence_interval=ConfidenceInterval(
            lower=None,
            upper=None,
            unit=str(metric.get("value_field") or metric.get("id") or "metric_value"),
        ),
        expected_impact=MoneyEstimate(
            value=_round(impact) if impact > 0 else None,
            currency=currency if impact > 0 else None,
            basis=_impact_basis(metric, deviation),
        ),
        cost_of_delay=delay,
        downside_risk=_downside_risk(impact, currency, "high"),
        value_of_information=voi,
        recommended_decision=recommendation,
        recommended_next_step=_recommended_next_step(recommendation),
        rationale=(
            "Forecast signal preserved, but v0 intentionally avoids calibrated probability "
            "for predicted future values."
        ),
        options=_options(
            probability=None,
            expected_impact=impact,
            cost_of_delay=delay.value_per_day,
            uncertainty="high",
            voi=voi.level,
        ),
        data_quality=DataQuality(
            history_points=max(0, int(history_points)),
            minimum_required=_minimum_required(metric),
            status="thin" if history_points >= 2 else "insufficient",
            missing_fields=[],
        ),
        time_series=None,
    )
    return payload.model_dump(mode="json")


def _build_time_series_decision_intelligence(
    *,
    metric: dict[str, Any],
    signal: dict[str, Any],
    history_values: list[float],
    time_series_analysis: TimeSeriesResult,
    calibration_state: dict[str, Any] | None = None,
    calibration_group: str | None = None,
) -> dict[str, Any]:
    expected = time_series_analysis.expected_value
    deviation = time_series_analysis.residual_gap
    raw_probability = time_series_analysis.probability
    calibration_payload = _calibration_for_probability(
        raw_probability,
        calibration_state=calibration_state,
        calibration_group=calibration_group,
    )
    probability = float(calibration_payload["calibrated_probability"])
    time_series = time_series_analysis.payload
    method = str(time_series.get("method"))
    robust_z = time_series_analysis.robust_z
    robust_sigma = time_series_analysis.robust_sigma
    currency = _impact_currency(metric)
    impact = _expected_impact(metric, deviation)
    residual = (
        time_series.get("residual")
        if isinstance(time_series.get("residual"), dict)
        else {}
    )
    residual_mad = _safe_float(residual.get("mad"))
    seasonality = (
        time_series.get("seasonality")
        if isinstance(time_series.get("seasonality"), dict)
        else {}
    )
    seasonality_status = str(seasonality.get("status") or "not_configured")
    missing_fields: list[str] = []
    uncertainty = _time_series_uncertainty(
        history_points=int(time_series.get("history_points") or len(history_values)),
        residual_mad=residual_mad,
        residual_sigma=robust_sigma,
        seasonality_status=seasonality_status,
        missing_fields=missing_fields,
    )
    if seasonality_status == "insufficient_seasonality" and uncertainty == "low":
        uncertainty = "medium"
    interval = ConfidenceInterval(
        lower=_round(expected - robust_sigma)
        if expected is not None and robust_sigma is not None
        else None,
        upper=_round(expected + robust_sigma)
        if expected is not None and robust_sigma is not None
        else None,
        unit=str(metric.get("value_field") or metric.get("id") or "metric_value"),
    )
    delay = _cost_of_delay(impact, currency)
    downside = _downside_risk(impact, currency, uncertainty)
    voi = _value_of_information(uncertainty, impact)
    recommended = _recommended_decision(probability, impact, uncertainty)
    next_step = _recommended_next_step(recommended)
    options = _options(
        probability=probability,
        expected_impact=impact,
        cost_of_delay=delay.value_per_day,
        uncertainty=uncertainty,
        voi=voi.level,
    )
    method_label = (
        "seasonal residual MAD"
        if method == "seasonal_residual_mad_v0"
        else "robust residual"
    )
    if calibration_payload["calibration_applied"]:
        rationale = (
            f"Decision Intelligence used {method_label} over "
            f"{time_series.get('history_points')} temporal point(s). It removes robust trend"
            + (
                " and month-of-year seasonality"
                if method == "seasonal_residual_mad_v0"
                else ""
            )
            + f" before scoring residual_z={_round(robust_z)}. "
            "Raw anomaly probability was calibrated with a Bayesian posterior."
        )
        probability_basis = (
            "Bayesian calibrated probability from residual robust_z raw score; "
            f"residual_z={_round(robust_z)}; "
            f"seasonality_status={seasonality_status}; "
            f"history_points={time_series.get('history_points')}; "
            f"bayesian_calibration_group={calibration_payload.get('calibration_group')}"
        )
    else:
        rationale = (
            f"Decision Intelligence used {method_label} over "
            f"{time_series.get('history_points')} temporal point(s). It removes robust trend"
            + (
                " and month-of-year seasonality"
                if method == "seasonal_residual_mad_v0"
                else ""
            )
            + f" before scoring residual_z={_round(robust_z)}. "
            "Anomaly probability is a coarse rarity score from residual robust_z, "
            "not a calibrated Bayesian posterior."
        )
        probability_basis = (
            "coarse rarity score from residual robust_z; not calibrated Bayesian posterior; "
            f"residual_z={_round(robust_z)}; "
            f"seasonality_status={seasonality_status}; "
            f"history_points={time_series.get('history_points')}; "
            f"{calibration_payload['disclaimer']}"
        )
    payload = DecisionIntelligence(
        method=method,  # type: ignore[arg-type]
        anomaly_probability=probability,
        probability_basis=probability_basis,
        uncertainty_level=uncertainty,
        confidence_interval=interval,
        expected_impact=MoneyEstimate(
            value=_round(impact) if impact > 0 else None,
            currency=currency if impact > 0 else None,
            basis=_impact_basis(metric, deviation),
        ),
        cost_of_delay=delay,
        downside_risk=downside,
        value_of_information=voi,
        recommended_decision=recommended,
        recommended_next_step=next_step,
        rationale=rationale,
        options=options,
        data_quality=DataQuality(
            history_points=int(
                time_series.get("history_points") or len(history_values)
            ),
            minimum_required=_minimum_required(metric),
            status="sufficient",
            missing_fields=[],
        ),
        calibration=CalibrationMetadata(**calibration_payload),
        time_series=time_series,
    )
    return payload.model_dump(mode="json")


def _calibration_for_probability(
    raw_probability: float | None,
    *,
    calibration_state: dict[str, Any] | None,
    calibration_group: str | None,
) -> dict[str, Any]:
    if raw_probability is None:
        raise calibration.CalibrationValidationError("raw_probability is required")
    return calibration.apply_calibration_to_probability(
        raw_probability,
        calibration_state,
        calibration_group=calibration_group,
    )


def _minimum_required(metric: dict[str, Any]) -> int:
    baseline = (
        metric.get("baseline") if isinstance(metric.get("baseline"), dict) else {}
    )
    configured = int(baseline.get("minimum_history") or 2)
    return max(3, configured)


def _missing_fields(metric: dict[str, Any], latest: dict[str, Any]) -> list[str]:
    entity = metric.get("entity") if isinstance(metric.get("entity"), dict) else {}
    fields = [
        str(metric.get("time_field") or ""),
        str(metric.get("value_field") or ""),
        str(entity.get("id_field") or ""),
    ]
    missing = []
    for field in fields:
        if (
            field
            and field != "__all__"
            and (field not in latest or latest.get(field) is None)
        ):
            missing.append(field)
    return sorted(set(missing))


def _safe_float(value: Any) -> float | None:
    parsed = num(value)
    return parsed if parsed is not None and isfinite(parsed) else None


def _median(values: list[float]) -> float:
    return float(median(values)) if values else 0.0


def _impact_currency(metric: dict[str, Any]) -> str | None:
    impact = metric.get("impact") if isinstance(metric.get("impact"), dict) else {}
    currency = str(impact.get("currency") or "").strip()
    return currency or None


def _expected_impact(metric: dict[str, Any], deviation_value: float | None) -> float:
    if deviation_value is None:
        return 0.0
    impact = metric.get("impact") if isinstance(metric.get("impact"), dict) else {}
    unit_value = _safe_float(impact.get("unit_value"))
    if unit_value is None:
        unit_value = 1.0 if _impact_currency(metric) else 0.0
    return max(0.0, abs(float(deviation_value)) * unit_value)


def _impact_basis(metric: dict[str, Any], deviation_value: float | None) -> str:
    impact = metric.get("impact") if isinstance(metric.get("impact"), dict) else {}
    unit_value = impact.get("unit_value")
    value_field = metric.get("value_field") or metric.get("id") or "metric_value"
    if deviation_value is None:
        return f"No deviation_value available for {value_field}."
    if unit_value is not None:
        return f"abs(deviation_value) * impact.unit_value ({unit_value}) for {value_field}."
    if _impact_currency(metric):
        return f"abs(deviation_value) used directly because {value_field} is monetary."
    return f"No monetary impact configured for {value_field}."


def _probability_from_robust_z(robust_z: float) -> float:
    if robust_z < 1.5:
        raw = 0.35 + robust_z * 0.10
    else:
        raw = 0.50 + min(0.45, (robust_z - 1.5) * 0.12)
    return round(max(0.05, min(0.95, raw)), 2)


def _uncertainty_level(
    history_points: int, mad: float, center: float, missing_fields: list[str]
) -> Level:
    if missing_fields:
        return "high"
    relative_mad = abs(mad / center) if center else (0.0 if mad == 0 else 1.0)
    if history_points >= 8 and relative_mad <= 0.15:
        return "low"
    if history_points >= 3 and relative_mad <= 0.45:
        return "medium"
    return "high"


def _time_series_uncertainty(
    *,
    history_points: int,
    residual_mad: float | None,
    residual_sigma: float | None,
    seasonality_status: str,
    missing_fields: list[str],
) -> Level:
    if missing_fields:
        return "high"
    if history_points < 5:
        return "high"
    sigma = abs(residual_sigma or 0.0)
    mad = abs(residual_mad or 0.0)
    if (
        seasonality_status == "applied"
        and history_points >= 18
        and sigma <= max(1.0, mad * 3.0)
    ):
        return "low"
    if history_points >= 8 and seasonality_status in {
        "applied",
        "insufficient_seasonality",
    }:
        return "medium"
    if history_points >= 5 and mad <= max(1.0, sigma):
        return "medium"
    return "high"


def _cost_of_delay(expected_impact: float, currency: str | None) -> DelayEstimate:
    if expected_impact <= 0 or not currency:
        return DelayEstimate(
            value_per_day=None,
            currency=None,
            basis="No monetary expected_impact available; cost of delay not estimated.",
        )
    value = max(1.0, expected_impact / 30.0)
    return DelayEstimate(
        value_per_day=_round(value),
        currency=currency,
        basis="expected_impact / 30-day operating month.",
    )


def _downside_risk(
    expected_impact: float, currency: str | None, uncertainty: Level
) -> DownsideRisk:
    if expected_impact <= 0 or not currency:
        return DownsideRisk(
            value=None,
            currency=None,
            basis="No monetary expected_impact available; downside not estimated.",
        )
    multiplier = {"low": 1.05, "medium": 1.25, "high": 1.50, "unknown": 1.50}[
        uncertainty
    ]
    return DownsideRisk(
        value=_round(expected_impact * multiplier),
        currency=currency,
        basis=f"expected_impact * {multiplier} uncertainty multiplier.",
    )


def _value_of_information(
    uncertainty: Level, expected_impact: float
) -> ValueOfInformation:
    if uncertainty == "high" and expected_impact >= 500:
        return ValueOfInformation(
            level="high",
            rationale="Uncertainty is high and potential impact is material; investigation can change the decision.",
        )
    if uncertainty in {"high", "medium"}:
        return ValueOfInformation(
            level="medium",
            rationale="Additional evidence may change timing or owner of the action.",
        )
    return ValueOfInformation(
        level="low",
        rationale="History is stable enough that more information is less likely to change the recommendation.",
    )


def _recommended_decision(
    probability: float, expected_impact: float, uncertainty: Level
) -> Recommendation:
    if uncertainty == "high":
        return "investigate" if expected_impact >= 500 else "monitor"
    if probability >= 0.75 and expected_impact >= 1_000 and uncertainty == "low":
        return "act_now"
    if probability >= 0.65 and expected_impact >= 500:
        return "investigate"
    if probability <= 0.45 or expected_impact < 250:
        return "monitor"
    return "wait"


def _recommended_next_step(recommendation: Recommendation) -> str:
    return {
        "act_now": "Prepare supervised action preview, run dry-run, then execute only after approval.",
        "investigate": "Review evidence pack, assign an owner, and gather one more operational confirmation before execution.",
        "wait": "Wait one refresh cycle and compare the next Gold value before acting.",
        "monitor": "Keep the item on the watchlist and avoid execution until evidence changes.",
        "insufficient_data": "Collect more history before deciding.",
    }[recommendation]


def _options(
    *,
    probability: float | None,
    expected_impact: float,
    cost_of_delay: float | None,
    uncertainty: Level,
    voi: Level,
) -> list[DecisionOption]:
    p = probability if probability is not None else 0.0
    delay = cost_of_delay or 0.0
    uncertainty_penalty = {"low": 0.05, "medium": 0.15, "high": 0.35, "unknown": 0.35}[
        uncertainty
    ] * expected_impact
    voi_value = {"low": 0.04, "medium": 0.10, "high": 0.18, "unknown": 0.10}[
        voi
    ] * expected_impact
    act_now = (p * expected_impact) - uncertainty_penalty
    investigate = voi_value - min(expected_impact * 0.04, 500.0)
    wait = -(delay * 7.0) - (p * expected_impact * 0.05)
    monitor = -(delay * 3.0) if probability is not None else None
    return [
        DecisionOption(
            option="act_now",
            expected_utility=_round(act_now)
            if expected_impact > 0 and probability is not None
            else None,
            utility_basis="anomaly_probability * expected_impact minus uncertainty penalty; not calibrated for execution ROI.",
            risk="high" if uncertainty in {"high", "unknown"} else "medium",
            explanation="Act only through supervised preview and dry-run when evidence is strong enough.",
        ),
        DecisionOption(
            option="investigate",
            expected_utility=_round(investigate) if expected_impact > 0 else None,
            utility_basis="value_of_information proxy minus bounded investigation cost.",
            risk="low" if uncertainty in {"high", "unknown"} else "medium",
            explanation="Collect one more operational confirmation before deciding whether to execute.",
        ),
        DecisionOption(
            option="wait",
            expected_utility=_round(wait)
            if expected_impact > 0 and probability is not None
            else None,
            utility_basis="negative seven-day delay cost plus residual anomaly exposure.",
            risk="medium" if probability is not None and probability >= 0.65 else "low",
            explanation="Wait one refresh cycle when the signal is weak or impact is modest.",
        ),
        DecisionOption(
            option="monitor",
            expected_utility=_round(monitor) if monitor is not None else None,
            utility_basis="short monitoring delay cost; null when probability was intentionally not estimated.",
            risk="low",
            explanation="Keep the item visible without moving to execution until the evidence changes.",
        ),
    ]


def _round(value: float | None) -> float | None:
    if value is None or not isfinite(value):
        return None
    return round(float(value), 2)
