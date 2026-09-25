from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

import asyncpg
from fastapi import HTTPException

from app.services import auth
from app.services.db_scope import scoped_db, workspace_scope_from_user
from app.services.intelligence.domain_aggregate_support import (
    GOLD_SCOPE_PREDICATE,
    MAX_GROUP_ROWS,
    STATUS_DEGRADED,
    STATUS_READY,
    STATUS_UNAVAILABLE,
    AggregateResult,
    GoldScope,
    as_float,
    as_int,
    as_of_date,
    as_of_datetime,
    business_days_in_month,
    clamp_top_n,
    gold_evidence,
    http_reason,
    invalid_schema_error,
    missing_optional_columns,
    month_start,
    resolve_optional_relation,
    resolve_relation,
    run_gold_aggregate,
    status_for,
)

ABSENCE_DATASET = "absence_by_type_and_month"
HEADCOUNT_DATASET = "headcount_by_department"

FRESHNESS_SLA_ENV = "OPERATIONS_FRESHNESS_SLA_HOURS"
DEFAULT_FRESHNESS_SLA_HOURS = 24.0
RECENT_FAILURES_LIMIT = 10

PIPELINE_RUNS_TABLE = "pipeline_runs"
EXTRACTION_RUNS_TABLE = "extraction_runs"
CONSOLE_SCOPE_PREDICATE = (
    "workspace_id = $1::uuid AND ($2::uuid IS NULL OR tenant_id = $2::uuid)"
)

_ABSENCE_REQUIRED = frozenset({"absence_month", "absence_type", "total_days_workable"})
_ABSENCE_OPTIONAL = frozenset({"employees_affected", "absence_records"})
_HEADCOUNT_REQUIRED = frozenset({"headcount"})

FRESHNESS_PROXY_NOTE = (
    "Horas transcurridas desde la ultima extraccion exitosa por cartucho, "
    "calculadas sobre extraction_runs y pipeline_runs de la base de console. "
    "NO mide cumplimiento de un SLA de negocio: OMEGA no tiene un SLA de "
    "frescura configurado y el umbral sla_hours es un parametro (o la variable "
    "OPERATIONS_FRESHNESS_SLA_HOURS). Solo SuccessFactors espeja extraction_runs "
    "en pipeline_runs y Replicon (DAG SES inbox) escribe pipeline_runs "
    "directamente; los demas cartuchos registran solo en extraction_runs. Los "
    "cartuchos sin ninguna corrida exitosa visible NO aparecen en la lista."
)

RUN_LOG_SOURCES_NOTE = (
    "solo SuccessFactors espeja extraction_runs en pipeline_runs y Replicon (DAG "
    "SES inbox) escribe pipeline_runs directamente; los conteos suman ambas "
    "tablas por cartucho"
)

ABSENCE_RATE_PROXY_NOTE = (
    "Tasa de ausentismo a nivel EMPRESA por tipo de ausencia: dias habiles de "
    "ausencia del ultimo mes cerrado (absence_by_type_and_month, SAP HCM PA2001) "
    "divididos entre headcount activo actual x dias habiles del mes. NO esta "
    "desglosada por unidad organizativa (el dataset gold de ausencias no trae "
    "org_unit y el puente pernr->org_unit es silver) y el headcount es el "
    "snapshot actual de headcount_by_department, no el del mes analizado."
)


@dataclass
class ConsoleScope:
    conn: Any
    tenant_id: str | None
    workspace_id: str

    @property
    def scope_args(self) -> tuple[str, str | None]:
        return (self.workspace_id, self.tenant_id or None)


@asynccontextmanager
async def open_console_scope(
    user: dict | None, pool: Any
) -> AsyncIterator[ConsoleScope]:
    tenant_id, workspace_id = workspace_scope_from_user(user)
    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            async with scoped_db(conn, tenant_id, workspace_id) as scoped:
                yield ConsoleScope(
                    conn=scoped, tenant_id=tenant_id, workspace_id=workspace_id
                )


ResultT = TypeVar("ResultT", bound=AggregateResult)


async def run_console_aggregate(
    user: dict | None,
    compute: Callable[[ConsoleScope], Awaitable[ResultT]],
    unavailable: Callable[[str], ResultT],
) -> ResultT:
    try:
        pool = await auth.pool()
        async with open_console_scope(user, pool) as scope:
            return await compute(scope)
    except HTTPException as exc:
        return unavailable(f"{http_reason(exc)}: {exc.detail}")
    except (asyncpg.PostgresError, OSError) as exc:
        return unavailable(f"unavailable: {exc}")


