from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from statistics import median
from typing import Any

from app.services.intelligence.utils import num, period_key


MAD_NORMAL_FACTOR = 1.4826
MAX_THEIL_SEN_POINTS = 60
MIN_TEMPORAL_HISTORY = 3
MIN_SEASONAL_HISTORY = 12
SEASONAL_PERIOD_MONTHS = 12


@dataclass(frozen=True)
class SeriesPoint:
    index: int
    month: int
    value: float
    label: str


@dataclass(frozen=True)
class TimeSeriesResult:
    payload: dict[str, Any]
    probability: float | None
    robust_z: float | None
    robust_sigma: float | None
    residual_gap: float | None
    expected_value: float | None


def analyze_time_series(
    *,
    metric: dict[str, Any],
    latest: dict[str, Any],
    history_rows: list[dict[str, Any]],
) -> TimeSeriesResult | None:

    time_field = str(metric.get("time_field") or "").strip()
    value_field = str(metric.get("value_field") or "").strip()
    if not time_field or not value_field:
        return None

    current = _point(latest, time_field, value_field)
    if current is None:
        return None

    points = [_point(row, time_field, value_field) for row in history_rows]
    history = sorted(
        (point for point in points if point is not None), key=lambda item: item.index
    )
    history_points = len(history)
    periods_observed = len({point.index for point in [*history, current]})
    minimum_required = _minimum_required(metric)
    entity_key = _entity_key(metric, latest)

    if history_points < max(MIN_TEMPORAL_HISTORY, minimum_required):
        payload = _empty_payload(
            metric=metric,
            method="insufficient_history",
            history_points=history_points,
            periods_observed=periods_observed,
            entity_key=entity_key,
            basis=(
                f"Only {history_points} temporal point(s) parsed; "
                f"minimum_required={max(MIN_TEMPORAL_HISTORY, minimum_required)}."
            ),
        )
        return TimeSeriesResult(
            payload=payload,
            probability=None,
            robust_z=None,
            robust_sigma=None,
            residual_gap=None,
            expected_value=None,
        )

    base_index = min(point.index for point in [*history, current])
    slope = _theil_sen_slope(history, base_index)
    intercept = _median(
        [point.value - slope * (point.index - base_index) for point in history]
    )
    current_trend = intercept + slope * (current.index - base_index)
    detrended = [
        (point, point.value - (intercept + slope * (point.index - base_index)))
        for point in history
    ]

    seasonal_status = "insufficient_seasonality"
    seasonal_method = "none"
    seasonal_period: str | None = "month_of_year"
    seasonal_basis = (
        "Monthly seasonality requires at least two historical observations for "
        "the current month and at least twelve temporal points."
    )
    seasonal_components: dict[int, float] = {}
    current_seasonal_component: float | None = None
    by_month: dict[int, list[float]] = {}
    for point, residual in detrended:
        by_month.setdefault(point.month, []).append(residual)
    current_month_residuals = by_month.get(current.month, [])
    if (
        history_points >= MIN_SEASONAL_HISTORY
        and len(current_month_residuals) >= 2
        and periods_observed >= SEASONAL_PERIOD_MONTHS
    ):
        seasonal_components = {
            month: _median(values)
            for month, values in by_month.items()
            if len(values) >= 2
        }
        current_seasonal_component = seasonal_components.get(current.month, 0.0)
        seasonal_method = "period_median_v0"
        seasonal_status = "applied"
        seasonal_basis = (
            "Applied month-of-year median residuals with at least two "
            "historical observations for the current month."
        )
    adjusted_residuals = [
        residual
        - (
            seasonal_components.get(point.month, 0.0)
            if seasonal_status == "applied"
            else 0.0
        )
        for point, residual in detrended
    ]
    residual_median = _median(adjusted_residuals)
    residual_mad = _median(
        [abs(value - residual_median) for value in adjusted_residuals]
    )
    robust_sigma, floor_basis = _robust_sigma(
        residual_mad, adjusted_residuals, residual_median
    )
    seasonal_for_current = (
        current_seasonal_component if current_seasonal_component is not None else 0.0
    )
    current_residual = current.value - current_trend - seasonal_for_current
    residual_gap = current_residual - residual_median
    robust_z = abs(residual_gap) / robust_sigma
    probability = _probability_from_robust_z(robust_z)
    residual_basis = (
        "Residual MAD over historical values after robust trend"
        + (" and month-of-year seasonality" if seasonal_status == "applied" else "")
        + f"; {floor_basis}"
    )
    method = (
        "seasonal_residual_mad_v0"
        if seasonal_status == "applied"
        else "robust_residual_v0"
    )
    expected_value = current_trend + seasonal_for_current + residual_median
    payload = {
        "method": method,
        "history_points": history_points,
        "periods_observed": periods_observed,
        "time_field": time_field,
        "value_field": value_field,
        "entity_key": entity_key,
        "trend": {
            "method": "theil_sen_v0",
            "current_value": _round(current_trend),
            "slope_per_period": _round(slope),
            "basis": (
                f"Simplified Theil-Sen median slope over {history_points} historical "
                "point(s) using monthly period indexes."
            ),
        },
        "seasonality": {
            "method": seasonal_method,
            "period": seasonal_period,
            "component": _round(current_seasonal_component),
            "status": seasonal_status,
            "basis": seasonal_basis,
        },
        "residual": {
            "value": _round(current_residual),
            "median": _round(residual_median),
            "mad": _round(residual_mad),
            "robust_z": _round(robust_z),
            "basis": residual_basis,
        },
    }
    return TimeSeriesResult(
        payload=payload,
        probability=probability,
        robust_z=robust_z,
        robust_sigma=robust_sigma,
        residual_gap=residual_gap,
        expected_value=expected_value,
    )


