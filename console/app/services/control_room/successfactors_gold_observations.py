from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.successfactors_gold_public_rows import (
    _NON_READY_STATUSES,
    _sf_gold_headcount_total,
    _strict_headcount,
    project_generic_gold_widget,
    valid_gold_widget_id,
)
from app.services.public_text_sensitivity import public_business_label


_HEADCOUNT_NAME_KEYS = (
    ("headcount_by_company", "company_name"),
    ("headcount_by_location", "location_name"),
    ("headcount_by_department", "department_name"),
)
_HEADCOUNT_ID_NAME_KEYS = {
    f"sf_{marker}": name_key for marker, name_key in _HEADCOUNT_NAME_KEYS
}


def _sf_gold_headcount_rows(
    rows: list[dict[str, Any]], label_keys: tuple[str, str]
) -> list[dict[str, Any]]:
    _, name_key = label_keys
    public_rows: list[dict[str, Any]] = []
    for row in rows:
        unexpected_label_keys = {
            key
            for key in ("company_name", "department_name", "fact", "location_name")
            if key != name_key and key in row
        }
        business_name = public_business_label(row.get(name_key))
        supplied_label = (
            public_business_label(row.get("label")) if "label" in row else business_name
        )
        headcount = _strict_headcount(row.get("headcount"))
        if (
            unexpected_label_keys
            or business_name is None
            or supplied_label is None
            or supplied_label.casefold() != business_name.casefold()
            or headcount is None
        ):
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
    raw_id = raw_widget.get("id")
    if not valid_gold_widget_id(raw_id):
        return {
            **raw_widget,
            "id": None,
            "value": None,
            "rows": [],
            "status": "invalid_schema",
        }
    is_active_headcount = raw_id == "sf_active_headcount"
    identity = " ".join(
        value.casefold()
        for key in ("id", "title")
        if isinstance((value := raw_widget.get(key)), str)
    )
    raw_title = raw_widget.get("title")
    title_is_valid = raw_title is None or public_business_label(raw_title) is not None
    name_key = (
        None
        if is_active_headcount or not isinstance(raw_id, str)
        else _HEADCOUNT_ID_NAME_KEYS.get(raw_id)
    )
    if name_key is None and not is_active_headcount:
        name_key = next(
            (key for marker, key in _HEADCOUNT_NAME_KEYS if marker in identity),
            None,
        )
    raw_rows_value = raw_widget.get("rows")
    rows_shape_valid = isinstance(raw_rows_value, (list, tuple)) and all(
        isinstance(row, Mapping) for row in raw_rows_value
    )
    raw_rows = [dict(row) for row in raw_rows_value] if rows_shape_valid else []
    if not title_is_valid or not rows_shape_valid:
        return {
            **raw_widget,
            "value": None,
            "rows": [],
            "status": "invalid_schema",
        }
    if name_key is None:
        return project_generic_gold_widget(
            raw_widget,
            raw_rows,
            identity,
            is_active_headcount=is_active_headcount,
            rows_shape_valid=rows_shape_valid,
        )
    observed_rows = _sf_gold_headcount_rows(raw_rows, ("unused_id", name_key))
    source_status = raw_widget.get("status")
    status = _sf_gold_headcount_widget_status(source_status, raw_rows, observed_rows)
    raw_value = raw_widget.get("value")
    observed_total = _sf_gold_headcount_total(observed_rows)
    value_is_valid = (
        type(raw_value) is int
        and raw_value >= 0
        and observed_total is not None
        and raw_value >= observed_total
    )
    coverage_subset = (
        status == "partial" and raw_widget.get("_coverage_observed") is True
    )
    rejected_rows = len(observed_rows) != len(raw_rows)
    if rejected_rows and not coverage_subset:
        status = "invalid_schema"
    if coverage_subset and rejected_rows and raw_value != observed_total:
        status = "invalid_schema"
    publish_observations = status == "ready" or (
        status == "partial" and coverage_subset
    )
    if publish_observations and not value_is_valid:
        status = "invalid_schema"
        publish_observations = False
    return {
        **raw_widget,
        "value": raw_value if publish_observations else None,
        "rows": observed_rows[:6] if publish_observations else [],
        "status": status,
    }


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
