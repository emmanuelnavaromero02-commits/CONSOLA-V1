from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.successfactors_gold_public_rows import (
    _NON_READY_STATUSES,
    _sf_gold_headcount_total,
    _strict_headcount,
)
from app.services.control_room.successfactors_gold_public_factory import (
    project_dimension_rows,
    project_public_gold_widget,
)


def _sf_gold_headcount_rows(
    rows: list[dict[str, Any]], label_keys: tuple[str, str]
) -> list[dict[str, Any]]:
    _, name_key = label_keys
    public_rows, _rejected = project_dimension_rows(rows, name_key)
    return public_rows


def _sf_gold_top_headcount_rows(
    rows: list[dict[str, Any]], label_keys: tuple[str, str], limit: int = 5
) -> list[dict[str, Any]]:
    return _sf_gold_headcount_rows(rows, label_keys)[:limit]


def _sf_gold_summary_total(
    result: Mapping[str, Any], rows: list[dict[str, Any]]
) -> int | None:
    total = _strict_headcount(result.get("total"))
    visible_total = _sf_gold_headcount_total(rows)
    if total is None or visible_total is None or total < visible_total:
        return None
    return total


def _sf_gold_headcount_widget_status(
    source_status: object,
    raw_rows: list[dict[str, Any]] | None,
    public_rows: list[dict[str, Any]],
) -> str:
    if source_status is None:
        status = "unavailable"
    elif isinstance(source_status, str):
        if not source_status:
            return "invalid_schema"
        status = source_status
    else:
        return "invalid_schema"
    if status in _NON_READY_STATUSES:
        return status
    if status not in {"ok", "ready"}:
        return "invalid_schema"
    if public_rows:
        return "ready"
    return "invalid_schema" if raw_rows else "empty"


def _sf_gold_public_widget(raw_widget: Mapping[str, Any]) -> dict[str, Any]:
    return project_public_gold_widget(raw_widget)


def _sf_gold_status_error(results: list[dict[str, Any]]) -> str | None:
    errors = [str(result.get("error")) for result in results if result.get("error")]
    return "; ".join(errors) if errors else None


async def _sf_load_foundation_gold_results(
    datasets: dict[str, str],
    user: dict | None,
) -> dict[str, dict[str, Any]]:
    try:
        from app.services.intelligence.successfactors_gold_headcount import (
            query_successfactors_headcount_summaries,
        )

        headcounts = await query_successfactors_headcount_summaries(user, limit=5)
    except HTTPException as exc:
        status = {403: "no_permission", 404: "missing"}.get(
            exc.status_code, "unavailable"
        )
        failure = {
            "rows": [],
            "total": None,
            "status": status,
            "error": str(exc.detail or "headcount aggregate unavailable"),
        }
        headcounts = {
            datasets[key]: dict(failure)
            for key in (
                "employee_360",
                "headcount_by_company",
                "headcount_by_location",
                "headcount_by_department",
            )
        }
    except Exception as exc:
        headcounts = {
            datasets[key]: {
                "rows": [],
                "total": None,
                "status": "unavailable",
                "error": str(exc),
            }
            for key in (
                "employee_360",
                "headcount_by_company",
                "headcount_by_location",
                "headcount_by_department",
            )
        }
    invalid_result = {
        "rows": [],
        "total": None,
        "status": "unavailable",
        "error": "headcount aggregate unavailable",
    }
    return {
        "active_headcount": dict(
            headcounts.get(datasets["employee_360"], invalid_result)
        ),
        **{
            key: dict(headcounts.get(datasets[key], invalid_result))
            for key in (
                "headcount_by_company",
                "headcount_by_location",
                "headcount_by_department",
            )
        },
    }


def _sf_gold_usable_rows(result: dict[str, Any]) -> list[dict[str, Any]] | None:
    status = str(result.get("status") or "")
    if status in {"ready", "empty"}:
        return result.get("rows") if isinstance(result.get("rows"), list) else []
    return None


def _sf_gold_combine_widget_status(results: list[dict[str, Any]]) -> str:
    statuses = {str(result.get("status") or "unavailable") for result in results}
    if "ready" in statuses:
        return "partial" if statuses & _NON_READY_STATUSES else "ready"
    for status in (
        "no_permission",
        "blocked",
        "unavailable",
        "missing",
        "invalid_schema",
        "error",
        "partial",
        "empty",
    ):
        if status in statuses:
            return status
    return "unavailable"


__all__ = (
    "_sf_gold_combine_widget_status",
    "_sf_gold_headcount_rows",
    "_sf_gold_headcount_total",
    "_sf_gold_headcount_widget_status",
    "_sf_gold_public_widget",
    "_sf_gold_summary_total",
    "_sf_gold_status_error",
    "_sf_gold_top_headcount_rows",
    "_sf_gold_usable_rows",
    "_sf_load_foundation_gold_results",
)
