from __future__ import annotations

from typing import Any, TypedDict

import asyncpg
from fastapi import HTTPException

from app.services.gold_publication_relation import (
    published_relation_columns,
    resolve_published_gold_relation,
)
from app.services.intelligence.benchmark_authority import (
    BENCHMARK_DATASET,
    resolve_benchmark_approval_authority,
)
from app.services.intelligence.gold_fetcher import _gold_dsn
from app.services.intelligence.gold_projection_guard import _valid_authority
from app.services.intelligence.utils import workspace_scope

READINESS_DATASET = "sap_successfactors_talent_readiness"
NINE_BOX_DATASET = "sap_successfactors_talent_9box"

_READINESS_REQUIRED = frozenset(
    {
        "tenant_id",
        "workspace_id",
        "invalid_score_input",
        "readiness_status",
        "source_mode",
        "readiness_score",
        "competency_score",
        "performance_score",
        "aspiration_score",
    }
)
_NINE_BOX_REQUIRED = frozenset(
    {
        "tenant_id",
        "workspace_id",
        "invalid_score_input",
        "performance_score",
        "potential_score",
        "box_status",
        "box_key",
    }
)

_CLAIM_COLUMN_SQL = {
    "source_mode": "source_mode = 'benchmark_internal'",
    "readiness_status": "readiness_status = 'benchmark_internal'",
    "box_status": "box_status = 'benchmark_internal'",
    "benchmark_raw_score": "benchmark_raw_score IS NOT NULL",
    "benchmark_score": "benchmark_score IS NOT NULL",
    "readiness_benchmark_count": "COALESCE(readiness_benchmark_count, 0) > 0",
    "benchmark_count": "COALESCE(benchmark_count, 0) > 0",
    "benchmark_provenance_status": (
        "COALESCE(benchmark_provenance_status, '') NOT IN "
        "('', 'approved_durable', 'not_applicable')"
    ),
}
_DURABLE_COLUMNS = frozenset(
    {
        "benchmark_approval_valid",
        "benchmark_provenance_status",
        "benchmark_materialization_head",
    }
)


class TalentPopulationCounts(TypedDict):
    readiness_calculable: int | None
    readiness_insufficient: int | None
    nine_box_available: int | None
    status: str
    error: str | None


class DesempenoCohortCounts(TypedDict):
    count: int | None
    band_counts: dict[str, int] | None
    status: str
    error: str | None


class NineBoxBoxCount(TypedDict):
    count: int | None
    status: str
    error: str | None


def _score_ok(column: str) -> str:
    return f"({column} IS NOT NULL AND {column} >= 0 AND {column} <= 100)"


def _claims_sql(columns: set[str]) -> str:
    terms = [
        sql for column, sql in _CLAIM_COLUMN_SQL.items() if column in columns
    ]
    if not terms:
        return "FALSE"
    return "(" + " OR ".join(terms) + ")"


def _durable_sql(columns: set[str]) -> str:
    if not _DURABLE_COLUMNS.issubset(columns):
        return "(FALSE AND $1::boolean AND $2::text IS NOT NULL)"
    return (
        "($1::boolean"
        " AND benchmark_approval_valid IS TRUE"
        " AND benchmark_provenance_status = 'approved_durable'"
        " AND COALESCE(benchmark_materialization_head::text, '') = $2)"
    )


def _not_degraded_sql(columns: set[str]) -> str:
    return f"(NOT {_claims_sql(columns)} OR {_durable_sql(columns)})"


async def _scoped_connection(user: dict | None):
    dsn = _gold_dsn()
    if not dsn:
        raise HTTPException(503, "gold database unavailable")
    tenant_id, workspace_id = workspace_scope(user)
    if not tenant_id:
        raise HTTPException(
            403, "gold dataset requires complete tenant/workspace scope"
        )
    conn = await asyncpg.connect(dsn, command_timeout=10)
    return conn, tenant_id, workspace_id


async def _relation_and_columns(conn, tenant_id, workspace_id, dataset):
    relation = await resolve_published_gold_relation(
        conn, tenant_id, workspace_id, dataset
    )
    exists = bool(await conn.fetchval("SELECT to_regclass($1)", relation.sql))
    if not exists:
        raise HTTPException(404, f"dataset unavailable: {dataset}")
    columns = set(await published_relation_columns(conn, relation))
    return relation, columns


