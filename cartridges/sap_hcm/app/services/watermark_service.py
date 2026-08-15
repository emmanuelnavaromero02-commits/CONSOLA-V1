from __future__ import annotations

import os

from app.core.pg_client import get_connection
from app.core.request_context import scope_values

_CARTRIDGE_ID = "sap_hcm"


def _scope_ids() -> tuple[str | None, str | None]:
    scoped_tenant_id, scoped_workspace_id = scope_values()
    if scoped_tenant_id and scoped_workspace_id:
        return scoped_tenant_id, scoped_workspace_id
    tenant_id = (os.environ.get("OMEGA_TENANT_ID") or os.environ.get("TENANT_ID") or "").strip()
    workspace_id = (os.environ.get("OMEGA_WORKSPACE_ID") or os.environ.get("WORKSPACE_ID") or "").strip()
    return tenant_id or None, workspace_id or None


def _watermark_scope() -> tuple[str, str | None, str | None]:
    tenant_id, workspace_id = _scope_ids()
    if tenant_id and workspace_id:
        return f"tenant:{tenant_id}:workspace:{workspace_id}", tenant_id, workspace_id
    return "platform", None, None


def _set_db_scope(cur, tenant_id: str | None, workspace_id: str | None) -> None:
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id or "",))
    cur.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id or "",))
    cur.execute("SELECT set_config('app.platform_admin', %s, true)", ("false" if tenant_id and workspace_id else "true",))


def get_watermark(entity_name: str) -> str | None:
    scope, tenant_id, workspace_id = _watermark_scope()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _set_db_scope(cur, tenant_id, workspace_id)
            cur.execute(
                """
                SELECT last_watermark_value FROM entity_watermarks
                WHERE cartridge_id = %s AND entity_name = %s AND watermark_scope = %s
                ORDER BY updated_at DESC LIMIT 1
                """,
                (_CARTRIDGE_ID, entity_name, scope),
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
    scope, tenant_id, workspace_id = _watermark_scope()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _set_db_scope(cur, tenant_id, workspace_id)
            cur.execute(
                """
                INSERT INTO entity_watermarks
                    (cartridge_id, entity_name, watermark_field, last_watermark_value, last_run_id,
                     tenant_id, workspace_id, watermark_scope)
                VALUES (%s, %s, %s, %s, %s, %s::uuid, %s::uuid, %s)
                ON CONFLICT (watermark_scope, cartridge_id, entity_name) DO UPDATE SET
                    watermark_field = EXCLUDED.watermark_field,
                    last_watermark_value = EXCLUDED.last_watermark_value,
                    last_run_id = EXCLUDED.last_run_id,
                    tenant_id = EXCLUDED.tenant_id,
                    workspace_id = EXCLUDED.workspace_id,
                    updated_at = NOW()
                """,
                (_CARTRIDGE_ID, entity_name, watermark_field, last_watermark_value, last_run_id, tenant_id, workspace_id, scope),
            )
        conn.commit()
    finally:
        conn.close()


def list_watermarks() -> list[dict]:
    scope, tenant_id, workspace_id = _watermark_scope()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _set_db_scope(cur, tenant_id, workspace_id)
            cur.execute(
                """
                SELECT cartridge_id, entity_name, watermark_field,
                       last_watermark_value, last_run_id, updated_at
                FROM entity_watermarks
                WHERE cartridge_id = %s AND watermark_scope = %s
                ORDER BY entity_name
                """,
                (_CARTRIDGE_ID, scope),
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
