"""
Generic cartridge tools — replace the per-cartridge container approach.

Every tool here accepts cartridge_id as a parameter and reads its configuration
from Postgres tables (entity_config, kb_config, entity_watermarks, pipeline_runs,
run_logs, mcp_custom_tools).

Phase 1: tools live alongside replicon's own MCP server. Phase 2 wires them as
virtual servers in the console registry. Phase 6 deletes the replicon container.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pandas as pd
from sqlalchemy import create_engine, text

from app.config import settings
from app.registry import tool
from app.tools.postgres import _conn


# ── DuckDB helper (S3 pre-configured) ─────────────────────────────────────────

def _duckdb() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    try:
        conn.execute("LOAD httpfs;")
    except Exception:
        conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute(f"SET s3_endpoint='{settings.minio_endpoint}';")
    conn.execute(f"SET s3_access_key_id='{settings.minio_access_key}';")
    conn.execute(f"SET s3_secret_access_key='{settings.minio_secret_key}';")
    conn.execute(f"SET s3_use_ssl={'true' if settings.minio_secure else 'false'};")
    conn.execute("SET s3_url_style='path';")
    return conn


def _bronze_path(cartridge_id: str, entity: str) -> str:
    return f"s3://{settings.minio_bucket}/raw/{cartridge_id}/{entity}/load_date=*/batch_id=*/*.parquet"


def _airflow_auth() -> tuple[str, str]:
    return (settings.airflow_user, settings.airflow_password)


# ── Tool · get_semantic ───────────────────────────────────────────────────────

@tool(
    name="cartridge_get_semantic",
    description=(
        "Return the business vocabulary (semantic_terms) for a cartridge. "
        "Each term has its definition and the technical column/dataset it maps to. "
        "Call this when the user asks about meaning of business terms or when "
        "writing SQL that should use natural language."
    ),
    input_schema={
        "type": "object",
        "properties": {"cartridge_id": {"type": "string"}},
        "required": ["cartridge_id"],
    },
)
def cartridge_get_semantic(cartridge_id: str) -> dict[str, Any]:
    """
    Returns business vocabulary from BOTH sources:
      - semantic_terms     (manually curated business glossary)
      - data_catalog       (per-column descriptions on Gold/Silver datasets owned by the cartridge)
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT term, definition, maps_to FROM semantic_terms "
            "WHERE cartridge_id=%s ORDER BY term",
            (cartridge_id,),
        )
        terms = [
            {"term": r[0], "definition": r[1], "maps_to": r[2]}
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT dataset, column_name, data_type, description, tags, is_metric "
            "FROM data_catalog WHERE cartridge=%s AND description IS NOT NULL "
            "ORDER BY dataset, column_name",
            (cartridge_id,),
        )
        columns = [
            {
                "dataset":     r[0],
                "column":      r[1],
                "type":        r[2],
                "description": r[3],
                "tags":        list(r[4] or []),
                "is_metric":   r[5],
            }
            for r in cur.fetchall()
        ]
    return {"semantic_terms": terms, "data_catalog": columns}


# ── Tool · sync_semantic_to_rag ───────────────────────────────────────────────

