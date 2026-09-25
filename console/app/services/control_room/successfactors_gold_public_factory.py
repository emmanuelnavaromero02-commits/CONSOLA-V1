from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from math import isfinite
import re
from typing import TYPE_CHECKING, Any

from app.services.control_room.successfactors_gold_public_contract import (
    COUNT_FIELDS,
    GOLD_WIDGETS,
    KNOWN_STATUSES,
    LABEL_KEYS,
    NON_READY_STATUSES,
    NUMBER_FIELDS,
    ROW_FIELDS,
)
from app.services.public_text_sensitivity import public_business_label

if TYPE_CHECKING:
    from app.schemas.control_room_business_responses import (
        ControlRoomGoldKpisResponse,
        ControlRoomGoldWidget,
    )


_ISO_DATETIME = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)


def valid_gold_widget_id(value: object) -> bool:
    return type(value) is str and value in GOLD_WIDGETS


def strict_public_gold_label(value: object) -> str | None:

    if type(value) is not str or public_business_label(value) is None:
        return None
    return value


def _strict_count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _finite_number(value: object) -> int | float | None:
    if type(value) is int:
        return value
    if type(value) is float and isfinite(value):
        return value
    return None


def _status(value: object, *, required: bool) -> str | None:
    if value is None:
        return "unavailable" if required else None
    if type(value) is not str or value not in KNOWN_STATUSES:
        return "invalid_schema"
    return "ready" if value == "ok" else value


def _strict_timestamp(value: object) -> str | None:
    if type(value) is not str or _ISO_DATETIME.fullmatch(value) is None:
        return None
    try:
        datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return value


def _rows(value: object) -> list[dict[str, Any]] | None:
    if not isinstance(value, (list, tuple)):
        return None
    if not all(isinstance(row, Mapping) for row in value):
        return None
    return [dict(row) for row in value]


def _headcount_total(rows: Sequence[Mapping[str, Any]]) -> int | None:
    if not rows:
        return None
    counts = [_strict_count(row.get("headcount")) for row in rows]
    return (
        sum(count for count in counts if count is not None)
        if all(count is not None for count in counts)
        else None
    )


def project_dimension_rows(
    rows: Sequence[Mapping[str, Any]], name_key: str
) -> tuple[list[dict[str, Any]], bool]:
    visible: list[dict[str, Any]] = []
    rejected = False
    for row in rows:
        unexpected = any(
            key in row for key in LABEL_KEYS if key not in {"label", name_key}
        )
        name = strict_public_gold_label(row.get(name_key))
        supplied = (
            strict_public_gold_label(row.get("label")) if "label" in row else name
        )
        headcount = _strict_count(row.get("headcount"))
        if unexpected or name is None or supplied != name or headcount is None:
            rejected = True
            continue
        visible.append({"label": name, name_key: name, "headcount": headcount})
    visible.sort(key=lambda row: (-row["headcount"], row["label"].casefold()))
    return visible, rejected


def _invalid(base: dict[str, Any], status: str = "invalid_schema") -> dict[str, Any]:
    return {**base, "value": None, "rows": [], "status": status}