async def _benchmark_verdict(
    conn, tenant_id: str, workspace_id: str
) -> tuple[bool, str]:
    try:
        relation = await resolve_published_gold_relation(
            conn, tenant_id, workspace_id, BENCHMARK_DATASET
        )
        benchmark_head = relation.run_id
    except HTTPException:
        return False, ""
    if not benchmark_head:
        return False, ""
    authority = await resolve_benchmark_approval_authority(
        tenant_id, workspace_id, benchmark_head
    )
    entry = authority.get((tenant_id, workspace_id))
    return (
        _valid_authority(
            entry,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            benchmark_head=benchmark_head,
        ),
        benchmark_head,
    )


def _http_status(exc: HTTPException) -> str:
    return {403: "no_permission", 404: "missing", 503: "unavailable"}.get(
        exc.status_code, "unavailable"
    )


async def query_talent_population_counts(user: dict | None) -> TalentPopulationCounts:

    def _unavailable(status: str, error: str | None) -> TalentPopulationCounts:
        return {
            "readiness_calculable": None,
            "readiness_insufficient": None,
            "nine_box_available": None,
            "status": status,
            "error": error,
        }

    try:
        conn, tenant_id, workspace_id = await _scoped_connection(user)
    except HTTPException as exc:
        return _unavailable(_http_status(exc), str(exc.detail))
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true),"
                " set_config('app.workspace_id', $2, true)",
                tenant_id,
                workspace_id,
            )
            authority_valid, benchmark_head = await _benchmark_verdict(
                conn, tenant_id, workspace_id
            )
            readiness_rel, readiness_cols = await _relation_and_columns(
                conn, tenant_id, workspace_id, READINESS_DATASET
            )
            nine_rel, nine_cols = await _relation_and_columns(
                conn, tenant_id, workspace_id, NINE_BOX_DATASET
            )
            if not _READINESS_REQUIRED.issubset(readiness_cols):
                return _unavailable(
                    "invalid_schema",
                    "readiness relation misses population count columns",
                )
            if not _NINE_BOX_REQUIRED.issubset(nine_cols):
                return _unavailable(
                    "invalid_schema",
                    "nine-box relation misses population count columns",
                )

            readiness_calculable_sql = f"""
                SELECT COUNT(*)::bigint AS total,
                       COUNT(*) FILTER (WHERE
                           invalid_score_input IS FALSE
                           AND LOWER(COALESCE(readiness_status, '')) IN
                               ('ready', 'near', 'not_ready')
                           AND {_score_ok('readiness_score')}
                           AND (
                               (LOWER(COALESCE(source_mode, '')) = 'cpa_real'
                                AND {_score_ok('competency_score')}
                                AND {_score_ok('performance_score')}
                                AND {_score_ok('aspiration_score')})
                               OR
                               (LOWER(COALESCE(source_mode, '')) = 'benchmark_internal'
                                AND {_durable_sql(readiness_cols)})
                           )
                       )::bigint AS calculable
                  FROM {readiness_rel.sql}
                 WHERE workspace_id::text = $3 AND tenant_id::text = $4
            """
            readiness_row = await conn.fetchrow(
                readiness_calculable_sql,
                authority_valid,
                benchmark_head,
                workspace_id,
                tenant_id,
            )

            nine_available_sql = f"""
                SELECT COUNT(*) FILTER (WHERE
                           invalid_score_input IS FALSE
                           AND {_score_ok('performance_score')}
                           AND {_score_ok('potential_score')}
                           AND LOWER(COALESCE(box_status, '')) = 'ready'
                           AND {_not_degraded_sql(nine_cols)}
                       )::bigint AS available
                  FROM {nine_rel.sql}
                 WHERE workspace_id::text = $3 AND tenant_id::text = $4
            """
            nine_row = await conn.fetchrow(
                nine_available_sql,
                authority_valid,
                benchmark_head,
                workspace_id,
                tenant_id,
            )
    except HTTPException as exc:
        return _unavailable(_http_status(exc), str(exc.detail))
    except (asyncpg.PostgresError, OSError) as exc:
        return _unavailable("unavailable", str(exc))
    finally:
        await conn.close()

    total = int(readiness_row["total"])
    calculable = int(readiness_row["calculable"])
    return {
        "readiness_calculable": calculable,
        "readiness_insufficient": total - calculable,
        "nine_box_available": int(nine_row["available"]),
        "status": "ready",
        "error": None,
    }


