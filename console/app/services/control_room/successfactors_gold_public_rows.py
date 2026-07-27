from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.public_text_sensitivity import public_business_label


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
_READY_STATUSES = frozenset({"ok", "ready"})
_KNOWN_STATUSES = _NON_READY_STATUSES | _READY_STATUSES
_PUBLIC_LABEL_KEYS = (
    "label",
    "company_name",
    "location_name",
    "department_name",
    "fact",
)
_PUBLIC_WIDGET_IDS = frozenset(
    {
        "sf_active_headcount",
        "sf_contractor_risk",
        "sf_headcount",
        "sf_headcount_by_company",
        "sf_headcount_by_department",
        "sf_headcount_by_location",
    }
)


def valid_gold_widget_id(value: object) -> bool:
    return isinstance(value, str) and value in _PUBLIC_WIDGET_IDS


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
    raw_rows: list[dict[str, Any]],
    identity: str,
    *,
    is_active_headcount: bool,
    rows_shape_valid: bool,
) -> dict[str, Any]:
    raw_status = raw_widget.get("status")
    if isinstance(raw_status, str):
        source_status = (
            raw_status if raw_status in _KNOWN_STATUSES else "invalid_schema"
        )
    else:
        source_status = "invalid_schema" if raw_status is not None else "unavailable"
    if not rows_shape_valid:
        return {**raw_widget, "value": None, "rows": [], "status": "invalid_schema"}

    public_rows: list[dict[str, Any]] = []
    rejected_labeled_row = False
    is_headcount = "headcount" in identity or any(
        "headcount" in row for row in raw_rows
    )
    for row in raw_rows:
        label_keys = [key for key in _PUBLIC_LABEL_KEYS if key in row]
        if is_headcount and (
            not label_keys or _strict_headcount(row.get("headcount")) is None
        ):
            rejected_labeled_row = True
            continue
        if not label_keys:
            rejected_labeled_row = True
            continue
        normalized = {key: public_business_label(row.get(key)) for key in label_keys}
        if any(value is None for value in normalized.values()):
            rejected_labeled_row = True
            continue
        business_names = [value for key, value in normalized.items() if key != "label"]
        if len(business_names) > 1:
            rejected_labeled_row = True
            continue
        if "label" not in normalized:
            if len(business_names) != 1:
                rejected_labeled_row = True
                continue
            normalized["label"] = business_names[0]
        elif (
            business_names
            and normalized["label"].casefold() != business_names[0].casefold()
        ):
            rejected_labeled_row = True
            continue
        public_rows.append({**row, **normalized})

    result = {**raw_widget, "rows": public_rows}
    coverage_subset = (
        is_headcount
        and not is_active_headcount
        and source_status == "partial"
        and raw_widget.get("_coverage_observed") is True
    )
    if source_status in _NON_READY_STATUSES and not coverage_subset:
        if (
            not is_headcount
            and raw_status is None
            and public_rows
            and not rejected_labeled_row
        ):
            return result
        result.update(value=None, rows=[], status=source_status)
        return result
    if not is_headcount:
        if rejected_labeled_row:
            result.update(value=None, rows=[], status="invalid_schema")
        return result

    raw_value = raw_widget.get("value")
    if is_active_headcount:
        if raw_rows or _strict_headcount(raw_value) is None:
            result.update(value=None, rows=[], status="invalid_schema")
        return result
    if not raw_rows:
        result.update(value=None, rows=[], status="invalid_schema")
        return result

    observed_total = _sf_gold_headcount_total(public_rows)
    value_is_valid = (
        type(raw_value) is int
        and raw_value >= 0
        and observed_total is not None
        and raw_value >= observed_total
    )
    if coverage_subset and (
        not public_rows
        or not value_is_valid
        or (rejected_labeled_row and raw_value != observed_total)
    ):
        result.update(value=None, rows=[], status="invalid_schema")
    elif rejected_labeled_row or not public_rows or not value_is_valid:
        result.update(value=None, rows=[], status="invalid_schema")
    return result


__all__ = (
    "_NON_READY_STATUSES",
    "_sf_gold_headcount_total",
    "_strict_headcount",
    "project_generic_gold_widget",
    "valid_gold_widget_id",
)
