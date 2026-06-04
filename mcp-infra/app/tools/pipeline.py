"""
Pipeline MCP tools — watermark management and DAG source storage.

watermark_get   : returns the last watermark value for a cartridge/entity
watermark_set   : writes a new watermark value after a successful run
dag_save_source : persists DAG Python source to cartridge_dags.source_code
                  so the AI can retrieve and modify it later
"""
from __future__ import annotations

from fastapi import HTTPException

from app.tools.postgres import _conn
from app.registry import tool


# ── Watermark ─────────────────────────────────────────────────────────────────

@tool(
    name="watermark_get",
    description=(
        "Get the last watermark value for a cartridge+entity. "
        "Returns null if no previous run exists (meaning: do a full load)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string", "description": "e.g. 'replicon'"},
            "entity":       {"type": "string", "description": "e.g. 'TimeEntry'"},
        },
        "required": ["cartridge_id", "entity"],
    },
)
def watermark_get(cartridge_id: str, entity: str) -> dict:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT watermark_field, last_watermark_value, last_run_id, updated_at
               FROM entity_watermarks
               WHERE cartridge_id = %s AND entity_name = %s""",
            (cartridge_id, entity),
        )
        row = cur.fetchone()
    if not row:
        return {"cartridge_id": cartridge_id, "entity": entity,
                "watermark_field": None, "last_value": None, "last_run_id": None}
    return {
        "cartridge_id":  cartridge_id,
        "entity":        entity,
        "watermark_field": row[0],
        "last_value":    row[1],
        "last_run_id":   row[2],
        "updated_at":    row[3].isoformat() if row[3] else None,
    }


@tool(
    name="watermark_set",
    description=(
        "Persist a new watermark value for a cartridge+entity after a successful extraction. "
        "Creates the record if it does not exist."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id":    {"type": "string"},
            "entity":          {"type": "string"},
            "watermark_field": {"type": "string", "description": "Field name used as watermark, e.g. 'last_modified'"},
            "value":           {"type": "string", "description": "New max watermark value"},
            "run_id":          {"type": "string", "description": "DAG run_id for traceability"},
        },
        "required": ["cartridge_id", "entity", "watermark_field", "value", "run_id"],
    },
)
def watermark_set(cartridge_id: str, entity: str, watermark_field: str,
                  value: str, run_id: str) -> dict:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO entity_watermarks
                   (cartridge_id, entity_name, watermark_field,
                    last_watermark_value, last_run_id, updated_at)
               VALUES (%s, %s, %s, %s, %s, NOW())
               ON CONFLICT (cartridge_id, entity_name) DO UPDATE
               SET watermark_field       = EXCLUDED.watermark_field,
                   last_watermark_value  = EXCLUDED.last_watermark_value,
                   last_run_id           = EXCLUDED.last_run_id,
                   updated_at            = NOW()""",
            (cartridge_id, entity, watermark_field, value, run_id),
        )
        conn.commit()
    return {"saved": True, "cartridge_id": cartridge_id, "entity": entity, "value": value}


# ── Pipeline run log ──────────────────────────────────────────────────────────