@tool(
    name="cartridge_sync_semantic_to_rag",
    description=(
        "Re-build the RAG knowledge base for a cartridge from its semantic_terms "
        "+ data_catalog. Run this after editing the vocabulary so semantic search "
        "stays fresh. Replaces any prior auto-synced source for the cartridge."
    ),
    input_schema={
        "type": "object",
        "properties": {"cartridge_id": {"type": "string"}},
        "required": ["cartridge_id"],
    },
)
async def cartridge_sync_semantic_to_rag(cartridge_id: str) -> dict[str, Any]:
    docs: list[str] = []
    with _conn() as c, c.cursor() as cur:
        # 1. Glossary terms — one doc each (few)
        cur.execute(
            "SELECT term, definition, maps_to FROM semantic_terms "
            "WHERE cartridge_id=%s",
            (cartridge_id,),
        )
        for term, definition, maps_to in cur.fetchall():
            docs.append(
                f"[Cartucho: {cartridge_id} · Término de glosario: {term}]\n"
                f"Definición: {definition or '(sin definición)'}\n"
                f"Maps to: {maps_to or '(no mapeado)'}"
            )

        # 2. Data catalog grouped by dataset — one doc per dataset with all its
        # documented columns. Keeps count low and preserves column-relation context.
        cur.execute(
            "SELECT dataset, column_name, data_type, description, tags "
            "FROM data_catalog "
            "WHERE cartridge=%s AND description IS NOT NULL "
            "ORDER BY dataset, column_name",
            (cartridge_id,),
        )
        rows_by_ds: dict[str, list[tuple]] = {}
        for ds, col, dtype, desc, tags in cur.fetchall():
            rows_by_ds.setdefault(ds, []).append((col, dtype, desc, tags))

        for ds, cols in rows_by_ds.items():
            lines = [f"[Cartucho: {cartridge_id} · Dataset: {ds}]"]
            for col, dtype, desc, tags in cols:
                tags_str = (", ".join(tags or []) if tags else "")
                tail = f" [tags: {tags_str}]" if tags_str else ""
                lines.append(f"- {col} ({dtype or '?'}): {desc}{tail}")
            docs.append("\n".join(lines))

    if not docs:
        return {"cartridge_id": cartridge_id, "documents": 0,
                "skipped": "no semantic data to sync"}

    content = "\n\n---\n\n".join(docs)
    source_name = f"_semantic_{cartridge_id}"

    # Delete the previous auto-synced source if present
    from app.rag.store import list_sources, delete_source
    for s in await list_sources():
        if s.get("name") == source_name:
            await delete_source(s["id"])
            break

    # Ingest fresh content
    from app.tools.rag import _do_ingest
    result = await _do_ingest(
        name=source_name,
        content=content,
        description=f"Auto-synced semantic model for cartridge {cartridge_id}",
        mime_type="text/plain",
    )
    return {
        "cartridge_id":  cartridge_id,
        "source_name":   source_name,
        "documents":     len(docs),
        "chunks_parent": result.get("parents") if isinstance(result, dict) else None,
        "chunks_child":  result.get("children") if isinstance(result, dict) else None,
    }


# ── Tool · search_term (fuzzy lookup across both glossaries) ─────────────────

@tool(
    name="cartridge_search_term",
    description=(
        "Fuzzy search a business term in BOTH semantic_terms (curated glossary) "
        "AND data_catalog (per-column descriptions). Use when the user asks "
        "what something means and you don't know if it's a business concept, "
        "a column or both."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "query":        {"type": "string", "description": "Word or phrase to search"},
        },
        "required": ["cartridge_id", "query"],
    },
)
async def cartridge_search_term(cartridge_id: str, query: str) -> dict[str, Any]:
    """
    Hybrid lookup:
      1. SQL ILIKE over semantic_terms + data_catalog (exact-ish matches).
      2. Vector search in RAG over the auto-synced semantic source for the cartridge
         (catches paraphrases, synonyms, cross-language).
    """
    # Normalize: try with both spaces and underscores so 'costo hundido' matches 'costo_hundido'
    p_space = f"%{query}%"
    p_under = f"%{query.replace(' ', '_')}%"
    p_dash  = f"%{query.replace(' ', '-')}%"
    tag     = query.lower().replace(" ", "_")
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT term, definition, maps_to FROM semantic_terms "
            "WHERE cartridge_id=%s AND ("
            "  term ILIKE %s OR term ILIKE %s OR "
            "  definition ILIKE %s OR maps_to ILIKE %s) "
            "ORDER BY term",
            (cartridge_id, p_space, p_under, p_space, p_space),
        )
        terms = [
            {"term": r[0], "definition": r[1], "maps_to": r[2]}
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT dataset, column_name, data_type, description "
            "FROM data_catalog WHERE cartridge=%s AND ("
            "  column_name ILIKE %s OR column_name ILIKE %s OR column_name ILIKE %s OR "
            "  description ILIKE %s OR description ILIKE %s OR "
            "  %s = ANY(tags)) "
            "ORDER BY dataset, column_name",
            (cartridge_id, p_space, p_under, p_dash, p_space, p_under, tag),
        )
        columns = [
            {"dataset": r[0], "column": r[1], "type": r[2], "description": r[3]}
            for r in cur.fetchall()
        ]

    # Vector fallback against the auto-synced RAG source for this cartridge
    rag_results: list[dict] = []
    try:
        from app.rag.store import list_sources
        from app.tools.rag import _do_search
        target_name = f"_semantic_{cartridge_id}"
        for s in await list_sources():
            if s.get("name") == target_name:
                rag_results = await _do_search(query=query, top_k=5, source_ids=[s["id"]])
                break
    except Exception as exc:
        rag_results = [{"error": str(exc)}]

    return {
        "query": query,
        "matches_in_semantic_terms": terms,
        "matches_in_data_catalog":   columns,
        "matches_in_rag":            rag_results,
        "rag_synced": any(s.get("name") == f"_semantic_{cartridge_id}"
                          for s in (await _safe_list_rag_sources())),
    }


