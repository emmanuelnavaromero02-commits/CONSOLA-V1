from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.successfactors_gold_public_factory import (
    project_public_gold_widget,
    valid_gold_widget_id,
)


_NON_READY_STATUSES = frozenset(
    {
        "blocked",
        "empty",
        "error",
        "invalid_schema",
        "missing",
        "no_permission",
        "partial",
        "unavailable",
    }
)


def _strict_headcount(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _sf_gold_headcount_total(rows: list[dict[str, Any]] | None) -> int | None:
    if not rows:
        return None
    counts = [
        headcount
        for row in rows
        if (headcount := _strict_headcount(row.get("headcount"))) is not None
    ]
    return sum(counts) if counts else None


def project_generic_gold_widget(
    raw_widget: Mapping[str, Any],
    _raw_rows: list[dict[str, Any]],
    _identity: str,
    *,
    is_active_headcount: bool,
    rows_shape_valid: bool,
) -> dict[str, Any]:

    del is_active_headcount, rows_shape_valid
    return project_public_gold_widget(raw_widget)


__all__ = (
    "_NON_READY_STATUSES",
    "_sf_gold_headcount_total",
    "_strict_headcount",
    "project_generic_gold_widget",
    "valid_gold_widget_id",
)
