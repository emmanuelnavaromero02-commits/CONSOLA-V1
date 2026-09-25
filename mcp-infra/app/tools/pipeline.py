from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import HTTPException

from app.config import settings
from app.pipeline_run_transitions import (
    postgres_monotonic_status,
    postgres_status_accepts,
)
from app.tools.postgres import _conn
from app.registry import tool


_DAG_ID_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_CARTRIDGE_ID_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


def _validate_dag_id(dag_id: str) -> str:
    value = (dag_id or "").strip()
    if not _DAG_ID_RE.fullmatch(value):
        raise ValueError("invalid dag_id")
    return value


def _validate_cartridge_id(cartridge_id: str) -> str:
    value = (cartridge_id or "").strip()
    if not _CARTRIDGE_ID_RE.fullmatch(value):
        raise ValueError("invalid cartridge_id")
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_candidate(root: Path, *parts: str) -> Path | None:
    candidate = (root / Path(*parts)).resolve()
    return candidate if _inside(candidate, root) else None


def _dag_source_candidates(cartridge_id: str, dag_id: str) -> list[tuple[str, Path]]:
    dags_root = Path(settings.airflow_dags_path).resolve()
    registry_root = Path("/registry/cartridges").resolve()
    candidates: list[tuple[str, Path]] = []

    for label, candidate in (
        (
            "registry",
            _safe_candidate(registry_root, cartridge_id, "dags", f"{dag_id}.py"),
        ),
        (
            "airflow_cartridge",
            _safe_candidate(dags_root, cartridge_id, f"{dag_id}.py"),
        ),
        (
            "airflow",
            _safe_candidate(dags_root, f"{dag_id}.py"),
        ),
    ):
        if candidate is not None:
            candidates.append((label, candidate))
    return candidates


def _db_save_source(cur, cartridge_id: str, dag_id: str, source_code: str, file_name: str | None = None) -> None:
    cur.execute(
        """INSERT INTO cartridge_dags (cartridge_id, dag_id, file, source_code, updated_at)
           VALUES (%s, %s, COALESCE(%s, %s), %s, NOW())
           ON CONFLICT (cartridge_id, dag_id) DO UPDATE
           SET file = COALESCE(EXCLUDED.file, cartridge_dags.file),
               source_code = EXCLUDED.source_code,
               updated_at = NOW()""",
        (cartridge_id, dag_id, file_name, f"{dag_id}.py", source_code),
    )


def _dag_source_writes_enabled() -> bool:
    app_env = os.environ.get("APP_ENV", "production").strip().lower()
    rce = os.environ.get("ALLOW_RCE_TOOLS", "").strip().lower()
    return app_env in {"development", "dev", "local", "test"} and rce in {"true", "1", "yes", "on"}


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
def _watermark_scope(tenant_id: str | None = None, workspace_id: str | None = None) -> str:
    tenant = str(tenant_id or "").strip()
    workspace = str(workspace_id or "").strip()
    if tenant and workspace:
        return f"tenant:{tenant}:workspace:{workspace}"
    return "platform"


def _set_db_scope(cur, tenant_id: str | None = None, workspace_id: str | None = None) -> None:
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id or ""),))
    cur.execute("SELECT set_config('app.workspace_id', %s, true)", (str(workspace_id or ""),))
    cur.execute(
        "SELECT set_config('app.platform_admin', %s, true)",
        ("false" if tenant_id and workspace_id else "true",),
    )