@tool(
    name="pipeline_run_save",
    description=(
        "Write execution statistics for a DAG run to pipeline_runs table. "
        "Call this at the end of every DAG run (success or failure) to keep a "
        "unified history visible in Studio. "
        "Pass airflow_dag_run_id (the Airflow run_id string) so logs can be "
        "fetched precisely without guessing by date."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "run_id":               {"type": "string"},
            "dag_id":               {"type": "string"},
            "cartridge_id":         {"type": "string"},
            "entity":               {"type": "string"},
            "airflow_dag_run_id":   {"type": "string",
                                     "description": "Airflow run_id (e.g. manual__2024-01-15T…). "
                                                    "Use get_current_context()['run_id'] inside the task."},
            "mode":                 {"type": "string", "description": "full | incremental"},
            "status":               {"type": "string", "description": "success | failed | partial"},
            "started_at":           {"type": "string", "description": "ISO datetime"},
            "finished_at":          {"type": "string", "description": "ISO datetime"},
            "duration_seconds":     {"type": "number"},
            "record_count":         {"type": "integer"},
            "bytes_written":        {"type": "integer"},
            "storage_uri":          {"type": "string"},
            "watermark_updated_to": {"type": "string"},
            "error_message":        {"type": "string"},
            "tenant_id":            {"type": "string"},
            "workspace_id":         {"type": "string"},
            "project_id":           {"type": "string"},
            "extra":                {"type": "object", "description": "Any additional stats"},
        },
        "required": ["run_id", "dag_id", "cartridge_id", "entity", "status"],
    },
)
def pipeline_run_save(
    run_id: str, dag_id: str, cartridge_id: str, entity: str,
    airflow_dag_run_id: str = None,
    mode: str = None, status: str = "success",
    started_at: str = None, finished_at: str = None,
    duration_seconds: float = None, record_count: int = None,
    bytes_written: int = None, storage_uri: str = None,
    watermark_updated_to: str = None, error_message: str = None,
    tenant_id: str = None, workspace_id: str = None, project_id: str = None,
    extra: dict = None,
) -> dict:
    import json
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'pipeline_runs'
            """
        )
        available = {str(row[0]) for row in cur.fetchall()}
        if (
            "tenant_id" in available
            and "workspace_id" in available
            and str(cartridge_id or "").strip() != "platform"
            and (not str(tenant_id or "").strip() or not str(workspace_id or "").strip())
        ):
            raise HTTPException(403, "pipeline_run_save requires tenant_id and workspace_id")
        columns = [
            "run_id",
            "dag_id",
            "cartridge_id",
            "entity",
            "airflow_dag_run_id",
            "mode",
            "status",
            "started_at",
            "finished_at",
            "duration_seconds",
            "record_count",
            "bytes_written",
            "storage_uri",
            "watermark_updated_to",
            "error_message",
            "extra",
        ]
        values = [
            run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
            mode, status, started_at, finished_at, duration_seconds,
            record_count, bytes_written, storage_uri,
            watermark_updated_to, error_message,
            json.dumps(extra or {}),
        ]
        for scoped_column, scoped_value in (
            ("tenant_id", tenant_id),
            ("workspace_id", workspace_id),
            ("project_id", project_id),
        ):
            if scoped_column in available and scoped_value:
                columns.append(scoped_column)
                values.append(scoped_value)
        placeholders = ",".join(["%s"] * len(columns))
        updates = [
            "airflow_dag_run_id   = COALESCE(EXCLUDED.airflow_dag_run_id, pipeline_runs.airflow_dag_run_id)",
            "status               = EXCLUDED.status",
            "finished_at          = EXCLUDED.finished_at",
            "duration_seconds     = EXCLUDED.duration_seconds",
            "record_count         = EXCLUDED.record_count",
            "bytes_written        = EXCLUDED.bytes_written",
            "storage_uri          = EXCLUDED.storage_uri",
            "watermark_updated_to = EXCLUDED.watermark_updated_to",
            "error_message        = EXCLUDED.error_message",
            "extra                = pipeline_runs.extra || EXCLUDED.extra",
        ]
        for scoped_column in ("tenant_id", "workspace_id", "project_id"):
            if scoped_column in columns:
                updates.append(f"{scoped_column} = COALESCE(EXCLUDED.{scoped_column}, pipeline_runs.{scoped_column})")
        cur.execute(
            f"""INSERT INTO pipeline_runs ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT (run_id) DO UPDATE
                SET {", ".join(updates)}""",
            values,
        )
        conn.commit()
    return {"saved": True, "run_id": run_id, "status": status}


# ── DAG source storage ────────────────────────────────────────────────────────

@tool(
    name="dag_save_source",
    description=(
        "Save or update the Python source code of a DAG in cartridge_dags.source_code. "
        "Call this every time a DAG is created or modified so the AI can retrieve "
        "the current source when asked to make changes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string", "description": "e.g. 'replicon'"},
            "dag_id":       {"type": "string", "description": "DAG identifier"},
            "source_code":  {"type": "string", "description": "Complete Python source of the DAG"},
        },
        "required": ["cartridge_id", "dag_id", "source_code"],
    },
)
def dag_save_source(cartridge_id: str, dag_id: str, source_code: str) -> dict:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cartridge_dags (cartridge_id, dag_id, source_code, updated_at)
               VALUES (%s, %s, %s, NOW())
               ON CONFLICT (cartridge_id, dag_id) DO UPDATE
               SET source_code = EXCLUDED.source_code,
                   updated_at  = NOW()""",
            (cartridge_id, dag_id, source_code),
        )
        conn.commit()
    return {"saved": True, "cartridge_id": cartridge_id, "dag_id": dag_id,
            "bytes": len(source_code.encode())}


@tool(
    name="dag_get_source",
    description=(
        "Retrieve the stored Python source code of a DAG. "
        "Use this before modifying a DAG so the AI has the current version."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cartridge_id": {"type": "string"},
            "dag_id":       {"type": "string"},
        },
        "required": ["cartridge_id", "dag_id"],
    },
)
def dag_get_source(cartridge_id: str, dag_id: str) -> dict:
    from app.config import settings
    from pathlib import Path
    import re as _re

    # Validate dag_id up front — it's about to be used as a filesystem path.
    # Without this, dag_id="../../../etc/passwd" reaches read_text below.
    if not _re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", dag_id or ""):
        return {"found": False, "cartridge_id": cartridge_id, "dag_id": dag_id,
                "error": "invalid dag_id"}

    # 1. Try DB first
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source_code, updated_at FROM cartridge_dags "
            "WHERE cartridge_id = %s AND dag_id = %s",
            (cartridge_id, dag_id),
        )
        row = cur.fetchone()

    if row and row[0]:
        return {
            "found":        True,
            "source":       "database",
            "cartridge_id": cartridge_id,
            "dag_id":       dag_id,
            "source_code":  row[0],
            "updated_at":   row[1].isoformat() if row[1] else None,
        }

    # 2. Fallback: read from Airflow dags directory on disk
    dags_root = Path(settings.airflow_dags_path).resolve()
    dag_path = (dags_root / f"{dag_id}.py").resolve()
    # Defence in depth: even with the regex above, follow-symlinks resolution
    # guards against a misconfigured dags_path that points at a symlink farm.
    try:
        dag_path.relative_to(dags_root)
    except ValueError:
        return {"found": False, "cartridge_id": cartridge_id, "dag_id": dag_id,
                "error": "dag_id escapes dags directory"}
    if dag_path.exists():
        source_code = dag_path.read_text(encoding="utf-8")
        # Auto-save to DB so next call hits the cache
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO cartridge_dags (cartridge_id, dag_id, source_code, updated_at)
                   VALUES (%s, %s, %s, NOW())
                   ON CONFLICT (cartridge_id, dag_id) DO UPDATE
                   SET source_code = EXCLUDED.source_code, updated_at = NOW()""",
                (cartridge_id, dag_id, source_code),
            )
            conn.commit()
        return {
            "found":        True,
            "source":       "disk",
            "cartridge_id": cartridge_id,
            "dag_id":       dag_id,
            "source_code":  source_code,
        }

    return {"found": False, "cartridge_id": cartridge_id, "dag_id": dag_id}
