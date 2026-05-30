"""
HubSpot MCP Server
==================
Exposes tools over Streamable HTTP so that Claude (or any MCP client) can
inspect, extract, and query HubSpot CRM data without writing custom code.

Mount path: /mcp  (configured in main.py)
"""
from __future__ import annotations

from typing import Any
import re

from fastmcp import FastMCP

from app.core.config import settings
from app.core import job_runner
from app.services.catalog_service import get_all_entities, get_all_kbs, get_entity_config
from app.services.duckdb_service import run_kb_sql, _get_duckdb_connection
from app.services.extraction_service import run_entity
from app.services.kb_service import run_knowledge_bit, get_kb_runs
from app.services.watermark_service import get_watermark

mcp = FastMCP(
    name="hubspot",
    instructions=(
        "You have access to the HubSpot CRM cartridge. "
        "Use list_entities to discover what data is available, preview to inspect rows, "
        "extract to ingest data into Bronze storage, and query_kb for analytics."
    ),
)


# ── Tool 1: list_entities ─────────────────────────────────────────────────────

@mcp.tool()
def list_entities() -> list[dict[str, Any]]:
    """
    List all HubSpot entities with their extraction mode, watermark field,
    last recorded watermark value, and description.
    """
    entities = get_all_entities()
    result = []
    for e in entities:
        name = e.get("entity") or e.get("name", "")
        wf = e.get("watermark_field")
        result.append({
            "entity":           name,
            "mode":             e.get("mode", "full"),
            "watermark_field":  wf,
            "last_watermark":   get_watermark(name) if wf else None,
            "description":      e.get("description", ""),
        })
    return result


# ── Tool 2: get_schema ────────────────────────────────────────────────────────

@mcp.tool()
def get_schema(entity: str) -> dict[str, Any]:
    """
    Return the configuration schema for a HubSpot entity including the property
    list, watermark config, and extraction mode.

    Args:
        entity: Entity name as listed by list_entities (e.g. "deals", "companies")
    """
    config = get_entity_config(entity)
    if not config:
        return {"error": f"Entity '{entity}' not found"}
    return {
        "entity":           config.get("entity"),
        "mode":             config.get("mode"),
        "watermark_field":  config.get("watermark_field"),
        "watermark_format": config.get("watermark_format"),
        "page_size":        config.get("page_size"),
        "properties":       config.get("properties"),
        "api_path":         config.get("api_path"),
        "date_field":       config.get("date_field"),
        "description":      config.get("description"),
    }


# ── Tool 3: preview ───────────────────────────────────────────────────────────

@mcp.tool()
def preview(entity: str, limit: int = 20) -> dict[str, Any]:
    """
    Preview the most recent rows for a HubSpot entity from Bronze (MinIO Parquet).
    Returns column names and up to `limit` rows.

    Args:
        entity: Entity name (e.g. "deals", "companies")
        limit:  Maximum number of rows to return (default 20, max 200)
    """
    limit = min(limit, 200)
    bucket = settings.minio_bucket
    path = f"s3://{bucket}/raw/hubspot/{entity}/load_date=*/batch_id=*/*.parquet"
    sql = f"SELECT * FROM read_parquet('{path}', hive_partitioning=true, union_by_name=true) LIMIT {limit}"
    try:
        conn = _get_duckdb_connection()
        try:
            rel = conn.execute(sql)
            columns = [desc[0] for desc in rel.description]
            rows = rel.fetchall()
        finally:
            conn.close()
        return {
            "entity":  entity,
            "columns": columns,
            "rows":    [dict(zip(columns, r)) for r in rows],
            "count":   len(rows),
        }
    except Exception as exc:
        return {"entity": entity, "error": str(exc), "rows": [], "columns": []}


# ── Tool 4: extract (BATCH — returns immediately) ─────────────────────────────

