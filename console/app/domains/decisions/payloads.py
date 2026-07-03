"""Pure payload helpers for decision routes."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any


def coerce_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def coerce_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def decision_row_to_dict(row: Any) -> dict:
    item = dict(row)
    for key in ("created_at", "closed_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
    if item.get("commitment_date"):
        item["commitment_date"] = item["commitment_date"].isoformat()
    if isinstance(item.get("kpis"), str):
        try:
            item["kpis"] = json.loads(item["kpis"])
        except Exception:
            item["kpis"] = []
    return item

