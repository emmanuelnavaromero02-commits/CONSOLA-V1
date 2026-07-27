from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

from app.services.intelligence.business_labels import business_label


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
_HEADCOUNT_NAME_KEYS = (
    ("headcount_by_company", "company_name"),
    ("headcount_by_location", "location_name"),
    ("headcount_by_department", "department_name"),
)
GoldResultLoader = Callable[[str, dict | None, int], Awaitable[dict[str, Any]]]


def _strict_headcount(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _sf_gold_headcount_rows(
    rows: list[dict[str, Any]], label_keys: tuple[str, str]
) -> list[dict[str, Any]]:
    _, name_key = label_keys
    public_rows: list[dict[str, Any]] = []
    for row in rows:
        business_name = business_label(row.get(name_key))
        headcount = _strict_headcount(row.get("headcount"))
        if business_name is None or headcount is None:
            continue
        public_rows.append(
            {
                "label": business_name,
                name_key: business_name,
                "headcount": headcount,
            }
        )
    return sorted(
        public_rows,
        key=lambda item: (-item["headcount"], item["label"].casefold()),
    )


def _sf_gold_top_headcount_rows(
    rows: list[dict[str, Any]], label_keys: tuple[str, str], limit: int = 5
) -> list[dict[str, Any]]:
    return _sf_gold_headcount_rows(rows, label_keys)[:limit]


def _sf_gold_headcount_total(rows: list[dict[str, Any]] | None) -> int | None:
    if not rows:
        return None
    counts = [
        headcount
        for row in rows
        if (headcount := _strict_headcount(row.get("headcount"))) is not None
    ]
    return sum(counts) if counts else None


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
    status = str(source_status or "unavailable")
    if status not in {"ok", "ready"}:
        return status
    if public_rows:
        return "ready"
    return "invalid_schema" if raw_rows else "empty"


def _sf_gold_public_widget(raw_widget: Mapping[str, Any]) -> dict[str, Any]:
    identity = " ".join(
        str(raw_widget.get(key) or "").casefold() for key in ("id", "title")
    )
    name_key = next(
        (key for marker, key in _HEADCOUNT_NAME_KEYS if marker in identity),
        None,
    )
    if name_key is None:
        return dict(raw_widget)
    raw_rows_value = raw_widget.get("rows")
    raw_rows = (
        [dict(row) for row in raw_rows_value if isinstance(row, Mapping)]
        if isinstance(raw_rows_value, (list, tuple))
        else []
    )
    observed_rows = _sf_gold_headcount_rows(raw_rows, ("unused_id", name_key))
    source_status = raw_widget.get("status") or ("ready" if raw_rows else "empty")
    status = _sf_gold_headcount_widget_status(source_status, raw_rows, observed_rows)
    raw_value = raw_widget.get("value")
    observed_total = _sf_gold_headcount_total(observed_rows)
    value_is_valid = (
        type(raw_value) is int
        and raw_value >= 0
        and observed_total is not None
        and raw_value >= observed_total
    )
    if status == "ready" and not value_is_valid:
        status = "invalid_schema"
    return {
        **raw_widget,
        "value": raw_value if status == "ready" else None,
        "rows": observed_rows[:6] if status == "ready" else [],
        "status": status,
    }


def _sf_gold_status_error(results: list[dict[str, Any]]) -> str | None:
    errors = [str(result.get("error")) for result in results if result.get("error")]
    return "; ".join(errors) if errors else None


async def _sf_load_foundation_gold_results(
    datasets: dict[str, str],
    user: dict | None,
    employee_loader: GoldResultLoader,
) -> dict[str, dict[str, Any]]:
    employee = await employee_loader(datasets["employee_360"], user, 5000)
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
                "headcount_by_company",
                "headcount_by_location",
                "headcount_by_department",
            )
        }
    return {
        "employee_360": employee,
        **{
            key: headcounts[datasets[key]]
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


def _sf_foundation_active_headcount(company_headcount_total: int | None) -> int | None:
    return company_headcount_total


__all__ = (
    "_sf_foundation_active_headcount",
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
