from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.config_loader import SeriesConfig
from app.services.date_windows import inegi_date


def metadata_rows(payload: dict[str, Any], *, retrieved_at: str, provenance: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("inegi", {}).get("metadata", []):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "series_id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "unit_code": str(item.get("unit_code") or ""),
                "frequency_code": str(item.get("frequency_code") or ""),
                "last_update": str(item.get("last_update") or ""),
                "source": str(item.get("source") or ""),
                **provenance,
                "_retrieved_at": retrieved_at,
            }
        )
    return rows


def observation_rows(
    payload: dict[str, Any],
    *,
    configs: dict[str, SeriesConfig],
    retrieved_at: str,
    provenance: dict[str, str],
) -> list[dict[str, Any]]:
    rows = []
    from_date = payload.get("inegi", {}).get("from_date")
    to_date = payload.get("inegi", {}).get("to_date")
    for series in payload.get("inegi", {}).get("series", []):
        if not isinstance(series, dict):
            continue
        series_id = str(series.get("INDICADOR") or "")
        config = configs.get(series_id)
        for datum in series.get("OBSERVATIONS") or []:
            if not isinstance(datum, dict):
                continue
            obs_date = inegi_date(str(datum.get("TIME_PERIOD") or ""))
            if from_date and obs_date < str(from_date):
                continue
            if to_date and obs_date > str(to_date):
                continue
            rows.append(
                {
                    "series_id": series_id,
                    "metric_name": config.metric_name if config else None,
                    "observation_date": obs_date,
                    "period_key": str(datum.get("TIME_PERIOD") or ""),
                    "value_raw": str(datum.get("OBS_VALUE") or ""),
                    "obs_status": str(datum.get("OBS_STATUS") or ""),
                    "obs_exception": str(datum.get("OBS_EXCEPTION") or ""),
                    "unit": config.unit if config else None,
                    "frequency": config.frequency if config else None,
                    "country_code": "MX",
                    **provenance,
                    "_retrieved_at": retrieved_at,
                }
            )
    return rows


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
