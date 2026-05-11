from __future__ import annotations

from app.core.pg_client import get_connection

_CARTRIDGE_ID = "replicon"


def get_watermark(entity_name: str) -> str | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT last_watermark_value FROM entity_watermarks
                WHERE cartridge_id = %s AND entity_name = %s
                ORDER BY updated_at DESC LIMIT 1
                """,
                (_CARTRIDGE_ID, entity_name),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def update_watermark(
    entity_name: str,
    watermark_field: str,
    last_watermark_value: str,
    last_run_id: str,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entity_watermarks
                    (cartridge_id, entity_name, watermark_field, last_watermark_value, last_run_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (cartridge_id, entity_name) DO UPDATE SET
                    watermark_field = EXCLUDED.watermark_field,
                    last_watermark_value = EXCLUDED.last_watermark_value,
                    last_run_id = EXCLUDED.last_run_id,
                    updated_at = NOW()
                """,
                (_CARTRIDGE_ID, entity_name, watermark_field, last_watermark_value, last_run_id),
            )
        conn.commit()
    finally:
        conn.close()


def list_watermarks() -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cartridge_id, entity_name, watermark_field,
                       last_watermark_value, last_run_id, updated_at
                FROM entity_watermarks
                WHERE cartridge_id = %s
                ORDER BY entity_name
                """,
                (_CARTRIDGE_ID,),
            )
            rows = cur.fetchall()
        return [
            {
                "cartridge_id": r[0], "entity_name": r[1], "watermark_field": r[2],
                "last_watermark_value": r[3], "last_run_id": r[4],
                "updated_at": r[5].isoformat() if r[5] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()
