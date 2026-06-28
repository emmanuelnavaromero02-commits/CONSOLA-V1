from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from app.core.pg_client import get_connection
from app.core.request_context import scope_values

logger = logging.getLogger(__name__)
CARTRIDGE_DAG_ID = "sap_successfactors_extract"


def _pipeline_extra(
    *,
    entity_name: str,
    run_type: str,
    status: str,
    reason: str | None = None,
) -> str:
    payload = {
        "source": "extraction_runs_mirror",
        "functional_status": status,
        "reason": reason or status,
        "entity": entity_name,
        "mode": run_type,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _mirror_pipeline_run(
    run_id: str,
    *,
    status: str | None = None,
    records_extracted: int | None = None,
    storage_uri: str | None = None,
    error_message: str | None = None,
    finished_at: datetime | None = None,
) -> None:
    """Best-effort mirror so operational viewers do not depend on the MCP save path."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cartridge_id, entity_name, run_type, status, records_extracted,
                       storage_uri, error_message, started_at, finished_at,
                       tenant_id, workspace_id
                  FROM extraction_runs
                 WHERE run_id = %s
                """,
                (run_id,),
            )
            row = cur.fetchone()
            if not row:
                return
            (
                cartridge_id,
                entity_name,
                run_type,
                row_status,
                row_records,
                row_storage_uri,
                row_error_message,
                started_at,
                row_finished_at,
                tenant_id,
                workspace_id,
            ) = row
            final_status = status or row_status or "running"
            final_records = records_extracted if records_extracted is not None else row_records
            final_storage_uri = storage_uri if storage_uri is not None else row_storage_uri
            final_error = error_message if error_message is not None else row_error_message
            final_finished_at = finished_at if finished_at is not None else row_finished_at
            cur.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, dag_id, cartridge_id, entity, mode, status,
                    started_at, finished_at, record_count, storage_uri,
                    error_message, tenant_id, workspace_id, extra
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s::uuid, %s::uuid, %s::jsonb
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    dag_id = EXCLUDED.dag_id,
                    cartridge_id = EXCLUDED.cartridge_id,
                    entity = EXCLUDED.entity,
                    mode = EXCLUDED.mode,
                    status = EXCLUDED.status,
                    started_at = COALESCE(EXCLUDED.started_at, pipeline_runs.started_at),
                    finished_at = EXCLUDED.finished_at,
                    record_count = EXCLUDED.record_count,
                    storage_uri = EXCLUDED.storage_uri,
                    error_message = EXCLUDED.error_message,
                    tenant_id = COALESCE(EXCLUDED.tenant_id, pipeline_runs.tenant_id),
                    workspace_id = COALESCE(EXCLUDED.workspace_id, pipeline_runs.workspace_id),
                    extra = COALESCE(pipeline_runs.extra, '{}'::jsonb) || EXCLUDED.extra
                """,
                (
                    run_id,
                    CARTRIDGE_DAG_ID,
                    cartridge_id,
                    entity_name,
                    run_type,
                    final_status,
                    started_at,
                    final_finished_at,
                    final_records,
                    final_storage_uri,
                    final_error[:4000] if final_error else None,
                    tenant_id,
                    workspace_id,
                    _pipeline_extra(
                        entity_name=entity_name,
                        run_type=run_type,
                        status=final_status,
                        reason="error" if final_error else final_status,
                    ),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.warning("Could not mirror extraction_run %s into pipeline_runs", run_id, exc_info=True)
    finally:
        conn.close()


def create_run(
    cartridge_id: str,
    entity_name: str,
    run_type: str,
    status: str,
    started_at: datetime,
    requested_run_id: str | None = None,
) -> str:
    run_id = str(requested_run_id or "").strip() or str(uuid.uuid4())
    tenant_id, workspace_id = scope_values()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO extraction_runs
                    (run_id, cartridge_id, entity_name, run_type, status, started_at,
                     tenant_id, workspace_id, scope_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'scoped')
                ON CONFLICT (run_id) DO UPDATE SET
                    cartridge_id = EXCLUDED.cartridge_id,
                    entity_name = EXCLUDED.entity_name,
                    run_type = EXCLUDED.run_type,
                    status = EXCLUDED.status,
                    records_extracted = NULL,
                    storage_uri = NULL,
                    error_message = NULL,
                    started_at = EXCLUDED.started_at,
                    finished_at = NULL,
                    tenant_id = EXCLUDED.tenant_id,
                    workspace_id = EXCLUDED.workspace_id,
                    scope_status = EXCLUDED.scope_status
                """,
                (run_id, cartridge_id, entity_name, run_type, status, started_at, tenant_id, workspace_id),
            )
        conn.commit()
        _mirror_pipeline_run(run_id, status=status)
        return run_id
    finally:
        conn.close()


def finish_run(
    run_id: str,
    status: str,
    records_extracted: int,
    storage_uri: str | None,
    finished_at: datetime,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE extraction_runs
                SET status = %s, records_extracted = %s,
                    storage_uri = %s, finished_at = %s
                WHERE run_id = %s
                """,
                (status, records_extracted, storage_uri, finished_at, run_id),
            )
        conn.commit()
        _mirror_pipeline_run(
            run_id,
            status=status,
            records_extracted=records_extracted,
            storage_uri=storage_uri,
            finished_at=finished_at,
        )
    finally:
        conn.close()


def fail_run(run_id: str, error_message: str, finished_at: datetime) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE extraction_runs
                SET status = 'failed', error_message = %s, finished_at = %s
                WHERE run_id = %s
                """,
                (error_message[:4000], finished_at, run_id),
            )
        conn.commit()
        _mirror_pipeline_run(
            run_id,
            status="failed",
            error_message=error_message[:4000],
            finished_at=finished_at,
        )
    finally:
        conn.close()


def get_last_run_status(entity_name: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if entity_name:
                cur.execute(
                    """
                    SELECT run_id, cartridge_id, entity_name, run_type, status,
                           records_extracted, storage_uri, error_message,
                           started_at, finished_at
                    FROM extraction_runs
                    WHERE entity_name = %s
                    ORDER BY started_at DESC LIMIT 20
                    """,
                    (entity_name,),
                )
            else:
                cur.execute(
                    """
                    SELECT run_id, cartridge_id, entity_name, run_type, status,
                           records_extracted, storage_uri, error_message,
                           started_at, finished_at
                    FROM extraction_runs
                    WHERE cartridge_id = 'sap_successfactors'
                    ORDER BY started_at DESC LIMIT 20
                    """
                )
            rows = cur.fetchall()
        return [
            {
                "run_id": r[0], "cartridge_id": r[1], "entity_name": r[2],
                "run_type": r[3], "status": r[4], "records_extracted": r[5],
                "storage_uri": r[6], "error_message": r[7],
                "started_at": r[8].isoformat() if r[8] else None,
                "finished_at": r[9].isoformat() if r[9] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()