def _console_evidence(table: str, *, filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "console_table",
        "table": table,
        "database": "DATABASE_URL",
        "filters": filters,
    }


@dataclass
class PipelineHealth(AggregateResult):
    as_of: datetime | None = None
    window_24h_start: datetime | None = None
    window_7d_start: datetime | None = None
    cartridges: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)
    cartridges_count: int | None = None
    cartridges_with_failures_24h: int | None = None
    failing_entities: list[dict[str, Any]] = field(default_factory=list)


def _health_sql(table: str) -> str:
    return f"""
        -- omega-aggregate: operations.pipeline_health.{table}
        SELECT cartridge_id,
               COUNT(*) FILTER (WHERE status = 'failed'  AND started_at >= $3)::bigint AS failed_24h,
               COUNT(*) FILTER (WHERE status = 'success' AND started_at >= $3)::bigint AS success_24h,
               COUNT(*) FILTER (WHERE status = 'failed')::bigint  AS failed_7d,
               COUNT(*) FILTER (WHERE status = 'success')::bigint AS success_7d,
               COUNT(*) FILTER (WHERE status = 'partial')::bigint AS partial_7d,
               MAX(finished_at) FILTER (WHERE status = 'failed') AS last_failed_at
          FROM {table}
         WHERE {CONSOLE_SCOPE_PREDICATE}
           AND started_at >= $4
         GROUP BY cartridge_id
         ORDER BY failed_7d DESC, cartridge_id
         LIMIT $5
    """


_HEALTH_TOTALS_SQL = f"""
    -- omega-aggregate: operations.pipeline_health.totals
    SELECT COUNT(*) FILTER (WHERE status = 'failed'  AND started_at >= $3)::bigint AS failed_24h,
           COUNT(*) FILTER (WHERE status = 'success' AND started_at >= $3)::bigint AS success_24h,
           COUNT(*) FILTER (WHERE status = 'failed')::bigint  AS failed_7d,
           COUNT(*) FILTER (WHERE status = 'success')::bigint AS success_7d,
           COUNT(*) FILTER (WHERE status = 'partial')::bigint AS partial_7d,
           COUNT(DISTINCT cartridge_id)::bigint AS cartridges_count,
           COUNT(DISTINCT cartridge_id) FILTER (WHERE status = 'failed' AND started_at >= $3)::bigint
               AS cartridges_with_failures_24h
      FROM (
           SELECT cartridge_id, status, started_at FROM pipeline_runs
            WHERE {CONSOLE_SCOPE_PREDICATE} AND started_at >= $4
           UNION ALL
           SELECT cartridge_id, status, started_at FROM extraction_runs
            WHERE {CONSOLE_SCOPE_PREDICATE} AND started_at >= $4
      ) runs
"""

_FAILING_ENTITIES_SQL = {
    PIPELINE_RUNS_TABLE: f"""
        -- omega-aggregate: operations.pipeline_health.failing_entities.pipeline_runs
        SELECT cartridge_id, entity,
               COUNT(*)::bigint AS failures_7d,
               COUNT(*) FILTER (WHERE started_at >= $3)::bigint AS failures_24h,
               MAX(COALESCE(finished_at, started_at)) AS last_failed_at
          FROM pipeline_runs
         WHERE {CONSOLE_SCOPE_PREDICATE}
           AND status = 'failed' AND started_at >= $4
         GROUP BY cartridge_id, entity
         ORDER BY failures_7d DESC, last_failed_at DESC, cartridge_id, entity
         LIMIT $5
    """,
    EXTRACTION_RUNS_TABLE: f"""
        -- omega-aggregate: operations.pipeline_health.failing_entities.extraction_runs
        SELECT cartridge_id, entity_name AS entity,
               COUNT(*)::bigint AS failures_7d,
               COUNT(*) FILTER (WHERE started_at >= $3)::bigint AS failures_24h,
               MAX(COALESCE(finished_at, started_at)) AS last_failed_at
          FROM extraction_runs
         WHERE {CONSOLE_SCOPE_PREDICATE}
           AND status = 'failed' AND started_at >= $4
         GROUP BY cartridge_id, entity_name
         ORDER BY failures_7d DESC, last_failed_at DESC, cartridge_id, entity
         LIMIT $5
    """,
}


