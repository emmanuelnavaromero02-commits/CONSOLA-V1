from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

import asyncpg


ACTIVE_HEADCOUNT_DATASET = "sap_successfactors_employee_360"
_TEXT_TYPES = frozenset({"character", "character varying", "text"})


class ActiveHeadcountResult(TypedDict):
    rows: list[dict[str, Any]]
    total: int | None
    status: str
    error: str | None


def _result(
    *, total: int | None, status: str, error: str | None = None
) -> ActiveHeadcountResult:
    return {"rows": [], "total": total, "status": status, "error": error}


def unavailable_active_headcount(error: object) -> ActiveHeadcountResult:
    return _result(total=None, status="unavailable", error=str(error))


def _contract_failure(
    columns: Mapping[str, Mapping[str, str]],
    *,
    table: str,
) -> ActiveHeadcountResult | None:
    table_columns = columns.get(table)
    if not table_columns:
        return _result(
            total=None,
            status="missing",
            error=f"dataset unavailable: {ACTIVE_HEADCOUNT_DATASET}",
        )
    required = {
        "tenant_id": _TEXT_TYPES,
        "workspace_id": _TEXT_TYPES,
        "is_active": frozenset({"boolean"}),
    }
    invalid = [
        name
        for name, accepted_types in required.items()
        if table_columns.get(name) not in accepted_types
    ]
    if invalid:
        fields = ", ".join(sorted(invalid))
        return _result(
            total=None,
            status="invalid_schema",
            error=f"invalid active headcount contract ({fields})",
        )
    return None


def _active_headcount_sql(relation_sql: str) -> str:
    return f"""
        SELECT COUNT(*)::bigint AS active_headcount
          FROM {relation_sql}
         WHERE workspace_id::text = $1
           AND tenant_id::text = $2
           AND is_active IS TRUE
    """


async def query_exact_active_headcount(
    conn: asyncpg.Connection,
    *,
    columns: Mapping[str, Mapping[str, str]],
    table: str,
    relation_sql: str | None = None,
    tenant_id: str,
    workspace_id: str,
) -> ActiveHeadcountResult:
    """Count the complete scoped active population without a row limit."""
    if failure := _contract_failure(columns, table=table):
        return failure
    rows = await conn.fetch(
        _active_headcount_sql(relation_sql or f'public."{table}"'),
        workspace_id,
        tenant_id,
    )
    if len(rows) != 1:
        return unavailable_active_headcount("invalid active headcount aggregate")
    value = rows[0].get("active_headcount")
    if type(value) is not int or value < 0:
        return unavailable_active_headcount("invalid active headcount value")
    return _result(total=value, status="ready")


__all__ = (
    "ACTIVE_HEADCOUNT_DATASET",
    "ActiveHeadcountResult",
    "query_exact_active_headcount",
    "unavailable_active_headcount",
)
