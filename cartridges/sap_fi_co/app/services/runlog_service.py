from __future__ import annotations

import uuid
from datetime import datetime

from app.core.pg_client import get_connection


def create_run(
    cartridge_id: str,
    entity_name: str,
    run_type: str,
    status: str,
    started_at: datetime,
) -> str:
    run_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO extraction_runs
                    (run_id, cartridge_id, entity_name, run_type, status, started_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (run_id, cartridge_id, entity_name, run_type, status, started_at),
            )
        conn.commit()
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
                    WHERE cartridge_id = 'replicon'
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