async def query_desempeno_cohort_counts(user: dict | None) -> DesempenoCohortCounts:

    def _unavailable(status: str, error: str | None) -> DesempenoCohortCounts:
        return {"count": None, "band_counts": None, "status": status, "error": error}

    try:
        conn, tenant_id, workspace_id = await _scoped_connection(user)
    except HTTPException as exc:
        return _unavailable(_http_status(exc), str(exc.detail))
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true),"
                " set_config('app.workspace_id', $2, true)",
                tenant_id,
                workspace_id,
            )
            relation, columns = await _relation_and_columns(
                conn, tenant_id, workspace_id, NINE_BOX_DATASET
            )
            if not {"invalid_score_input", "performance_score"}.issubset(columns):
                return _unavailable(
                    "invalid_schema",
                    "nine-box relation misses cohort columns",
                )
            band_column_sql = (
                "CASE WHEN performance_band_available IN ('high', 'medium', 'low')"
                " THEN performance_band_available ELSE NULL END"
                if "performance_band_available" in columns
                else "NULL"
            )
            cohort_sql = f"""
                WITH cohort AS (
                    SELECT COALESCE(
                               {band_column_sql},
                               CASE
                                   WHEN (CASE WHEN performance_score > 5
                                              THEN performance_score / 20.0
                                              ELSE performance_score END) >= 4
                                       THEN 'high'
                                   WHEN (CASE WHEN performance_score > 5
                                              THEN performance_score / 20.0
                                              ELSE performance_score END) >= 3
                                       THEN 'medium'
                                   ELSE 'low'
                               END
                           ) AS band
                      FROM {relation.sql}
                     WHERE workspace_id::text = $1 AND tenant_id::text = $2
                       AND invalid_score_input IS FALSE
                       AND {_score_ok('performance_score')}
                )
                SELECT COUNT(*)::bigint AS total,
                       COUNT(*) FILTER (WHERE band = 'high')::bigint AS high,
                       COUNT(*) FILTER (WHERE band = 'medium')::bigint AS medium,
                       COUNT(*) FILTER (WHERE band = 'low')::bigint AS low
                  FROM cohort
            """
            row = await conn.fetchrow(cohort_sql, workspace_id, tenant_id)
    except HTTPException as exc:
        return _unavailable(_http_status(exc), str(exc.detail))
    except (asyncpg.PostgresError, OSError) as exc:
        return _unavailable("unavailable", str(exc))
    finally:
        await conn.close()

    return {
        "count": int(row["total"]),
        "band_counts": {
            "high": int(row["high"]),
            "medium": int(row["medium"]),
            "low": int(row["low"]),
        },
        "status": "ready",
        "error": None,
    }


class NineBoxCellCounts(TypedDict):
    rows: list[dict[str, Any]]
    status: str
    error: str | None


