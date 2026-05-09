from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.pg_client import get_connection
from app.services.catalog_service import get_all_kbs, get_kb_config
from app.services.duckdb_service import run_kb_sql, write_kb_parquet, write_kb_to_postgres


def get_all_knowledge_bits() -> list[dict]:
    return [
        {
            "id":               kb.get("kb_id") or kb.get("id"),
            "name":             kb.get("name"),
            "description":      kb.get("description"),
            "source_entities":  kb.get("source_entities") or [],
            "pg_table":         kb.get("pg_table"),
        }
        for kb in get_all_kbs()
    ]


def _create_kb_run(kb_id: str, started_at: datetime) -> str:
    run_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kb_runs (run_id, kb_id, status, started_at) VALUES (%s, %s, %s, %s)",
                (run_id, kb_id, "running", started_at),
            )
        conn.commit()
    finally:
        conn.close()
    return run_id


def _finish_kb_run(run_id: str, records: int, storage_uri: str, finished_at: datetime) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE kb_runs
                   SET status = 'completed', records_output = %s,
                       storage_uri = %s, finished_at = %s
                   WHERE run_id = %s""",
                (records, storage_uri, finished_at, run_id),
            )
        conn.commit()
    finally:
        conn.close()


def _fail_kb_run(run_id: str, error: str, finished_at: datetime) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE kb_runs
                   SET status = 'failed', error_message = %s, finished_at = %s
                   WHERE run_id = %s""",
                (error[:4000], finished_at, run_id),
            )
        conn.commit()
    finally:
        conn.close()


def run_knowledge_bit(kb_id: str) -> dict:
    config = get_kb_config(kb_id)
    if not config:
        return {"status": "error", "error": f"Knowledge Bit not found: {kb_id}"}

    sql         = config.get("sql", "")
    output_path = config.get("output_path", "")
    pg_table    = config.get("pg_table")

    if not sql:
        return {"status": "error", "error": f"KB {kb_id} has no SQL defined"}

    started_at = datetime.now(timezone.utc)
    run_id = _create_kb_run(kb_id, started_at)

    try:
        df = run_kb_sql(sql)

        storage_uri = write_kb_parquet(df, output_path, kb_id, run_id)

        if pg_table:
            write_kb_to_postgres(df, pg_table)

        finished_at = datetime.now(timezone.utc)
        _finish_kb_run(run_id, len(df), storage_uri, finished_at)

        return {
            "run_id":      run_id,
            "kb_id":       kb_id,
            "status":      "completed",
            "records":     len(df),
            "storage_uri": storage_uri,
            "started_at":  started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        }

    except Exception as exc:
        finished_at = datetime.now(timezone.utc)
        _fail_kb_run(run_id, str(exc), finished_at)
        return {"run_id": run_id, "kb_id": kb_id, "status": "failed", "error": str(exc)}


def run_all_knowledge_bits() -> list[dict]:
    return [run_knowledge_bit(kb.get("kb_id") or kb.get("id")) for kb in get_all_kbs()]


def get_kb_runs(kb_id: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if kb_id:
                cur.execute(
                    """SELECT run_id, kb_id, status, records_output, storage_uri,
                              error_message, started_at, finished_at
                       FROM kb_runs WHERE kb_id = %s ORDER BY started_at DESC LIMIT 20""",
                    (kb_id,),
                )
            else:
                cur.execute(
                    """SELECT run_id, kb_id, status, records_output, storage_uri,
                              error_message, started_at, finished_at
                       FROM kb_runs ORDER BY started_at DESC LIMIT 20"""
                )
            rows = cur.fetchall()
        return [
            {
                "run_id":         r[0],
                "kb_id":          r[1],
                "status":         r[2],
                "records_output": r[3],
                "storage_uri":    r[4],
                "error_message":  r[5],
                "started_at":     r[6].isoformat() if r[6] else None,
                "finished_at":    r[7].isoformat() if r[7] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()