async def _safe_list_rag_sources() -> list[dict]:
    try:
        from app.rag.store import list_sources
        return await list_sources()
    except Exception:
        return []


# ── Tool · get_manifest ───────────────────────────────────────────────────────

@tool(
    name="cartridge_get_manifest",
    description=(
        "Return the full manifest of a cartridge: header, entities, DAGs, "
        "Knowledge Bits, semantic vocabulary, custom tools and analytic apps. "
        "Heavy — use only when you need a complete view."
    ),
    input_schema={
        "type": "object",
        "properties": {"cartridge_id": {"type": "string"}},
        "required": ["cartridge_id"],
    },
)
def cartridge_get_manifest(cartridge_id: str) -> dict[str, Any]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, name, version, description, pattern, category, bronze_path "
            "FROM cartridges WHERE id=%s",
            (cartridge_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"error": f"Cartridge '{cartridge_id}' not found"}
        header = {
            "id": row[0], "name": row[1], "version": row[2],
            "description": row[3], "pattern": row[4],
            "category": row[5], "bronze_path": row[6],
        }
        cur.execute(
            "SELECT conn_id, description, auth_type, poll_strategy "
            "FROM cartridge_connections WHERE cartridge_id=%s ORDER BY conn_id",
            (cartridge_id,),
        )
        connections = [
            {"conn_id": r[0], "description": r[1], "auth_type": r[2], "poll_strategy": r[3]}
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT dag_id, file, description, trigger FROM cartridge_dags "
            "WHERE cartridge_id=%s ORDER BY dag_id",
            (cartridge_id,),
        )
        dags = [
            {"dag_id": r[0], "file": r[1], "description": r[2], "trigger": r[3]}
            for r in cur.fetchall()
        ]
    return {**header, "connections": connections, "dags": dags}


# ── Tool 0 · list_cartridges (discovery) ──────────────────────────────────────

@tool(
    name="list_cartridges",
    description=(
        "List all installed cartridges with id, name, category and entity count. "
        "ALWAYS call this first when the user asks about a cartridge by name and "
        "you need its cartridge_id for other cartridge_* tools."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
)
def list_cartridges() -> list[dict[str, Any]]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.name, c.version, c.category, c.description,
                   COUNT(e.entity) FILTER (WHERE e.enabled = TRUE) AS entities
            FROM cartridges c
            LEFT JOIN entity_config e ON e.cartridge_id = c.id
            GROUP BY c.id, c.name, c.version, c.category, c.description
            ORDER BY c.name
            """
        )
        rows = cur.fetchall()
    return [
        {
            "id":          r[0],
            "name":        r[1],
            "version":     r[2],
            "category":    r[3],
            "description": (r[4] or "").strip(),
            "entities":    r[5] or 0,
        }
        for r in rows
    ]


# ── Tool 1 · list_entities ────────────────────────────────────────────────────

@tool(
    name="cartridge_list_entities",
    description=(
        "List all enabled entities for a cartridge with their extraction mode, "
        "watermark field, last recorded watermark value and description. "
        "If you don't know the cartridge_id, call list_cartridges first."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string", "description": "e.g. 'replicon'"},
        },
        "required": ["cartridge_id"],
    },
)
def cartridge_list_entities(cartridge_id: str) -> list[dict[str, Any]]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT e.entity, e.mode, e.watermark_field, e.description,
                   w.last_watermark_value
            FROM entity_config e
            LEFT JOIN entity_watermarks w
              ON w.cartridge_id = e.cartridge_id AND w.entity_name = e.entity
            WHERE e.cartridge_id = %s AND e.enabled = TRUE
            ORDER BY e.entity
            """,
            (cartridge_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "entity":          r[0],
            "mode":            r[1] or "full",
            "watermark_field": r[2],
            "description":     r[3] or "",
            "last_watermark":  r[4],
        }
        for r in rows
    ]