def _merge_failing_entities(per_table: dict[str, list[Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for table, rows in per_table.items():
        for row in rows:
            key = (str(row["cartridge_id"]), str(row["entity"] or ""))
            item = merged.setdefault(
                key,
                {
                    "cartridge_id": key[0],
                    "entity": key[1],
                    "failures_7d": 0,
                    "failures_24h": 0,
                    "last_failed_at": None,
                    "sources": [],
                },
            )
            item["failures_7d"] += as_int(row["failures_7d"]) or 0
            item["failures_24h"] += as_int(row["failures_24h"]) or 0
            last = row["last_failed_at"]
            if last is not None and (
                item["last_failed_at"] is None or last > item["last_failed_at"]
            ):
                item["last_failed_at"] = last
            item["sources"].append(table)
    out = list(merged.values())
    out.sort(
        key=lambda item: (
            -item["failures_7d"],
            -(item["last_failed_at"].timestamp() if item["last_failed_at"] else 0.0),
            item["cartridge_id"],
            item["entity"],
        )
    )
    return out


def _merge_health_rows(per_table: dict[str, list[Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for table, rows in per_table.items():
        for row in rows:
            cartridge = str(row["cartridge_id"])
            item = merged.setdefault(
                cartridge,
                {
                    "cartridge_id": cartridge,
                    "failed_24h": 0,
                    "success_24h": 0,
                    "failed_7d": 0,
                    "success_7d": 0,
                    "partial_7d": 0,
                    "last_failed_at": None,
                    "sources": [],
                },
            )
            for key in (
                "failed_24h",
                "success_24h",
                "failed_7d",
                "success_7d",
                "partial_7d",
            ):
                item[key] += as_int(row[key]) or 0
            last_failed = row["last_failed_at"]
            if last_failed is not None and (
                item["last_failed_at"] is None or last_failed > item["last_failed_at"]
            ):
                item["last_failed_at"] = last_failed
            item["sources"].append(table)
    out = []
    for item in merged.values():
        runs_7d = item["failed_7d"] + item["success_7d"]
        item["failure_rate_7d"] = (
            round(item["failed_7d"] / runs_7d, 4) if runs_7d > 0 else None
        )
        out.append(item)
    out.sort(key=lambda item: (-item["failed_7d"], item["cartridge_id"]))
    return out


async def query_pipeline_health(
    user: dict | None,
    *,
    as_of: datetime | None = None,
    failing_entities_top_n: int = RECENT_FAILURES_LIMIT,
) -> PipelineHealth:
    now = as_of_datetime(as_of)
    start_24h = now - timedelta(hours=24)
    start_7d = now - timedelta(days=7)
    entities_limit = clamp_top_n(failing_entities_top_n, default=RECENT_FAILURES_LIMIT)
    base = {"as_of": now, "window_24h_start": start_24h, "window_7d_start": start_7d}

    def _unavailable(error: str) -> PipelineHealth:
        return PipelineHealth(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: ConsoleScope) -> PipelineHealth:
        totals_row = await scope.conn.fetchrow(
            _HEALTH_TOTALS_SQL, *scope.scope_args, start_24h, start_7d
        )
        per_table: dict[str, list[Any]] = {}
        for table in (PIPELINE_RUNS_TABLE, EXTRACTION_RUNS_TABLE):
            per_table[table] = await scope.conn.fetch(
                _health_sql(table),
                *scope.scope_args,
                start_24h,
                start_7d,
                MAX_GROUP_ROWS,
            )
        cartridges = _merge_health_rows(per_table)
        entities_per_table: dict[str, list[Any]] = {}
        for table, sql in _FAILING_ENTITIES_SQL.items():
            entities_per_table[table] = await scope.conn.fetch(
                sql, *scope.scope_args, start_24h, start_7d, entities_limit
            )
        failing_entities = _merge_failing_entities(entities_per_table)[:entities_limit]
        totals = {
            key: as_int(totals_row[key]) or 0
            for key in (
                "failed_24h",
                "success_24h",
                "failed_7d",
                "success_7d",
                "partial_7d",
            )
        }
        cartridges_count = as_int(totals_row["cartridges_count"]) or 0
        notes = [RUN_LOG_SOURCES_NOTE]
        if cartridges_count > len(cartridges):
            notes.append(
                f"cartridges muestra {len(cartridges)} de {cartridges_count} cartuchos "
                "(mayor numero de fallos primero); los totales cubren todos"
            )
        filters = {
            "window_24h_start": start_24h,
            "window_7d_start": start_7d,
            "as_of": now,
            "workspace_id": scope.workspace_id,
            "tenant_id": scope.tenant_id,
            "breakdown_limit": MAX_GROUP_ROWS,
            "failing_entities_top_n": entities_limit,
        }
        return PipelineHealth(
            status=STATUS_READY,
            evidence_refs=[
                _console_evidence(PIPELINE_RUNS_TABLE, filters=filters),
                _console_evidence(EXTRACTION_RUNS_TABLE, filters=filters),
            ],
            notes=notes,
            cartridges=cartridges,
            totals=totals,
            cartridges_count=cartridges_count,
            cartridges_with_failures_24h=as_int(
                totals_row["cartridges_with_failures_24h"]
            )
            or 0,
            failing_entities=failing_entities,
            **base,
        )

    return await run_console_aggregate(user, _compute, _unavailable)


@dataclass
class DataFreshnessByCartridge(AggregateResult):
    as_of: datetime | None = None
    sla_hours: float | None = None
    sla_source: str | None = None
    cartridges: list[dict[str, Any]] = field(default_factory=list)
    cartridges_count: int | None = None
    exceeding_sla: int | None = None


def resolve_sla_hours(sla_hours: float | None) -> tuple[float, str]:
    if sla_hours is not None:
        try:
            value = float(sla_hours)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value, "param"
    raw = os.environ.get(FRESHNESS_SLA_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if value > 0:
            return value, "env"
    return DEFAULT_FRESHNESS_SLA_HOURS, "default"


def _freshness_sql(table: str) -> str:
    return f"""
        -- omega-aggregate: operations.data_freshness.{table}
        SELECT cartridge_id,
               MAX(finished_at) AS last_success_at,
               COUNT(*)::bigint AS success_runs
          FROM {table}
         WHERE {CONSOLE_SCOPE_PREDICATE}
           AND status = 'success'
         GROUP BY cartridge_id
         ORDER BY last_success_at ASC NULLS FIRST, cartridge_id
         LIMIT $3
    """


_FRESHNESS_TOTALS_SQL = f"""
    -- omega-aggregate: operations.data_freshness.totals
    SELECT COUNT(*)::bigint AS cartridges_count,
           COUNT(*) FILTER (WHERE last_success_at < $3)::bigint AS exceeding_sla
      FROM (
           SELECT cartridge_id, MAX(finished_at) AS last_success_at
             FROM (
                  SELECT cartridge_id, finished_at FROM extraction_runs
                   WHERE {CONSOLE_SCOPE_PREDICATE} AND status = 'success'
                  UNION ALL
                  SELECT cartridge_id, finished_at FROM pipeline_runs
                   WHERE {CONSOLE_SCOPE_PREDICATE} AND status = 'success'
             ) runs
            GROUP BY cartridge_id
      ) per_cartridge
"""


async def query_data_freshness_by_cartridge(
    user: dict | None,
    *,
    sla_hours: float | None = None,
    as_of: datetime | None = None,
) -> DataFreshnessByCartridge:
    now = as_of_datetime(as_of)
    sla_value, sla_source = resolve_sla_hours(sla_hours)
    base = {
        "proxy_note": FRESHNESS_PROXY_NOTE,
        "as_of": now,
        "sla_hours": sla_value,
        "sla_source": sla_source,
    }

    def _unavailable(error: str) -> DataFreshnessByCartridge:
        return DataFreshnessByCartridge(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: ConsoleScope) -> DataFreshnessByCartridge:
        sla_threshold = now - timedelta(hours=sla_value)
        totals_row = await scope.conn.fetchrow(
            _FRESHNESS_TOTALS_SQL, *scope.scope_args, sla_threshold
        )
        merged: dict[str, dict[str, Any]] = {}
        for table in (EXTRACTION_RUNS_TABLE, PIPELINE_RUNS_TABLE):
            rows = await scope.conn.fetch(
                _freshness_sql(table), *scope.scope_args, MAX_GROUP_ROWS
            )
            for row in rows:
                cartridge = str(row["cartridge_id"])
                item = merged.setdefault(
                    cartridge,
                    {
                        "cartridge_id": cartridge,
                        "last_success_at": None,
                        "success_runs": 0,
                        "sources": [],
                    },
                )
                last = row["last_success_at"]
                if last is not None and (
                    item["last_success_at"] is None or last > item["last_success_at"]
                ):
                    item["last_success_at"] = last
                item["success_runs"] += as_int(row["success_runs"]) or 0
                item["sources"].append(table)
        cartridges = []
        for item in merged.values():
            last = item["last_success_at"]
            hours = None
            if last is not None:
                hours = round((now - as_of_datetime(last)).total_seconds() / 3600.0, 2)
            item["hours_since_success"] = hours
            item["exceeds_sla"] = bool(hours is not None and hours > sla_value)
            cartridges.append(item)
        cartridges.sort(
            key=lambda item: (
                -(item["hours_since_success"] or 0.0),
                item["cartridge_id"],
            )
        )
        cartridges_count = as_int(totals_row["cartridges_count"]) or 0
        notes = [RUN_LOG_SOURCES_NOTE]
        if sla_source == "default":
            notes.append("sla_hours no configurado: se usa el default de 24h")
        if cartridges_count > len(cartridges):
            notes.append(
                f"cartridges muestra {len(cartridges)} de {cartridges_count} cartuchos "
                "(mas desactualizados primero); exceeding_sla cubre todos"
            )
        filters = {
            "status": "success",
            "as_of": now,
            "sla_hours": sla_value,
            "sla_source": sla_source,
            "sla_threshold": sla_threshold,
            "workspace_id": scope.workspace_id,
            "tenant_id": scope.tenant_id,
            "breakdown_limit": MAX_GROUP_ROWS,
        }
        return DataFreshnessByCartridge(
            status=STATUS_DEGRADED if sla_source == "default" else STATUS_READY,
            evidence_refs=[
                _console_evidence(EXTRACTION_RUNS_TABLE, filters=filters),
                _console_evidence(PIPELINE_RUNS_TABLE, filters=filters),
            ],
            notes=notes,
            cartridges=cartridges,
            cartridges_count=cartridges_count,
            exceeding_sla=as_int(totals_row["exceeding_sla"]) or 0,
            **base,
        )

    return await run_console_aggregate(user, _compute, _unavailable)


@dataclass
class AbsenceRateCompanyByType(AggregateResult):
    period: date | None = None
    working_days: int | None = None
    headcount: int | None = None
    total_days_workable: float | None = None
    absence_rate: float | None = None
    types_count: int | None = None
    by_type: list[dict[str, Any]] = field(default_factory=list)


async def query_absence_rate_company_by_type(
    user: dict | None,
    *,
    top_n: int = 20,
    as_of: date | None = None,
) -> AbsenceRateCompanyByType:
    top_n = clamp_top_n(top_n, default=20)
    current_month = month_start(as_of_date(as_of))
    base = {"proxy_note": ABSENCE_RATE_PROXY_NOTE}

    def _unavailable(error: str) -> AbsenceRateCompanyByType:
        return AbsenceRateCompanyByType(status=STATUS_UNAVAILABLE, error=error, **base)

    async def _compute(scope: GoldScope) -> AbsenceRateCompanyByType:
        absence = await resolve_relation(
            scope,
            ABSENCE_DATASET,
            required=_ABSENCE_REQUIRED,
            optional=_ABSENCE_OPTIONAL,
        )
        if absence.missing_required:
            return AbsenceRateCompanyByType(
                status=STATUS_UNAVAILABLE,
                error=invalid_schema_error(absence),
                missing_columns=list(absence.missing_required),
                **base,
            )
        period_sql = f"""
            -- omega-aggregate: operations.absence_rate.period
            SELECT MAX(absence_month)::date AS period
              FROM {absence.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND absence_month::date < $3::date
        """
        period = await scope.conn.fetchval(period_sql, *scope.scope_args, current_month)
        evidence = [
            gold_evidence(
                absence,
                filters={
                    "before_month": current_month,
                    "period": period,
                    "top_n": top_n,
                },
            )
        ]
        notes: list[str] = []
        if period is None:
            return AbsenceRateCompanyByType(
                status=STATUS_DEGRADED,
                evidence_refs=evidence,
                missing_columns=missing_optional_columns(absence),
                notes=["sin meses cerrados con datos en absence_by_type_and_month"],
                **base,
            )
        affected_expr = absence.expr(
            "employees_affected", "SUM(employees_affected)::bigint", "NULL::bigint"
        )
        records_expr = absence.expr(
            "absence_records", "SUM(absence_records)::bigint", "NULL::bigint"
        )
        totals_sql = f"""
            -- omega-aggregate: operations.absence_rate.totals
            SELECT COALESCE(SUM(total_days_workable), 0)::float8 AS total_days_workable,
                   COUNT(DISTINCT absence_type)::bigint AS types_count
              FROM {absence.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND absence_month::date = $3::date
        """
        totals = await scope.conn.fetchrow(totals_sql, *scope.scope_args, period)
        by_type_sql = f"""
            -- omega-aggregate: operations.absence_rate.by_type
            SELECT absence_type,
                   COALESCE(SUM(total_days_workable), 0)::float8 AS days_workable,
                   {affected_expr} AS employees_affected,
                   {records_expr} AS absence_records
              FROM {absence.sql}
             WHERE {GOLD_SCOPE_PREDICATE}
               AND absence_month::date = $3::date
             GROUP BY absence_type
             ORDER BY days_workable DESC NULLS LAST, absence_type
             LIMIT $4
        """
        type_rows = await scope.conn.fetch(
            by_type_sql, *scope.scope_args, period, top_n
        )

        headcount_rel = await resolve_optional_relation(
            scope, HEADCOUNT_DATASET, required=_HEADCOUNT_REQUIRED
        )
        headcount: int | None = None
        if headcount_rel is None or headcount_rel.missing_required:
            notes.append(
                "headcount_by_department no disponible: absence_rate no calculable, "
                "solo dias de ausencia"
            )
        else:
            headcount_sql = f"""
                -- omega-aggregate: operations.absence_rate.headcount
                SELECT COALESCE(SUM(headcount), 0)::bigint AS headcount
                  FROM {headcount_rel.sql}
                 WHERE {GOLD_SCOPE_PREDICATE}
            """
            headcount = as_int(
                await scope.conn.fetchval(headcount_sql, *scope.scope_args)
            )
            evidence.append(
                gold_evidence(headcount_rel, filters={"snapshot": "current"})
            )
            if not headcount:
                notes.append(
                    "headcount_by_department sin filas para el scope: absence_rate "
                    "no calculable, solo dias de ausencia"
                )

        working_days = business_days_in_month(period)
        total_days = as_float(totals["total_days_workable"]) or 0.0
        denominator = (headcount or 0) * working_days
        rate = round(total_days / denominator, 4) if denominator > 0 else None
        by_type = []
        for row in type_rows:
            days = as_float(row["days_workable"]) or 0.0
            by_type.append(
                {
                    "absence_type": row["absence_type"],
                    "days_workable": days,
                    "employees_affected": as_int(row["employees_affected"]),
                    "absence_records": as_int(row["absence_records"]),
                    "rate": round(days / denominator, 4) if denominator > 0 else None,
                }
            )
        status = status_for(absence)
        if not headcount:
            status = STATUS_DEGRADED
        return AbsenceRateCompanyByType(
            status=status,
            evidence_refs=evidence,
            missing_columns=missing_optional_columns(absence),
            notes=notes,
            period=period,
            working_days=working_days,
            headcount=headcount,
            total_days_workable=total_days,
            absence_rate=rate,
            types_count=as_int(totals["types_count"]),
            by_type=by_type,
            **base,
        )

    return await run_gold_aggregate(user, _compute, _unavailable)


__all__ = [
    "ABSENCE_DATASET",
    "ABSENCE_RATE_PROXY_NOTE",
    "AbsenceRateCompanyByType",
    "CONSOLE_SCOPE_PREDICATE",
    "DataFreshnessByCartridge",
    "FRESHNESS_PROXY_NOTE",
    "FRESHNESS_SLA_ENV",
    "HEADCOUNT_DATASET",
    "PipelineHealth",
    "open_console_scope",
    "query_absence_rate_company_by_type",
    "query_data_freshness_by_cartridge",
    "query_pipeline_health",
    "resolve_sla_hours",
    "run_console_aggregate",
]