def watermark_get(
    cartridge_id: str,
    entity: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    scope = _watermark_scope(tenant_id, workspace_id)
    with _conn() as conn, conn.cursor() as cur:
        _set_db_scope(cur, tenant_id, workspace_id)
        cur.execute(
            """SELECT watermark_field, last_watermark_value, last_run_id, updated_at
               FROM entity_watermarks
               WHERE cartridge_id = %s AND entity_name = %s AND watermark_scope = %s""",
            (cartridge_id, entity, scope),
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
def watermark_set(
    cartridge_id: str,
    entity: str,
    watermark_field: str,
    value: str,
    run_id: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    scope = _watermark_scope(tenant_id, workspace_id)
    with _conn() as conn, conn.cursor() as cur:
        _set_db_scope(cur, tenant_id, workspace_id)
        cur.execute(
            """INSERT INTO entity_watermarks
                   (cartridge_id, entity_name, watermark_field,
                    last_watermark_value, last_run_id, updated_at,
                    tenant_id, workspace_id, watermark_scope)
               VALUES (%s, %s, %s, %s, %s, NOW(), %s::uuid, %s::uuid, %s)
               ON CONFLICT (watermark_scope, cartridge_id, entity_name) DO UPDATE
               SET watermark_field       = EXCLUDED.watermark_field,
                   last_watermark_value  = EXCLUDED.last_watermark_value,
                   last_run_id           = EXCLUDED.last_run_id,
                   tenant_id             = EXCLUDED.tenant_id,
                   workspace_id          = EXCLUDED.workspace_id,
                   updated_at            = NOW()""",
            (cartridge_id, entity, watermark_field, value, run_id, tenant_id, workspace_id, scope),
        )
        conn.commit()
    return {"saved": True, "cartridge_id": cartridge_id, "entity": entity, "value": value}


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
        _set_db_scope(cur, tenant_id, workspace_id)
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
        canonical_run_id = str(run_id or "").strip()
        airflow_run = str(airflow_dag_run_id or "").strip()
        if canonical_run_id and airflow_run and canonical_run_id != airflow_run:
            lookup_sql = [
                "SELECT run_id",
                "  FROM pipeline_runs",
                " WHERE cartridge_id = %s",
                "   AND dag_id = %s",
                "   AND entity = %s",
                "   AND airflow_dag_run_id = %s",
            ]
            lookup_values: list[object] = [cartridge_id, dag_id, entity, airflow_run]
            if "tenant_id" in available and tenant_id:
                lookup_sql.append("   AND tenant_id = %s::uuid")
                lookup_values.append(tenant_id)
            if "workspace_id" in available and workspace_id:
                lookup_sql.append("   AND workspace_id = %s::uuid")
                lookup_values.append(workspace_id)
            lookup_sql.append(
                " ORDER BY CASE WHEN run_id = %s THEN 0 ELSE 1 END, started_at DESC NULLS LAST LIMIT 1"
            )
            lookup_values.append(airflow_run)
            cur.execute("\n".join(lookup_sql), tuple(lookup_values))
            existing = cur.fetchone()
            if existing and existing[0]:
                canonical_run_id = str(existing[0])
        run_id = canonical_run_id or run_id
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
        monotonic_status = postgres_monotonic_status(
            "pipeline_runs.status", "EXCLUDED.status"
        )
        accepts_status = postgres_status_accepts(
            "pipeline_runs.status", "EXCLUDED.status"
        )
        updates = [
            "airflow_dag_run_id   = COALESCE(EXCLUDED.airflow_dag_run_id, pipeline_runs.airflow_dag_run_id)",
            f"status               = {monotonic_status}",
            f"finished_at          = CASE WHEN {accepts_status} THEN COALESCE(EXCLUDED.finished_at, pipeline_runs.finished_at) ELSE pipeline_runs.finished_at END",
            f"duration_seconds     = CASE WHEN {accepts_status} THEN COALESCE(EXCLUDED.duration_seconds, pipeline_runs.duration_seconds) ELSE pipeline_runs.duration_seconds END",
            f"record_count         = CASE WHEN {accepts_status} THEN COALESCE(EXCLUDED.record_count, pipeline_runs.record_count) ELSE pipeline_runs.record_count END",
            f"bytes_written        = CASE WHEN {accepts_status} THEN COALESCE(EXCLUDED.bytes_written, pipeline_runs.bytes_written) ELSE pipeline_runs.bytes_written END",
            f"storage_uri          = CASE WHEN {accepts_status} THEN COALESCE(EXCLUDED.storage_uri, pipeline_runs.storage_uri) ELSE pipeline_runs.storage_uri END",
            f"watermark_updated_to = CASE WHEN {accepts_status} THEN COALESCE(EXCLUDED.watermark_updated_to, pipeline_runs.watermark_updated_to) ELSE pipeline_runs.watermark_updated_to END",
            f"error_message        = CASE WHEN {accepts_status} THEN COALESCE(EXCLUDED.error_message, pipeline_runs.error_message) ELSE pipeline_runs.error_message END",
            f"extra                = CASE WHEN {accepts_status} THEN COALESCE(pipeline_runs.extra, '{{}}'::jsonb) || EXCLUDED.extra ELSE pipeline_runs.extra END",
        ]
        for scoped_column in ("tenant_id", "workspace_id", "project_id"):
            if scoped_column in columns:
                updates.append(f"{scoped_column} = COALESCE(EXCLUDED.{scoped_column}, pipeline_runs.{scoped_column})")
        cur.execute(
            f"""INSERT INTO pipeline_runs ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT (run_id) DO UPDATE
                SET {", ".join(updates)}
                RETURNING status""",
            values,
        )
        saved_status = str(cur.fetchone()[0])
        conn.commit()
    return {
        "saved": True,
        "run_id": run_id,
        "status": saved_status,
        "requested_status": status,
        "status_advanced": saved_status == str(status),
    }


@tool(
    name="dag_save_source",
    description=(
        "Save or update the Python source code of a DAG. The Airflow-visible "
        "file is the source of truth; cartridge_dags.source_code is refreshed "
        "only after the file write succeeds."
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
    try:
        cartridge_id = _validate_cartridge_id(cartridge_id)
        dag_id = _validate_dag_id(dag_id)
    except ValueError as exc:
        return {"saved": False, "cartridge_id": cartridge_id, "dag_id": dag_id, "error": str(exc)}

    existing = [(label, path) for label, path in _dag_source_candidates(cartridge_id, dag_id) if path.exists()]
    target_label: str
    target_path: Path
    if existing:
        target_label, target_path = existing[0]
    else:
        dags_root = Path(settings.airflow_dags_path).resolve()
        candidate = _safe_candidate(dags_root, f"{dag_id}.py")
        if candidate is None:
            return {
                "saved": False,
                "cartridge_id": cartridge_id,
                "dag_id": dag_id,
                "error": "dag_id escapes dags directory",
            }
        target_label, target_path = "airflow", candidate

    if not _dag_source_writes_enabled():
        return {
            "saved": False,
            "cartridge_id": cartridge_id,
            "dag_id": dag_id,
            "path": str(target_path),
            "source": target_label,
            "error": (
                "dag_save_source refuses DB-only updates. Enable APP_ENV=development "
                "and ALLOW_RCE_TOOLS=true to write the Airflow DAG file."
            ),
        }
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(source_code, encoding="utf-8")
    except OSError as exc:
        return {
            "saved": False,
            "cartridge_id": cartridge_id,
            "dag_id": dag_id,
            "path": str(target_path),
            "source": target_label,
            "error": f"failed writing Airflow DAG file: {exc}",
        }

    with _conn() as conn, conn.cursor() as cur:
        _db_save_source(cur, cartridge_id, dag_id, source_code, target_path.name)
        conn.commit()
    return {"saved": True, "source": target_label, "path": str(target_path),
            "cartridge_id": cartridge_id, "dag_id": dag_id,
            "bytes": len(source_code.encode())}


@tool(
    name="dag_get_source",
    description=(
        "Retrieve the Airflow-visible Python source code of a DAG. "
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
    try:
        cartridge_id = _validate_cartridge_id(cartridge_id)
        dag_id = _validate_dag_id(dag_id)
    except ValueError as exc:
        return {"found": False, "cartridge_id": cartridge_id, "dag_id": dag_id,
                "error": str(exc)}

    for label, dag_path in _dag_source_candidates(cartridge_id, dag_id):
        if not dag_path.exists():
            continue
        source_code = dag_path.read_text(encoding="utf-8")
        with _conn() as conn, conn.cursor() as cur:
            _db_save_source(cur, cartridge_id, dag_id, source_code, dag_path.name)
            conn.commit()
        return {
            "found":        True,
            "source":       label,
            "path":         str(dag_path),
            "cartridge_id": cartridge_id,
            "dag_id":       dag_id,
            "source_code":  source_code,
        }

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
            "source":       "database_legacy_snapshot",
            "cartridge_id": cartridge_id,
            "dag_id":       dag_id,
            "source_code":  row[0],
            "updated_at":   row[1].isoformat() if row[1] else None,
        }

    return {"found": False, "cartridge_id": cartridge_id, "dag_id": dag_id}