def _active_widget(
    base: dict[str, Any], raw: Mapping[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    status = _status(raw.get("status"), required=True)
    if status in NON_READY_STATUSES or status == "partial":
        return _invalid(base, status or "invalid_schema")
    value = _strict_count(raw.get("value"))
    if status != "ready" or rows or value is None:
        return _invalid(base)
    return {**base, "value": value, "rows": [], "status": "ready"}


def _dimension_widget(
    base: dict[str, Any],
    raw: Mapping[str, Any],
    rows: list[dict[str, Any]],
    name_key: str,
) -> dict[str, Any]:
    status = _status(raw.get("status"), required=True)
    if status in NON_READY_STATUSES:
        return _invalid(base, status or "invalid_schema")
    if status not in {"ready", "partial"}:
        return _invalid(base)
    visible, rejected = project_dimension_rows(rows, name_key)
    if not visible:
        return _invalid(base)
    visible_total = _headcount_total(visible)
    raw_value = _strict_count(raw.get("value"))
    if visible_total is None or raw_value is None or raw_value < visible_total:
        return _invalid(base)
    public_rows = visible[:6]
    public_total = _headcount_total(public_rows)
    if public_total is None:
        return _invalid(base)
    partial = (
        status == "partial"
        or rejected
        or len(public_rows) != len(visible)
        or raw_value != public_total
    )
    return {
        **base,
        "value": public_total if partial else raw_value,
        "rows": public_rows,
        "status": "partial" if partial else "ready",
    }


def _generic_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    label_keys = [key for key in LABEL_KEYS if key in row]
    labels = {key: strict_public_gold_label(row.get(key)) for key in label_keys}
    if not label_keys or any(value is None for value in labels.values()):
        return None
    names = [value for key, value in labels.items() if key != "label"]
    label = labels.get("label")
    if len(names) > 1 or (label is not None and names and label != names[0]):
        return None
    if label is None:
        label = names[0]
    projected: dict[str, Any] = {**labels, "label": label}
    for field in ROW_FIELDS:
        if field not in row:
            continue
        value = row.get(field)
        if field in COUNT_FIELDS:
            public_value = _strict_count(value)
        elif field in NUMBER_FIELDS:
            public_value = _finite_number(value)
        else:
            public_value = _status(value, required=False)
        if public_value is None or public_value == "invalid_schema":
            return None
        projected[field] = public_value
    return projected


def _generic_widget(
    base: dict[str, Any],
    raw: Mapping[str, Any],
    rows: list[dict[str, Any]],
    *,
    headcount: bool,
) -> dict[str, Any]:
    status = _status(raw.get("status"), required=headcount)
    visible = [projected for row in rows if (projected := _generic_row(row))]
    rejected = len(visible) != len(rows)
    if status in NON_READY_STATUSES:
        return _invalid(base, status or "invalid_schema")
    if (not headcount and rejected) or (headcount and not visible):
        return _invalid(base)
    if headcount:
        total = _headcount_total(visible)
        raw_value = _strict_count(raw.get("value"))
        if total is None or raw_value is None or raw_value < total:
            return _invalid(base)
        public_rows = visible[:6]
        public_total = _headcount_total(public_rows)
        if public_total is None:
            return _invalid(base)
        partial = status == "partial" or rejected or len(public_rows) != len(visible)
        partial = partial or raw_value != public_total
        return {
            **base,
            "value": public_total if partial else raw_value,
            "rows": public_rows,
            "status": "partial" if partial else "ready",
        }
    projected = {**base, "rows": visible, "status": status}
    for field in ("value", "risk_factor"):
        if field in raw:
            value = _finite_number(raw.get(field))
            if value is None:
                return _invalid(base)
            projected[field] = value
    if "contractor_count" in raw:
        count = _strict_count(raw.get("contractor_count"))
        if count is None:
            return _invalid(base)
        projected["contractor_count"] = count
    return projected


def project_public_gold_widget(raw: object) -> dict[str, Any]:
    source = raw if isinstance(raw, Mapping) else {}
    raw_id = source.get("id")
    contract = GOLD_WIDGETS.get(raw_id) if type(raw_id) is str else None
    if contract is None:
        return _invalid({"id": None, "title": None})
    base = {"id": raw_id, "title": contract.title}
    rows = _rows(source.get("rows"))
    if rows is None:
        return _invalid(base)
    if contract.active_headcount:
        return _active_widget(base, source, rows)
    if contract.name_key:
        return _dimension_widget(base, source, rows, contract.name_key)
    return _generic_widget(base, source, rows, headcount=contract.generic_headcount)


def project_public_gold_response(raw: object) -> dict[str, Any]:
    source = raw if isinstance(raw, Mapping) else {}
    widgets = source.get("widgets")
    projected = {
        "widgets": [
            project_public_gold_widget(widget)
            for widget in widgets
            if isinstance(widget, Mapping)
        ]
        if isinstance(widgets, (list, tuple))
        else []
    }
    generated_at = _strict_timestamp(source.get("generated_at"))
    if generated_at is not None:
        projected["generated_at"] = generated_at
    return projected


def build_public_gold_widget(raw: object) -> ControlRoomGoldWidget:
    from app.schemas.control_room_business_responses import ControlRoomGoldWidget

    return ControlRoomGoldWidget.model_validate(project_public_gold_widget(raw))


def build_public_gold_response(raw: object) -> ControlRoomGoldKpisResponse:
    from app.schemas.control_room_business_responses import ControlRoomGoldKpisResponse

    return ControlRoomGoldKpisResponse.model_validate(project_public_gold_response(raw))


__all__ = (
    "build_public_gold_response",
    "build_public_gold_widget",
    "project_dimension_rows",
    "project_public_gold_response",
    "project_public_gold_widget",
    "strict_public_gold_label",
    "valid_gold_widget_id",
)