# ── Tool 2 · get_schema ───────────────────────────────────────────────────────

@tool(
    name="cartridge_get_schema",
    description=(
        "Return the configuration schema for a single entity in a cartridge: "
        "fields list, watermark config, page size, extraction mode."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "entity":       {"type": "string", "description": "Entity name as listed by cartridge_list_entities"},
        },
        "required": ["cartridge_id", "entity"],
    },
)
def cartridge_get_schema(cartridge_id: str, entity: str) -> dict[str, Any]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT entity, mode, watermark_field, watermark_format, page_size,
                   select_fields, effective_dated, date_field, primary_key,
                   dag_id, description
            FROM entity_config
            WHERE cartridge_id = %s AND entity = %s
            """,
            (cartridge_id, entity),
        )
        row = cur.fetchone()
    if not row:
        return {"error": f"Entity '{entity}' not found in cartridge '{cartridge_id}'"}
    return {
        "entity":           row[0],
        "mode":             row[1],
        "watermark_field":  row[2],
        "watermark_format": row[3],
        "page_size":        row[4],
        "select_fields":    row[5],
        "effective_dated":  row[6],
        "date_field":       row[7],
        "primary_key":      row[8],
        "dag_id":           row[9],
        "description":      row[10],
    }


# ── Tool 3 · preview ──────────────────────────────────────────────────────────

@tool(
    name="cartridge_preview",
    description=(
        "Preview the most recent rows of a cartridge entity from Bronze "
        "(MinIO Parquet). Returns column names and up to `limit` rows."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "entity":       {"type": "string"},
            "limit":        {"type": "integer", "default": 20, "description": "Max 200"},
        },
        "required": ["cartridge_id", "entity"],
    },
)
def cartridge_preview(cartridge_id: str, entity: str, limit: int = 20) -> dict[str, Any]:
    limit = min(limit, 200)
    path  = _bronze_path(cartridge_id, entity)
    sql   = f"SELECT * FROM read_parquet('{path}', hive_partitioning=true) LIMIT {limit}"
    try:
        conn = _duckdb()
        try:
            rel = conn.execute(sql)
            columns = [desc[0] for desc in rel.description]
            rows    = rel.fetchall()
        finally:
            conn.close()
        return {
            "cartridge_id": cartridge_id,
            "entity":       entity,
            "columns":      columns,
            "rows":         [dict(zip(columns, r)) for r in rows],
            "count":        len(rows),
        }
    except Exception as exc:
        return {"cartridge_id": cartridge_id, "entity": entity,
                "error": str(exc), "rows": [], "columns": []}


# ── Tool 4 · extract (one entity) ─────────────────────────────────────────────

@tool(
    name="cartridge_extract",
    description=(
        "Trigger extraction of one entity by firing its Airflow DAG. "
        "Returns dag_run_id and a generated run_id immediately. Use "
        "cartridge_get_job_status(run_id) to poll progress."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "entity":       {"type": "string"},
            "mode":         {"type": "string", "enum": ["full", "incremental", "historical"],
                             "default": "incremental"},
            "from_date":    {"type": "string", "description": "ISO date — historical mode only"},
            "to_date":      {"type": "string", "description": "ISO date — historical mode only"},
        },
        "required": ["cartridge_id", "entity"],
    },
)
async def cartridge_extract(
    cartridge_id: str, entity: str,
    mode: str = "incremental",
    from_date: str | None = None,
    to_date:   str | None = None,
) -> dict[str, Any]:
    # Look up the DAG bound to this entity
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT dag_id FROM entity_config WHERE cartridge_id=%s AND entity=%s",
            (cartridge_id, entity),
        )
        row = cur.fetchone()
    if not row or not row[0]:
        return {"error": f"No dag_id configured for {cartridge_id}.{entity}"}
    dag_id = row[0]
    run_id = uuid.uuid4().hex[:8]

    conf = {"run_id": run_id, "entity": entity, "mode": mode}
    if from_date:
        conf["from_date"] = from_date
    if to_date:
        conf["to_date"] = to_date

    async with httpx.AsyncClient(auth=_airflow_auth(), timeout=30) as client:
        r = await client.post(
            f"{settings.airflow_url.rstrip('/')}/api/v1/dags/{dag_id}/dagRuns",
            json={"conf": conf},
        )
        r.raise_for_status()
        data = r.json()

    return {
        "run_id":        run_id,
        "dag_id":        dag_id,
        "dag_run_id":    data["dag_run_id"],
        "state":         data["state"],
        "cartridge_id":  cartridge_id,
        "entity":        entity,
        "mode":          mode,
    }


# ── Tool 5 · extract_all ──────────────────────────────────────────────────────

@tool(
    name="cartridge_extract_all",
    description=(
        "Trigger extraction of every enabled entity in a cartridge. "
        "Each entity's DAG is fired in parallel (Airflow handles concurrency)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "mode":         {"type": "string", "enum": ["full", "incremental"],
                             "default": "incremental"},
        },
        "required": ["cartridge_id"],
    },
)
async def cartridge_extract_all(cartridge_id: str, mode: str = "incremental") -> dict[str, Any]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT entity, dag_id FROM entity_config "
            "WHERE cartridge_id=%s AND enabled=TRUE AND dag_id IS NOT NULL",
            (cartridge_id,),
        )
        rows = cur.fetchall()
    if not rows:
        return {"error": f"No entities with dag_id configured for '{cartridge_id}'"}

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(auth=_airflow_auth(), timeout=30) as client:
        for entity, dag_id in rows:
            run_id = uuid.uuid4().hex[:8]
            try:
                r = await client.post(
                    f"{settings.airflow_url.rstrip('/')}/api/v1/dags/{dag_id}/dagRuns",
                    json={"conf": {"run_id": run_id, "entity": entity, "mode": mode}},
                )
                r.raise_for_status()
                results.append({
                    "entity":     entity,
                    "dag_id":     dag_id,
                    "run_id":     run_id,
                    "dag_run_id": r.json()["dag_run_id"],
                    "state":      "queued",
                })
            except Exception as exc:
                results.append({"entity": entity, "dag_id": dag_id, "error": str(exc)})

    return {"cartridge_id": cartridge_id, "mode": mode, "triggered": results}


# ── Tool 6 · get_run_logs ─────────────────────────────────────────────────────

@tool(
    name="cartridge_get_run_logs",
    description="Per-step logs for a run_id, ordered by timestamp ascending.",
    input_schema={
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "limit":  {"type": "integer", "default": 50, "description": "Max 200"},
        },
        "required": ["run_id"],
    },
)
def cartridge_get_run_logs(run_id: str, limit: int = 50) -> list[dict[str, Any]]:
    limit = min(limit, 200)
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT entity, level, message, detail, ts "
            "FROM run_logs WHERE run_id=%s "
            "ORDER BY ts ASC LIMIT %s",
            (run_id, limit),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        detail = r[3]
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                pass
        out.append({
            "ts":      r[4].isoformat() if r[4] else None,
            "entity":  r[0],
            "level":   r[1],
            "message": r[2],
            "detail":  detail,
        })
    return out


# ── Tool 7 · get_job_status ───────────────────────────────────────────────────

@tool(
    name="cartridge_get_job_status",
    description="Current status and result of a pipeline run by run_id.",
    input_schema={
        "type": "object",
        "properties": {"run_id": {"type": "string"}},
        "required": ["run_id"],
    },
)
def cartridge_get_job_status(run_id: str) -> dict[str, Any]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT run_id, dag_id, cartridge_id, entity, mode, status,
                   started_at, finished_at, duration_seconds, record_count,
                   bytes_written, storage_uri, watermark_updated_to,
                   error_message, airflow_dag_run_id, extra
            FROM pipeline_runs WHERE run_id=%s
            """,
            (run_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"error": f"Run '{run_id}' not found"}
    return {
        "run_id":               row[0],
        "dag_id":               row[1],
        "cartridge_id":         row[2],
        "entity":               row[3],
        "mode":                 row[4],
        "status":               row[5],
        "started_at":           row[6].isoformat() if row[6] else None,
        "finished_at":          row[7].isoformat() if row[7] else None,
        "duration_seconds":     float(row[8]) if row[8] is not None else None,
        "record_count":         row[9],
        "bytes_written":        row[10],
        "storage_uri":          row[11],
        "watermark_updated_to": row[12],
        "error_message":        row[13],
        "airflow_dag_run_id":   row[14],
        "extra":                row[15],
    }


