from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services.intelligence.gold_fetcher import query_gold_dataset_rows


DATASET = "banxico_market_context"


async def banxico_readiness(user: dict | None) -> dict[str, Any]:
    try:
        rows = await query_gold_dataset_rows(DATASET, user, limit=100)
    except HTTPException as exc:
        if exc.status_code == 404:
            return _empty("insufficient_data", "banxico_market_context_not_materialized")
        raise

    series = [_series_row(row) for row in rows]
    usable = [row for row in series if row["usable"]]
    stale = [row for row in series if row["status"] == "stale"]
    if not series:
        status = "insufficient_data"
    elif usable:
        status = "ready"
    elif stale:
        status = "stale"
    else:
        status = "partial"

    return {
        "cartridge_id": "banxico",
        "dataset": DATASET,
        "status": status,
        "series_count": len(series),
        "usable_count": len(usable),
        "series": series,
    }


def _series_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "series_id": str(row.get("series_id") or ""),
        "metric_name": str(row.get("metric_name") or ""),
        "as_of": _text(row.get("as_of")),
        "unit": str(row.get("unit") or ""),
        "value": _number(row.get("value")),
        "confidence": _number(row.get("confidence")),
        "status": str(row.get("freshness_status") or row.get("status") or ""),
        "usable": bool(row.get("usable")),
        "source_authority": str(row.get("source_authority") or ""),
    }


def _number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _empty(status: str, reason: str) -> dict[str, Any]:
    return {
        "cartridge_id": "banxico",
        "dataset": DATASET,
        "status": status,
        "reason": reason,
        "series_count": 0,
        "usable_count": 0,
        "series": [],
    }
