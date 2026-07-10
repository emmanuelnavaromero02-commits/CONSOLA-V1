from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.config_loader import SeriesConfig
from app.services.date_windows import banxico_date


def metadata_rows(payload: dict[str, Any], *, retrieved_at: str, provenance: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("bmx", {}).get("series", []):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "series_id": str(item.get("idSerie") or ""),
                "title": str(item.get("titulo") or ""),
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
    for series in payload.get("bmx", {}).get("series", []):
        if not isinstance(series, dict):
            continue
        series_id = str(series.get("idSerie") or "")
        config = configs.get(series_id)
        for datum in series.get("datos") or []:
            if not isinstance(datum, dict):
                continue
            rows.append(
                {
                    "series_id": series_id,
                    "metric_name": config.metric_name if config else None,
                    "observation_date": banxico_date(str(datum.get("fecha"))),
                    "value_raw": str(datum.get("dato") or ""),
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