@mcp.tool()
async def extract(
    entity: str,
    mode: str = "incremental",
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """
    [BATCH — async] Trigger extraction of a HubSpot entity into Bronze (MinIO Parquet).

    Returns IMMEDIATELY with a job_id. The extraction runs in the background.
    Use get_job_status(job_id) to poll progress, or list_jobs() to see all jobs.

    Args:
        entity:    Entity name (e.g. "deals", "companies")
        mode:      "full" | "incremental" | "historical" (default: incremental)
        from_date: ISO date — historical mode only (e.g. "2024-01-01")
        to_date:   ISO date — historical mode only (e.g. "2024-03-31")
    """
    config = get_entity_config(entity)
    if not config:
        return {"error": f"Entity '{entity}' not found"}
    overridden = dict(config)
    overridden["mode"] = mode
    return await job_runner.create_extract_job(overridden, from_date=from_date, to_date=to_date)


# ── Tool 4b: extract_all (BATCH — extrae todas las entidades) ────────────────

@mcp.tool()
async def extract_all(mode: str = "incremental") -> dict[str, Any]:
    """
    [BATCH — async] Extrae TODAS las entidades de HubSpot en paralelo (máx 4 simultáneas).

    Regresa INMEDIATAMENTE con un job_id. Usa get_job_status(job_id) para ver
    avance y el resultado final con detalle por entidad.

    Args:
        mode: "full" | "incremental" (default: incremental)
    """
    return await job_runner.create_extract_all_job(mode)


# ── Tool 4c: get_run_logs ─────────────────────────────────────────────────────

@mcp.tool()
async def get_run_logs(job_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """
    Obtiene los logs detallados de progreso de un job (extract o extract_all).

    Args:
        job_id: ID del job retornado por extract() o extract_all()
        limit:  Número de entradas a retornar (default 50, máx 200)
    """
    limit = min(limit, 200)
    pool = await job_runner._get_pool()
    rows = await pool.fetch(
        "SELECT entity, level, message, detail, ts "
        "FROM run_logs WHERE run_id=$1 AND cartridge='hubspot' "
        "ORDER BY ts ASC LIMIT $2",
        job_id, limit,
    )
    import json as _json
    result = []
    for row in rows:
        detail = row["detail"]
        if isinstance(detail, str):
            try:
                detail = _json.loads(detail)
            except Exception:
                pass
        result.append({
            "ts":      row["ts"].isoformat(),
            "entity":  row["entity"],
            "level":   row["level"],
            "message": row["message"],
            "detail":  detail,
        })
    return result


# ── Tool 5: get_job_status ────────────────────────────────────────────────────

@mcp.tool()
async def get_job_status(job_id: str) -> dict[str, Any]:
    """
    Get the current status and result of a background extraction job.

    Args:
        job_id: Short ID returned by extract() (e.g. "a3f2b1c0")
    """
    return await job_runner.get_job(job_id)


# ── Tool 5b: list_jobs ────────────────────────────────────────────────────────

@mcp.tool()
async def list_jobs(limit: int = 10) -> list[dict[str, Any]]:
    """
    List recent extraction jobs for this cartridge with their status and outcome.

    Args:
        limit: Number of jobs to return (default 10, max 50)
    """
    return await job_runner.list_jobs(limit)


# ── Tool 6: list_kbs ─────────────────────────────────────────────────────────

@mcp.tool()
def list_kbs() -> list[dict[str, Any]]:
    """
    List all Knowledge Bits defined for the HubSpot cartridge, including
    their description and output table.
    """
    kbs = get_all_kbs()
    return [
        {
            "kb_id":       kb.get("kb_id") or kb.get("id"),
            "name":        kb.get("name"),
            "description": kb.get("description"),
            "pg_table":    kb.get("pg_table"),
            "output_path": kb.get("output_path"),
        }
        for kb in kbs
    ]


# ── Tool 7: run_kb ────────────────────────────────────────────────────────────

@mcp.tool()
def run_kb(kb_id: str) -> dict[str, Any]:
    """
    Execute a Knowledge Bit: runs its SQL against Bronze Parquet data,
    writes results to Silver Parquet (MinIO) and PostgreSQL.

    Args:
        kb_id: Knowledge Bit ID as listed by list_kbs()
    """
    try:
        return run_knowledge_bit(kb_id)
    except Exception as exc:
        return {"kb_id": kb_id, "status": "failed", "error": str(exc)}


# ── Tool 8: query_kb ─────────────────────────────────────────────────────────

@mcp.tool()
def query_kb(sql: str, limit: int = 100) -> dict[str, Any]:
    """
    Run arbitrary DuckDB SQL against HubSpot Bronze/Silver Parquet data.
    The query runs in-process via DuckDB with MinIO S3 access pre-configured.
    Use {bucket} as a placeholder for the MinIO bucket name.

    Args:
        sql:   DuckDB SQL query. Wrap table refs like:
               read_parquet('s3://{bucket}/raw/hubspot/deals/**/*.parquet')
        limit: Safety row cap applied if the query has no LIMIT clause (default 100)
    """
    limit = min(limit, 5000)
    stripped = (sql or "").strip()
    if not re.match(r"^(select|with)\b", stripped, flags=re.IGNORECASE):
        return {"error": "Only SELECT/WITH statements are allowed"}
    forbidden = re.search(
        r"\b(attach|copy|create|delete|drop|insert|install|load|pragma|update|alter|truncate)\b",
        stripped,
        flags=re.IGNORECASE,
    )
    if forbidden:
        return {"error": f"Forbidden SQL keyword: {forbidden.group(1).upper()}"}
    if ";" in stripped.rstrip(";"):
        return {"error": "Forbidden SQL statement separator"}
    resolved = sql.replace("{bucket}", settings.minio_bucket)
    if "limit" not in resolved.lower():
        resolved = f"SELECT * FROM ({resolved}) _q LIMIT {limit}"
    try:
        conn = _get_duckdb_connection()
        try:
            rel = conn.execute(resolved)
            columns = [desc[0] for desc in rel.description]
            rows = rel.fetchall()
        finally:
            conn.close()
        return {
            "columns": columns,
            "rows":    [dict(zip(columns, r)) for r in rows],
            "count":   len(rows),
        }
    except Exception as exc:
        return {"error": str(exc), "sql": resolved}


# ── Custom tools loader ───────────────────────────────────────────────────────

def _make_sql_tool(name: str, description: str, sql: str) -> None:
    """Register a SQL-query custom tool on the mcp instance."""
    resolved_sql = sql.replace("{bucket}", settings.minio_bucket)
    if "limit" not in resolved_sql.lower():
        resolved_sql = f"SELECT * FROM ({resolved_sql}) _q LIMIT 100"

    def _tool_fn() -> dict[str, Any]:
        conn = _get_duckdb_connection()
        try:
            rel = conn.execute(resolved_sql)
            columns = [d[0] for d in rel.description]
            rows = rel.fetchall()
        finally:
            conn.close()
        return {"columns": columns, "rows": [dict(zip(columns, r)) for r in rows], "count": len(rows)}

    _tool_fn.__name__ = name
    _tool_fn.__doc__ = description or f"Custom SQL tool: {name}"
    mcp.add_tool(_tool_fn)


def _make_extract_tool(name: str, description: str, entity: str, mode: str) -> None:
    """Register an entity-extract custom tool on the mcp instance."""
    def _tool_fn() -> dict[str, Any]:
        config = get_entity_config(entity)
        if not config:
            return {"error": f"Entity '{entity}' not found"}
        overridden = dict(config)
        overridden["mode"] = mode
        try:
            return run_entity(overridden)
        except Exception as exc:
            return {"entity": entity, "status": "failed", "error": str(exc)}

    _tool_fn.__name__ = name
    _tool_fn.__doc__ = description or f"Extract {entity} ({mode})"
    mcp.add_tool(_tool_fn)


def _make_kb_tool(name: str, description: str, kb_id: str) -> None:
    """Register a Knowledge Bit runner custom tool on the mcp instance."""
    def _tool_fn() -> dict[str, Any]:
        try:
            return run_knowledge_bit(kb_id)
        except Exception as exc:
            return {"kb_id": kb_id, "status": "failed", "error": str(exc)}

    _tool_fn.__name__ = name
    _tool_fn.__doc__ = description or f"Run Knowledge Bit: {kb_id}"
    mcp.add_tool(_tool_fn)


def load_custom_tools() -> int:
    """
    Load custom tool definitions from mcp_custom_tools in PostgreSQL
    and register them on the mcp instance. Returns the count loaded.
    """
    try:
        from app.core.pg_client import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, description, tool_type, config FROM mcp_custom_tools "
                    "WHERE cartridge_id='hubspot' AND enabled=TRUE"
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return 0

    loaded = 0
    for name, description, tool_type, config in rows:
        try:
            cfg = config if isinstance(config, dict) else {}
            if tool_type == "sql_query":
                sql = cfg.get("sql", "")
                if sql:
                    _make_sql_tool(name, description, sql)
                    loaded += 1
            elif tool_type == "extract":
                entity = cfg.get("entity", "")
                mode = cfg.get("mode", "incremental")
                if entity:
                    _make_extract_tool(name, description, entity, mode)
                    loaded += 1
            elif tool_type == "run_kb":
                kb_id = cfg.get("kb_id", "")
                if kb_id:
                    _make_kb_tool(name, description, kb_id)
                    loaded += 1
        except Exception:
            continue
    return loaded


# Load custom tools at module import time (before main.py calls http_app())
load_custom_tools()
