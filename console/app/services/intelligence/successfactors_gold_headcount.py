from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import asyncpg
from fastapi import HTTPException

from app.services.intelligence.gold_fetcher import _gold_dsn, _gold_table
from app.services.intelligence.business_labels import business_label
from app.services.intelligence.successfactors_active_headcount import (
    ACTIVE_HEADCOUNT_DATASET,
    query_exact_active_headcount,
    unavailable_active_headcount,
)
from app.services.intelligence.utils import workspace_scope


_DIMENSIONS = (
    (
        "headcount_by_company",
        "sap_successfactors_headcount_by_company",
        "company_name",
    ),
    (
        "headcount_by_location",
        "sap_successfactors_headcount_by_location",
        "location_name",
    ),
    (
        "headcount_by_department",
        "sap_successfactors_headcount_by_department",
        "department_name",
    ),
)
_INTEGER_TYPES = frozenset({"smallint", "integer", "bigint"})
_TEXT_TYPES = frozenset({"character", "character varying", "text"})


def _aggregate_sql(table: str, name_key: str) -> str:
    return f"""
        WITH scoped AS MATERIALIZED (
            SELECT "{name_key}"::text AS raw_business_name,
                   headcount
              FROM public."{table}"
             WHERE workspace_id::text = $1
               AND tenant_id::text = $2
               AND "{name_key}"::text = ANY($3::text[])
        ),
        summary AS (
            SELECT COUNT(*) FILTER (
                       WHERE headcount IS NULL OR headcount < 0
                   )::bigint AS invalid_count,
                   COUNT(*) FILTER (
                       WHERE headcount IS NOT NULL AND headcount >= 0
                   )::bigint AS valid_count,
                   SUM(headcount) FILTER (
                       WHERE headcount IS NOT NULL AND headcount >= 0
                   )::bigint AS total_headcount
              FROM scoped
        ),
        top_rows AS (
            SELECT raw_business_name, headcount
              FROM scoped
             WHERE headcount IS NOT NULL AND headcount >= 0
             ORDER BY headcount DESC, raw_business_name
             LIMIT $4
        )
        SELECT summary.invalid_count,
               summary.valid_count,
               summary.total_headcount,
               top_rows.raw_business_name AS business_name,
               top_rows.headcount::bigint AS headcount
          FROM summary
          LEFT JOIN top_rows ON TRUE
         ORDER BY headcount DESC NULLS LAST, business_name
    """


