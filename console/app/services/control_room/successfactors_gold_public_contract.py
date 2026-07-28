from __future__ import annotations

from typing import NamedTuple


class GoldWidgetContract(NamedTuple):
    title: str
    name_key: str | None = None
    active_headcount: bool = False
    generic_headcount: bool = False


GOLD_WIDGETS = {
    "sf_active_headcount": GoldWidgetContract(
        "Headcount total activo", active_headcount=True
    ),
    "sf_contractor_risk": GoldWidgetContract("Riesgo de contratistas"),
    "sf_headcount": GoldWidgetContract("Headcount", generic_headcount=True),
    "sf_headcount_by_company": GoldWidgetContract(
        "Headcount por compania", "company_name"
    ),
    "sf_headcount_by_department": GoldWidgetContract(
        "Headcount por departamento", "department_name"
    ),
    "sf_headcount_by_location": GoldWidgetContract(
        "Headcount por ubicacion", "location_name"
    ),
}
LABEL_KEYS = ("label", "company_name", "location_name", "department_name", "fact")
NON_READY_STATUSES = frozenset(
    {
        "blocked",
        "empty",
        "error",
        "invalid_schema",
        "missing",
        "no_permission",
        "unavailable",
    }
)
KNOWN_STATUSES = NON_READY_STATUSES | {"ok", "partial", "ready"}
COUNT_FIELDS = frozenset({"count", "headcount", "contractor_count"})
NUMBER_FIELDS = frozenset({"value", "risk_factor", "percentage", "rate"})
ROW_FIELDS = (
    "value",
    "count",
    "headcount",
    "contractor_count",
    "risk_factor",
    "percentage",
    "rate",
    "status",
)


__all__ = (
    "COUNT_FIELDS",
    "GOLD_WIDGETS",
    "KNOWN_STATUSES",
    "LABEL_KEYS",
    "NON_READY_STATUSES",
    "NUMBER_FIELDS",
    "ROW_FIELDS",
)
