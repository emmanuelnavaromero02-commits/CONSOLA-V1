"""Pure helpers for the public data API payloads."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


DATA_API_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def data_api_columns_param(columns: str) -> list[str]:
    """Parse the comma-separated columns query parameter."""

    if not columns:
        return []
    return [item.strip() for item in columns.split(",") if item.strip()]


def data_api_invalid_column(columns: Iterable[str]) -> str | None:
    """Return the first unsafe column identifier, if any."""

    for column in columns:
        if not DATA_API_IDENTIFIER_RE.match(column):
            return column
    return None


def data_api_options_sql(dataset: str, columns: list[str]) -> str:
    """Build the legacy distinct-options SQL used by Refinement."""

    sqls = [
        f"SELECT DISTINCT {column} AS val, '{column}' AS col FROM pggold.gold_{dataset} WHERE {column} IS NOT NULL"
        for column in columns
    ]
    return " UNION ALL ".join(sqls) + " ORDER BY col, val"


def data_api_options_response(
    columns: list[str], rows: list[Mapping[str, Any]]
) -> dict[str, list[str]] | list[Any]:
    """Group Refinement rows by option column.

    The empty-row contract intentionally stays as ``[]`` because analytic apps
    already depend on that legacy response shape.
    """

    if not rows:
        return []

    options: dict[str, list[str]] = {column: [] for column in columns}
    for row in rows:
        col_key = row.get("col")
        if col_key in options and row.get("val") is not None:
            options[col_key].append(str(row["val"]))
    return options