def insufficient_time_series_payload(
    *,
    metric: dict[str, Any],
    history_points: int,
    basis: str,
) -> dict[str, Any]:
    return _empty_payload(
        metric=metric,
        method="insufficient_history",
        history_points=max(0, int(history_points)),
        periods_observed=max(0, int(history_points)),
        entity_key=None,
        basis=basis,
    )


def _point(
    row: dict[str, Any], time_field: str, value_field: str
) -> SeriesPoint | None:
    value = num(row.get(value_field))
    parsed = _parse_month_index(row.get(time_field))
    if value is None or parsed is None:
        return None
    index, month = parsed
    return SeriesPoint(
        index=index,
        month=month,
        value=value,
        label=period_key(row, time_field),
    )


def _parse_month_index(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text).date()
        except ValueError:
            try:
                parsed = date.fromisoformat(text[:10])
            except ValueError:
                return None
    return parsed.year * 12 + parsed.month, parsed.month


def _theil_sen_slope(points: list[SeriesPoint], base_index: int) -> float:
    recent = points[-MAX_THEIL_SEN_POINTS:]
    slopes: list[float] = []
    normalized = [(point.index - base_index, point.value) for point in recent]
    for left_idx, (left_time, left_value) in enumerate(normalized):
        for right_time, right_value in normalized[left_idx + 1 :]:
            delta_t = right_time - left_time
            if delta_t:
                slopes.append((right_value - left_value) / delta_t)
    return _median(slopes)


def _robust_sigma(
    mad: float, residuals: list[float], residual_median: float
) -> tuple[float, str]:
    sigma = MAD_NORMAL_FACTOR * mad
    if sigma > 0:
        return sigma, "robust_sigma=1.4826*MAD"
    absolute_residuals = [abs(value) for value in residuals]
    floor = max(abs(residual_median) * 0.05, _median(absolute_residuals) * 0.10, 1.0)
    return (
        floor,
        "MAD was zero; sigma floor=max(5pct residual median, 10pct median abs residual, 1.0)",
    )


def _empty_payload(
    *,
    metric: dict[str, Any],
    method: str,
    history_points: int,
    periods_observed: int,
    entity_key: str | None,
    basis: str,
) -> dict[str, Any]:
    return {
        "method": method,
        "history_points": history_points,
        "periods_observed": periods_observed,
        "time_field": str(metric.get("time_field") or ""),
        "value_field": str(
            metric.get("value_field") or metric.get("id") or "metric_value"
        ),
        "entity_key": entity_key,
        "trend": {
            "method": "none",
            "current_value": None,
            "slope_per_period": None,
            "basis": basis,
        },
        "seasonality": {
            "method": "none",
            "period": None,
            "component": None,
            "status": "not_configured",
            "basis": basis,
        },
        "residual": {
            "value": None,
            "median": None,
            "mad": None,
            "robust_z": None,
            "basis": basis,
        },
    }


def _minimum_required(metric: dict[str, Any]) -> int:
    baseline = (
        metric.get("baseline") if isinstance(metric.get("baseline"), dict) else {}
    )
    try:
        configured = int(baseline.get("minimum_history") or 2)
    except (TypeError, ValueError):
        configured = 2
    return max(MIN_TEMPORAL_HISTORY, configured)


def _entity_key(metric: dict[str, Any], row: dict[str, Any]) -> str | None:
    entity = metric.get("entity") if isinstance(metric.get("entity"), dict) else {}
    id_field = str(entity.get("id_field") or "").strip()
    if not id_field or id_field == "__all__":
        return None if not id_field else "__all__"
    value = row.get(id_field)
    return str(value) if value is not None and str(value).strip() else None


def _probability_from_robust_z(robust_z: float) -> float:
    if robust_z < 1.5:
        raw = 0.35 + robust_z * 0.10
    else:
        raw = 0.50 + min(0.45, (robust_z - 1.5) * 0.12)
    return round(max(0.05, min(0.95, raw)), 2)


def _median(values: list[float]) -> float:
    return float(median(values)) if values else 0.0


def _round(value: float | None) -> float | None:
    if value is None or not isfinite(value):
        return None
    return round(float(value), 2)
