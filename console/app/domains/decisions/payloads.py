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


DECISION_UPDATE_FIELDS = {
    "title",
    "description",
    "commitment_date",
    "kpis",
    "status",
    "outcome",
    "closed_at",
    "follow_up_decision_id",
    "assignee_id",
    "visibility",
}


def decision_update_assignments(body: dict[str, Any]) -> tuple[list[str], list[Any]]:
    sets: list[str] = []
    params: list[Any] = []
    for key, value in body.items():
        if key not in DECISION_UPDATE_FIELDS:
            continue
        if key == "kpis":
            params.append(json.dumps(value))
            sets.append(f"{key} = ${len(params)}::jsonb")
            continue
        if key == "commitment_date":
            value = coerce_date(value)
        elif key == "closed_at":
            value = coerce_datetime(value)
        elif key == "visibility" and value not in ("private", "shared"):
            continue
        params.append(value)
        sets.append(f"{key} = ${len(params)}")
    if body.get("status") == "closed" and "closed_at" not in body:
        sets.append("closed_at = COALESCE(closed_at, NOW())")
    return sets, params


def decision_update_sql_and_params(
    *,
    sets: list[str],
    params: list[Any],
    decision_id: int,
    workspace_id: str,
) -> tuple[str, list[Any]]:
    update_params = list(params)
    update_params.append(decision_id)
    decision_ref = f"${len(update_params)}"
    update_params.append(workspace_id)
    workspace_ref = f"${len(update_params)}"
    sql = (
        f"UPDATE decisions SET {', '.join(sets)} "
        f"WHERE id = {decision_ref} AND workspace_id = {workspace_ref} RETURNING *"
    )
    return sql, update_params