# ── Tool 8 · list_jobs ────────────────────────────────────────────────────────

@tool(
    name="cartridge_list_jobs",
    description="Recent pipeline runs for a cartridge, newest first.",
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "limit":        {"type": "integer", "default": 10, "description": "Max 50"},
        },
        "required": ["cartridge_id"],
    },
)
def cartridge_list_jobs(cartridge_id: str, limit: int = 10) -> list[dict[str, Any]]:
    limit = min(limit, 50)
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT run_id, dag_id, entity, mode, status,
                   started_at, finished_at, record_count, error_message
            FROM pipeline_runs
            WHERE cartridge_id=%s
            ORDER BY started_at DESC NULLS LAST
            LIMIT %s
            """,
            (cartridge_id, limit),
        )
        rows = cur.fetchall()
    return [
        {
            "run_id":        r[0],
            "dag_id":        r[1],
            "entity":        r[2],
            "mode":          r[3],
            "status":        r[4],
            "started_at":    r[5].isoformat() if r[5] else None,
            "finished_at":   r[6].isoformat() if r[6] else None,
            "record_count":  r[7],
            "error_message": r[8],
        }
        for r in rows
    ]


# ── Tool 9 · list_kbs ─────────────────────────────────────────────────────────

@tool(
    name="cartridge_list_kbs",
    description="List Knowledge Bits defined for a cartridge.",
    input_schema={
        "type": "object",
        "properties": {"cartridge_id": {"type": "string"}},
        "required": ["cartridge_id"],
    },
)
def cartridge_list_kbs(cartridge_id: str) -> list[dict[str, Any]]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT kb_id, name, description, pg_table, output_path "
            "FROM kb_config WHERE cartridge_id=%s AND enabled=TRUE ORDER BY kb_id",
            (cartridge_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "kb_id":       r[0],
            "name":        r[1],
            "description": r[2],
            "pg_table":    r[3],
            "output_path": r[4],
        }
        for r in rows
    ]


# ── Tool 10 · run_kb ──────────────────────────────────────────────────────────

@tool(
    name="cartridge_run_kb",
    description=(
        "Execute a Knowledge Bit: runs its SQL via DuckDB against Bronze "
        "Parquet, writes results to Silver Parquet (MinIO) and Postgres."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "kb_id":        {"type": "string", "description": "ID listed by cartridge_list_kbs"},
        },
        "required": ["cartridge_id", "kb_id"],
    },
)
def cartridge_run_kb(cartridge_id: str, kb_id: str) -> dict[str, Any]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT sql, pg_table, output_path FROM kb_config "
            "WHERE cartridge_id=%s AND kb_id=%s AND enabled=TRUE",
            (cartridge_id, kb_id),
        )
        row = cur.fetchone()
    if not row:
        return {"error": f"Knowledge Bit '{kb_id}' not found in cartridge '{cartridge_id}'"}

    sql, pg_table, output_path = row
    sql = sql.replace("{bucket}", settings.minio_bucket)
    run_id = uuid.uuid4().hex[:8]

    try:
        conn = _duckdb()
        try:
            df: pd.DataFrame = conn.execute(sql).df()
        finally:
            conn.close()
    except Exception as exc:
        return {"kb_id": kb_id, "status": "failed", "error": str(exc)}

    storage_uri = None
    if output_path:
        try:
            from minio import Minio
            mc = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
            load_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            key = f"{output_path}/load_date={load_date}/batch_id={run_id}/{kb_id}.parquet"
            with tempfile.TemporaryDirectory() as tmp:
                local = Path(tmp) / f"{kb_id}.parquet"
                df.to_parquet(local, index=False, engine="pyarrow", compression="snappy")
                mc.fput_object(settings.minio_bucket, key, str(local))
            storage_uri = f"s3://{settings.minio_bucket}/{key}"
        except Exception as exc:
            return {"kb_id": kb_id, "status": "partial",
                    "error": f"DuckDB ok but MinIO write failed: {exc}",
                    "rows": len(df)}

    if pg_table:
        try:
            url = (
                f"postgresql+psycopg2://{settings.pg_user}:{settings.pg_password}"
                f"@{settings.pg_host}:{settings.pg_port}/{settings.pg_db}"
            )
            engine = create_engine(url)
            try:
                with engine.begin() as conn:
                    conn.execute(text("CREATE SCHEMA IF NOT EXISTS knowledge_bits"))
                df.to_sql(pg_table, engine, schema="knowledge_bits",
                          if_exists="replace", index=False)
            finally:
                engine.dispose()
        except Exception as exc:
            return {"kb_id": kb_id, "status": "partial",
                    "error": f"Parquet ok but Postgres write failed: {exc}",
                    "rows": len(df), "storage_uri": storage_uri}

    return {
        "kb_id":        kb_id,
        "cartridge_id": cartridge_id,
        "status":       "success",
        "rows":         len(df),
        "storage_uri":  storage_uri,
        "pg_table":     f"knowledge_bits.{pg_table}" if pg_table else None,
    }


# ── Tool 11 · query_kb ────────────────────────────────────────────────────────

@tool(
    name="cartridge_query_kb",
    description=(
        "Run an arbitrary DuckDB SQL query against a cartridge's Bronze/Silver "
        "Parquet data. Use {bucket} as a placeholder for the MinIO bucket. "
        "A LIMIT is auto-injected if the query doesn't have one."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string", "description": "Used for context only — SQL must reference paths explicitly"},
            "sql":          {"type": "string",
                             "description": "Wrap table refs like read_parquet('s3://{bucket}/raw/<cart>/<entity>/**/*.parquet')"},
            "limit":        {"type": "integer", "default": 100, "description": "Max 5000"},
        },
        "required": ["cartridge_id", "sql"],
    },
)
def cartridge_query_kb(cartridge_id: str, sql: str, limit: int = 100) -> dict[str, Any]:
    limit = min(limit, 5000)
    resolved = sql.replace("{bucket}", settings.minio_bucket)
    if "limit" not in resolved.lower():
        resolved = f"SELECT * FROM ({resolved}) _q LIMIT {limit}"
    try:
        conn = _duckdb()
        try:
            rel = conn.execute(resolved)
            columns = [d[0] for d in rel.description]
            rows    = rel.fetchall()
        finally:
            conn.close()
        return {
            "cartridge_id": cartridge_id,
            "columns":      columns,
            "rows":         [dict(zip(columns, r)) for r in rows],
            "count":        len(rows),
        }
    except Exception as exc:
        return {"cartridge_id": cartridge_id, "error": str(exc), "sql": resolved}