def _column_contract(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    columns: dict[str, dict[str, str]] = {}
    for row in rows:
        table = str(row.get("table_name") or "")
        column = str(row.get("column_name") or "")
        data_type = str(row.get("data_type") or "")
        if table and column:
            columns.setdefault(table, {})[column] = data_type
    return columns


def _contract_error(
    columns: dict[str, dict[str, str]], dataset: str, name_key: str
) -> HTTPException | None:
    table_columns = columns.get(_gold_table(dataset))
    if not table_columns:
        return HTTPException(404, f"dataset unavailable: {dataset}")
    if not {"tenant_id", "workspace_id"} <= set(table_columns):
        return HTTPException(403, f"dataset is not tenant/workspace scoped: {dataset}")
    if table_columns.get("headcount") not in _INTEGER_TYPES:
        return HTTPException(503, f"invalid headcount contract: {dataset}")
    if table_columns.get(name_key) not in _TEXT_TYPES:
        return HTTPException(503, f"invalid business label contract: {dataset}")
    return None


def _strict_int(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _parse_dimension(
    dataset: str,
    name_key: str,
    rows: list[Mapping[str, Any]],
    limit: int,
) -> dict[str, Any]:
    if not rows:
        raise HTTPException(503, f"missing headcount aggregate: {dataset}")
    first = rows[0]
    invalid_count = _strict_int(first.get("invalid_count"))
    valid_count = _strict_int(first.get("valid_count"))
    if invalid_count is None or valid_count is None or invalid_count:
        raise HTTPException(503, f"invalid headcount observations: {dataset}")
    total = first.get("total_headcount")
    if any(
        _strict_int(row.get("invalid_count")) != invalid_count
        or _strict_int(row.get("valid_count")) != valid_count
        or row.get("total_headcount") != total
        or type(row.get("total_headcount")) is not type(total)
        for row in rows[1:]
    ):
        raise HTTPException(503, f"inconsistent headcount summary: {dataset}")
    if valid_count == 0:
        if total is not None or any(
            row.get("business_name") is not None for row in rows
        ):
            raise HTTPException(503, f"invalid empty headcount aggregate: {dataset}")
        return {"rows": [], "total": None, "status": "empty", "error": None}
    strict_total = _strict_int(total)
    public_rows: list[dict[str, Any]] = []
    for row in rows:
        name = business_label(row.get("business_name"))
        headcount = _strict_int(row.get("headcount"))
        if name is None or headcount is None:
            raise HTTPException(503, f"invalid headcount top rows: {dataset}")
        public_rows.append({name_key: name, "headcount": headcount})
    counts = [row["headcount"] for row in public_rows]
    if counts != sorted(counts, reverse=True):
        raise HTTPException(503, f"unordered headcount top rows: {dataset}")
    if strict_total is None or len(public_rows) != min(valid_count, limit):
        raise HTTPException(503, f"invalid headcount aggregate: {dataset}")
    if strict_total < sum(row["headcount"] for row in public_rows):
        raise HTTPException(503, f"inconsistent headcount aggregate: {dataset}")
    return {
        "rows": public_rows,
        "total": strict_total,
        "status": "ready",
        "error": None,
    }


def _failure(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HTTPException):
        status = {403: "no_permission", 404: "missing"}.get(
            exc.status_code, "unavailable"
        )
        error = str(exc.detail)
    else:
        status, error = "unavailable", str(exc)
    return {"rows": [], "total": None, "status": status, "error": error}


async def _query_dimension(
    conn: asyncpg.Connection,
    *,
    dataset: str,
    name_key: str,
    tenant_id: str,
    workspace_id: str,
    limit: int,
) -> dict[str, Any]:
    table = _gold_table(dataset)
    label_cursor = conn.cursor(
        f"""
        SELECT "{name_key}"::text AS business_name
          FROM public."{table}"
         WHERE workspace_id::text = $1 AND tenant_id::text = $2
        """,
        workspace_id,
        tenant_id,
        prefetch=1_000,
    )
    accepted: set[str] = set()
    async for row in label_cursor:
        raw = row.get("business_name")
        if isinstance(raw, str) and business_label(raw) is not None:
            accepted.add(raw)
    accepted_labels = sorted(accepted)
    if not accepted_labels:
        return {"rows": [], "total": None, "status": "empty", "error": None}
    rows = await conn.fetch(
        _aggregate_sql(table, name_key),
        workspace_id,
        tenant_id,
        accepted_labels,
        limit,
    )
    return _parse_dimension(dataset, name_key, [dict(row) for row in rows], limit)


async def query_successfactors_headcount_summaries(
    user: dict | None, *, limit: int = 5
) -> dict[str, dict[str, Any]]:
    dsn = _gold_dsn()
    if not dsn:
        raise HTTPException(503, "gold database unavailable")
    tenant_id, workspace_id = workspace_scope(user)
    if not tenant_id or not tenant_id.strip() or not workspace_id.strip():
        raise HTTPException(
            403, "gold dataset requires complete tenant/workspace scope"
        )
    safe_limit = max(1, min(limit if type(limit) is int else 5, 20))
    active_table = _gold_table(ACTIVE_HEADCOUNT_DATASET)
    tables = [
        active_table,
        *[_gold_table(dataset) for _key, dataset, _name_key in _DIMENSIONS],
    ]
    conn = await asyncpg.connect(dsn, command_timeout=10)
    results: dict[str, dict[str, Any]] = {}
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                tenant_id,
                workspace_id,
            )
            column_rows = await conn.fetch(
                """
                SELECT table_name, column_name, data_type
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = ANY($1::text[])
                """,
                tables,
            )
            columns = _column_contract([dict(row) for row in column_rows])
            try:
                async with conn.transaction():
                    results[
                        ACTIVE_HEADCOUNT_DATASET
                    ] = await query_exact_active_headcount(
                        conn,
                        columns=columns,
                        table=active_table,
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                    )
            except Exception as exc:
                results[ACTIVE_HEADCOUNT_DATASET] = unavailable_active_headcount(exc)
            for _key, dataset, name_key in _DIMENSIONS:
                if error := _contract_error(columns, dataset, name_key):
                    results[dataset] = _failure(error)
                    continue
                try:
                    async with conn.transaction():
                        results[dataset] = await _query_dimension(
                            conn,
                            dataset=dataset,
                            name_key=name_key,
                            tenant_id=tenant_id,
                            workspace_id=workspace_id,
                            limit=safe_limit,
                        )
                except Exception as exc:
                    results[dataset] = _failure(exc)
        return results
    finally:
        await conn.close()


__all__ = ("query_successfactors_headcount_summaries",)
