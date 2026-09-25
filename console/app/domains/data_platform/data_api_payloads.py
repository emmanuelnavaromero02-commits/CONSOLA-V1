from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


DATA_API_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
FISCAL_YEAR_EXPR = (
    "(CASE WHEN EXTRACT(MONTH FROM mes)<=2 THEN EXTRACT(YEAR FROM mes)-1 "
    "ELSE EXTRACT(YEAR FROM mes) END)"
)


@dataclass(frozen=True)
class DataApiFilteredQuery:
    sql: str
    params: list[str]
    limit: int


class DataApiQueryValidationError(ValueError):

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def data_api_columns_param(columns: str) -> list[str]:

    if not columns:
        return []
    return [item.strip() for item in columns.split(",") if item.strip()]


def data_api_invalid_column(columns: Iterable[str]) -> str | None:

    for column in columns:
        if not DATA_API_IDENTIFIER_RE.match(column):
            return column
    return None


def data_api_valid_dataset_name(dataset: str | None) -> bool:
    return bool(DATA_API_IDENTIFIER_RE.fullmatch(dataset or ""))


def data_api_options_sql(dataset: str, columns: list[str]) -> str:

    sqls = [
        f"SELECT DISTINCT {column} AS val, '{column}' AS col FROM pggold.gold_{dataset} WHERE {column} IS NOT NULL"
        for column in columns
    ]
    return " UNION ALL ".join(sqls) + " ORDER BY col, val"


def data_api_options_response(
    columns: list[str], rows: list[Mapping[str, Any]]
) -> dict[str, list[str]] | list[Any]:

    if not rows:
        return []

    options: dict[str, list[str]] = {column: [] for column in columns}
    for row in rows:
        col_key = row.get("col")
        if col_key in options and row.get("val") is not None:
            options[col_key].append(str(row["val"]))
    return options


def data_api_query_limit(value: Any) -> int:
    return min(int(value), 10000)


def data_api_select_clause(columns: Iterable[str]) -> str:
    safe_cols = []
    for column in columns:
        if column == "*" or DATA_API_IDENTIFIER_RE.match(column):
            safe_cols.append(column)
    return ", ".join(safe_cols) if safe_cols else "*"


def _data_api_add_param(params: list[str], value: Any) -> str:
    param_value = str(value)
    if len(param_value) > 500:
        raise DataApiQueryValidationError(400, "Filter value too long (max 500 chars)")
    params.append(param_value)
    return "?"


def data_api_filtered_query(
    dataset: str,
    *,
    filters: Any,
    limit: int,
    columns: Iterable[str],
) -> DataApiFilteredQuery:
    if not isinstance(filters, dict):
        raise DataApiQueryValidationError(400, "filters must be an object")
    if len(filters) > 20:
        raise DataApiQueryValidationError(400, "Too many filters (max 20)")

    select_clause = data_api_select_clause(columns)
    params: list[str] = []
    conditions: list[str] = []
    for key, value in filters.items():
        if value is None or value == "" or value == []:
            continue
        if key == "fiscal_year":
            values = value if isinstance(value, list) else [value]
            if len(values) > 50:
                raise DataApiQueryValidationError(
                    400, "Too many fiscal_year values (max 50)"
                )
            placeholders = ",".join(
                _data_api_add_param(params, int(item)) for item in values
            )
            conditions.append(f"{FISCAL_YEAR_EXPR} IN ({placeholders})")
        elif DATA_API_IDENTIFIER_RE.match(key):
            values = value if isinstance(value, list) else [value]
            if len(values) > 100:
                raise DataApiQueryValidationError(
                    400, f"Too many values for filter '{key}' (max 100)"
                )
            if len(values) == 1:
                conditions.append(f"{key} = {_data_api_add_param(params, values[0])}")
            else:
                placeholders = ",".join(
                    _data_api_add_param(params, item) for item in values
                )
                conditions.append(f"{key} IN ({placeholders})")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return DataApiFilteredQuery(
        sql=f"SELECT {select_clause} FROM pggold.gold_{dataset} {where} LIMIT {limit}",
        params=params,
        limit=limit,
    )
