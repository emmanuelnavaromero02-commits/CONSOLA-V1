from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException


_LOCK_METADATA_SQL = """
SELECT metadata
  FROM control_room_items
 WHERE workspace_id = $1
   AND item_id = $2
   AND owner_user_id IS NOT DISTINCT FROM $3
 FOR UPDATE
"""


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


async def locked_operational_metadata(
    conn: Any,
    *,
    workspace_id: str,
    item_id: str,
    owner_user_id: int | None,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        _LOCK_METADATA_SQL,
        workspace_id,
        item_id,
        owner_user_id,
    )
    if not row:
        raise HTTPException(404, "control room item not found")
    return _mapping(row.get("metadata"))


def merged_control_state(
    metadata: Mapping[str, Any],
    *,
    control_id: str,
    baseline: Mapping[str, Any],
    changes: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    current = _mapping(metadata.get("control_state"))
    authoritative_control = _mapping(current.get(control_id))
    current[control_id] = {
        **dict(baseline),
        **authoritative_control,
        **dict(changes),
    }
    return current


def merged_alert_state(
    metadata: Mapping[str, Any],
    *,
    changes: Mapping[str, Any],
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current = _mapping(metadata.get("alert_state"))
    for key, value in (defaults or {}).items():
        current.setdefault(key, value)
    current.update(dict(changes))
    return current


def merged_lesson_state(
    metadata: Mapping[str, Any],
    *,
    application: Mapping[str, Any],
    rule: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    lesson_id = int(application.get("lesson_id") or 0)
    raw_applications = metadata.get("lesson_applications")
    applications = (
        [
            dict(value)
            for value in raw_applications
            if isinstance(value, Mapping)
            and int(value.get("lesson_id") or 0) != lesson_id
        ]
        if isinstance(raw_applications, list)
        else []
    )
    applications.insert(0, dict(application))

    rules = merged_learned_rules(metadata, rule=rule)
    return rules, applications[:20]


def merged_learned_rules(metadata: Mapping[str, Any], *, rule: str) -> list[str]:
    raw_rules = metadata.get("learned_rules")
    rules = []
    if isinstance(raw_rules, list):
        for value in raw_rules:
            text = str(value or "").strip()
            if text and text not in rules:
                rules.append(text)
    if rule and rule not in rules:
        rules.insert(0, rule)
    return rules[:10]


__all__ = (
    "locked_operational_metadata",
    "merged_alert_state",
    "merged_control_state",
    "merged_learned_rules",
    "merged_lesson_state",
)
