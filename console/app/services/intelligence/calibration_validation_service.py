from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.services.intelligence import calibration
from app.services.intelligence.evidence_refs import normalize_evidence_refs


SOURCE_TYPES = {
    "monte_carlo_simulation",
    "decision_option",
    "prediction_outcome",
    "backtest_case",
    "manual_fixture",
}
FORBIDDEN_SCOPE_KEYS = {"tenant_id", "workspace_id", "security_context"}
DEFAULT_MODEL_VERSION = calibration.MODEL_VERSION


def _actor_id(user: dict | None) -> int | None:
    raw = (user or {}).get("id")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _synthetic_allowed() -> bool:
    app_env = os.environ.get("APP_ENV")
    return app_env is not None and app_env.strip().lower() in {
        "test",
        "local",
        "development",
    }


def _forbidden_path(value: Any, *, prefix: str = "") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            text_key = str(key)
            path = f"{prefix}.{text_key}" if prefix else text_key
            if text_key in FORBIDDEN_SCOPE_KEYS:
                return path
            nested = _forbidden_path(item, prefix=path)
            if nested:
                return nested
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            nested = _forbidden_path(item, prefix=f"{prefix}[{idx}]")
            if nested:
                return nested
    return None


def _short_text(
    value: Any, *, field: str, max_length: int, required: bool = True
) -> str:
    text = str(value or "").strip()
    if not text and required:
        raise HTTPException(422, f"{field} is required")
    if len(text) > max_length:
        raise HTTPException(422, f"{field} is too long")
    return text


def _validate_evidence_refs(value: Any) -> list[dict[str, str]]:
    return normalize_evidence_refs(value, max_items=20)


def _observed_at(value: Any) -> str:
    if value in (None, ""):
        return datetime.now(timezone.utc).isoformat()
    text = str(value).strip()
    if len(text) > 80:
        raise HTTPException(422, "observed_at is too long")
    parseable = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        datetime.fromisoformat(parseable)
    except ValueError as exc:
        raise HTTPException(422, "observed_at must be ISO-8601") from exc
    return text


def _calibration_group(source_type: str, value: Any) -> str:
    provided = str(value or "").strip()
    if provided:
        if len(provided) > 80:
            raise HTTPException(422, "calibration_group is too long")
        return provided
    return {
        "monte_carlo_simulation": "monte_carlo",
        "decision_option": "decision_option",
        "prediction_outcome": "prediction_outcome",
        "backtest_case": "backtest",
        "manual_fixture": "global",
    }[source_type]


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload or {})
    if "model_version" in clean:
        raise HTTPException(422, "model_version is server-owned")
    forbidden = _forbidden_path(clean)
    if forbidden:
        raise HTTPException(422, f"scope fields are not accepted: {forbidden}")
    source_type = _short_text(
        clean.get("source_type"), field="source_type", max_length=80
    )
    if source_type not in SOURCE_TYPES:
        raise HTTPException(422, "unsupported source_type")
    if source_type == "manual_fixture" and not _synthetic_allowed():
        raise HTTPException(403, "manual_fixture requires an explicit local APP_ENV")
    source_id = _short_text(clean.get("source_id"), field="source_id", max_length=256)
    model_version = DEFAULT_MODEL_VERSION
    observed_at = _observed_at(clean.get("observed_at"))
    horizon_days = clean.get("horizon_days", 30)
    try:
        horizon_days = int(horizon_days)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "horizon_days must be an integer") from exc
    if horizon_days < 1 or horizon_days > 3650:
        raise HTTPException(422, "horizon_days must be between 1 and 3650")
    engine_payload = {
        "actual_status": clean.get("actual_status"),
        "predicted_metric": clean.get("predicted_metric"),
        "predicted_probability": clean.get("predicted_probability"),
        "predicted_value": clean.get("predicted_value"),
        "predicted_interval": clean.get("predicted_interval") or {},
        "actual_value": clean.get("actual_value"),
        "calibration_group": _calibration_group(
            source_type, clean.get("calibration_group")
        ),
        "model_version": model_version,
    }
    try:
        calibration.normalize_observation(engine_payload)
    except calibration.CalibrationValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        **clean,
        **engine_payload,
        "source_type": source_type,
        "source_id": source_id,
        "model_version": model_version,
        "observed_at": observed_at,
        "horizon_days": horizon_days,
        "evidence_refs": _validate_evidence_refs(clean.get("evidence_refs")),
    }


__all__ = (
    "DEFAULT_MODEL_VERSION",
    "FORBIDDEN_SCOPE_KEYS",
    "SOURCE_TYPES",
    "_actor_id",
    "_forbidden_path",
    "_short_text",
    "_synthetic_allowed",
    "_validate_payload",
)