async def query_nine_box_cell_counts(user: dict | None) -> NineBoxCellCounts:

    def _unavailable(status: str, error: str | None) -> NineBoxCellCounts:
        return {"rows": [], "status": status, "error": error}

    try:
        conn, tenant_id, workspace_id = await _scoped_connection(user)
    except HTTPException as exc:
        return _unavailable(_http_status(exc), str(exc.detail))
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true),"
                " set_config('app.workspace_id', $2, true)",
                tenant_id,
                workspace_id,
            )
            authority_valid, benchmark_head = await _benchmark_verdict(
                conn, tenant_id, workspace_id
            )
            relation, columns = await _relation_and_columns(
                conn, tenant_id, workspace_id, NINE_BOX_DATASET
            )
            if not _NINE_BOX_REQUIRED.issubset(columns):
                return _unavailable(
                    "invalid_schema",
                    "nine-box relation misses population count columns",
                )
            ready_pred = (
                "invalid_score_input IS FALSE"
                f" AND {_score_ok('performance_score')}"
                f" AND {_score_ok('potential_score')}"
                " AND LOWER(COALESCE(box_status, '')) = 'ready'"
            )
            benchmark_pred = (
                f"{ready_pred} AND source_mode = 'benchmark_internal'"
                if "source_mode" in columns
                else "FALSE"
            )
            cells_sql = f"""
                SELECT box_key,
                       COUNT(*)::bigint AS employee_count,
                       COUNT(*) FILTER (WHERE {ready_pred})::bigint AS ready_count,
                       COUNT(*) FILTER (WHERE {benchmark_pred})::bigint AS benchmark_count
                  FROM {relation.sql}
                 WHERE workspace_id::text = $3 AND tenant_id::text = $4
                   AND box_key IS NOT NULL
                   AND {_not_degraded_sql(columns)}
                 GROUP BY box_key
            """
            rows = await conn.fetch(
                cells_sql,
                authority_valid,
                benchmark_head,
                workspace_id,
                tenant_id,
            )
    except HTTPException as exc:
        return _unavailable(_http_status(exc), str(exc.detail))
    except (asyncpg.PostgresError, OSError) as exc:
        return _unavailable("unavailable", str(exc))
    finally:
        await conn.close()

    cells: list[dict[str, Any]] = []
    for row in rows:
        employee_count = int(row["employee_count"])
        ready_count = int(row["ready_count"])
        benchmark_count = int(row["benchmark_count"])
        cells.append(
            {
                "box_key": str(row["box_key"]),
                "employee_count": employee_count,
                "ready_count": ready_count,
                "benchmark_count": benchmark_count,
                "blocked_count": max(employee_count - ready_count, 0),
                "box_status": (
                    "benchmark_internal"
                    if benchmark_count and ready_count
                    else "ready"
                    if ready_count
                    else "blocked"
                ),
            }
        )
    return {"rows": cells, "status": "ready", "error": None}


async def query_nine_box_box_count(user: dict | None, box_id: str) -> NineBoxBoxCount:

    def _unavailable(status: str, error: str | None) -> NineBoxBoxCount:
        return {"count": None, "status": status, "error": error}

    try:
        conn, tenant_id, workspace_id = await _scoped_connection(user)
    except HTTPException as exc:
        return _unavailable(_http_status(exc), str(exc.detail))
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true),"
                " set_config('app.workspace_id', $2, true)",
                tenant_id,
                workspace_id,
            )
            authority_valid, benchmark_head = await _benchmark_verdict(
                conn, tenant_id, workspace_id
            )
            relation, columns = await _relation_and_columns(
                conn, tenant_id, workspace_id, NINE_BOX_DATASET
            )
            if not _NINE_BOX_REQUIRED.issubset(columns):
                return _unavailable(
                    "invalid_schema",
                    "nine-box relation misses population count columns",
                )
            box_count_sql = f"""
                SELECT COUNT(*) FILTER (WHERE
                           box_key = $5
                           AND invalid_score_input IS FALSE
                           AND {_score_ok('performance_score')}
                           AND {_score_ok('potential_score')}
                           AND {_not_degraded_sql(columns)}
                       )::bigint AS box_count
                  FROM {relation.sql}
                 WHERE workspace_id::text = $3 AND tenant_id::text = $4
            """
            row = await conn.fetchrow(
                box_count_sql,
                authority_valid,
                benchmark_head,
                workspace_id,
                tenant_id,
                box_id,
            )
    except HTTPException as exc:
        return _unavailable(_http_status(exc), str(exc.detail))
    except (asyncpg.PostgresError, OSError) as exc:
        return _unavailable("unavailable", str(exc))
    finally:
        await conn.close()

    return {"count": int(row["box_count"]), "status": "ready", "error": None}


__all__ = (
    "DesempenoCohortCounts",
    "NineBoxBoxCount",
    "NineBoxCellCounts",
    "TalentPopulationCounts",
    "query_desempeno_cohort_counts",
    "query_nine_box_box_count",
    "query_nine_box_cell_counts",
    "query_talent_population_counts",
)
