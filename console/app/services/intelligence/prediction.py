from __future__ import annotations

from typing import Any

from app.services.intelligence.utils import confidence, prediction_confidence, severity, signal_type, stable_id


def projected_value(actual: float, history_values: list[float], horizon_days: int) -> tuple[float, str]:
    if not history_values:
        return actual, "latest_value"
    recent = history_values[-1]
    trend = actual - recent
    # Keep v1 deliberately simple and explainable: one observed trend step scaled
    # softly by the horizon so 21 days does not explode small samples.
    scale = min(2.0, max(0.5, horizon_days / 14))
    return round(actual + trend * scale, 4), "trend_delta"


def build_prediction_signal(
    *,
    base_signal: dict[str, Any],
    history_values: list[float],
    horizon_days: int,
    warning_pct: float,
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    expected = float(base_signal["expected_value"])
    actual = float(base_signal["actual_value"])
    predicted, method = projected_value(actual, history_values, horizon_days)
    deviation = predicted - expected
    deviation_pct = 0.0 if expected == 0 and predicted == 0 else (1.0 if expected == 0 else deviation / abs(expected))
    abs_pct = abs(deviation_pct)
    if abs_pct < warning_pct:
        return None
    subtype = "future_opportunity" if signal_type(base_signal["expected_behavior"], predicted, expected) == "opportunity" else "future_risk"
    signal = {**base_signal}
    signal.update(
        {
            "signal_id": stable_id(
                {
                    "cartridge": base_signal["cartridge_id"],
                    "dataset": base_signal["dataset"],
                    "metric": base_signal["metric"],
                    "entity": base_signal["entity_id"],
                    "period": base_signal["period_key"],
                    "horizon": horizon_days,
                }
            ),
            "actual_value": round(actual, 4),
            "predicted_value": predicted,
            "prediction_horizon_days": horizon_days,
            "prediction_method": method,
            "signal_subtype": subtype,
            "deviation_value": round(deviation, 4),
            "deviation_pct": round(deviation_pct, 4),
            "severity": severity(abs_pct, rules),
            "signal_type": signal_type(base_signal["expected_behavior"], predicted, expected),
            "confidence": prediction_confidence(confidence(len(history_values), abs_pct), len(history_values)),
            "summary": (
                f"Prediccion {horizon_days}d: {base_signal['metric_name']} para "
                f"{base_signal['entity_label']} se proyecta {predicted:.2f} vs esperado "
                f"{expected:.2f} ({deviation_pct:+.1%})."
            ),
        }
    )
    return signal
