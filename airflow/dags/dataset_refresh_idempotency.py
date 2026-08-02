"""Durable per-dataset reservation slots backed by ``pipeline_runs``."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


MATERIALIZATION_LAYERS = frozenset({"silver", "gold"})


def _slot_id(run_id: str, tenant_id: str, workspace_id: str, dataset: str) -> str:
    digest = hashlib.sha256(
        "\0".join((run_id, tenant_id, workspace_id, dataset)).encode("utf-8")
    ).hexdigest()
    return f"dataset_refresh_item:{digest}"


def _scope(cur: Any, tenant_id: str, workspace_id: str) -> None:
    cur.execute(
        "SELECT set_config('app.tenant_id', %s, true), "
        "set_config('app.workspace_id', %s, true)",
        (tenant_id, workspace_id),
    )


def reserve_materialization(
    dsn: str,
    *,
    airflow_run_id: str,
    tenant_id: str,
    workspace_id: str,
    cartridge_id: str,
    dataset: str,
    lease_seconds: int = 900,
) -> dict[str, Any]:
    import psycopg2

    slot = _slot_id(airflow_run_id, tenant_id, workspace_id, dataset)
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        _scope(cur, tenant_id, workspace_id)
        cur.execute("SELECT to_regclass('public.pipeline_runs')")
        if not cur.fetchone()[0]:
            raise RuntimeError("materialization idempotency storage unavailable")
        lease_seconds = max(30, min(int(lease_seconds), 3600))
        cur.execute(
            """
            SELECT status, extra, lease_expires_at, fencing_token
              FROM pipeline_runs
             WHERE run_id = %s
               AND tenant_id = %s::uuid
               AND workspace_id = %s::uuid
             FOR UPDATE
            """,
            (slot, tenant_id, workspace_id),
        )
        existing = cur.fetchone()
        if existing and str(existing[0]) == "success":
            extra = existing[1] if isinstance(existing[1], dict) else {}
            return {
                "reserved": False,
                "completed": True,
                "result": extra.get("result") or {},
            }
        if (
            existing
            and str(existing[0]) == "running"
            and existing[2] is not None
            and existing[2] > datetime.now(timezone.utc)
        ):
            raise RuntimeError("materialization idempotency slot is already active")
        if existing:
            cur.execute(
                """
                UPDATE pipeline_runs
                   SET status = 'running', started_at = NOW(), finished_at = NULL,
                       error_message = NULL, heartbeat_at = NOW(),
                       lease_expires_at = NOW() + %s * INTERVAL '1 second',
                       fencing_token = fencing_token + 1
                 WHERE run_id = %s
                   AND tenant_id = %s::uuid
                   AND workspace_id = %s::uuid
                """,
                (lease_seconds, slot, tenant_id, workspace_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO pipeline_runs (
                    run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                    mode, status, started_at, tenant_id, workspace_id, extra,
                    heartbeat_at, lease_expires_at, fencing_token
                )
                VALUES (%s, 'dataset_refresh_chain', %s, %s, %s,
                        'materialize', 'running', NOW(), %s::uuid, %s::uuid, '{}'::jsonb,
                        NOW(), NOW() + %s * INTERVAL '1 second', 1)
                """,
                (
                    slot,
                    cartridge_id,
                    dataset,
                    airflow_run_id,
                    tenant_id,
                    workspace_id,
                    lease_seconds,
                ),
            )
        cur.execute(
            """SELECT fencing_token FROM pipeline_runs
                 WHERE run_id=%s AND tenant_id=%s::uuid AND workspace_id=%s::uuid""",
            (slot, tenant_id, workspace_id),
        )
        lease_token = int(cur.fetchone()[0])
        conn.commit()
    return {
        "reserved": True,
        "completed": False,
        "slot_id": slot,
        "lease_token": lease_token,
    }


def finish_materialization(
    dsn: str,
    *,
    slot_id: str,
    tenant_id: str,
    workspace_id: str,
    lease_token: int,
    success: bool,
    result: dict[str, Any] | None = None,
) -> None:
    import psycopg2

    safe_result = {
        "name": str((result or {}).get("name") or ""),
        "row_count": int((result or {}).get("row_count") or 0),
    }
    if success:
        layer = str((result or {}).get("layer") or "").strip()
        if layer not in MATERIALIZATION_LAYERS:
            raise RuntimeError("materialization outcome layer is unavailable")
        safe_result["layer"] = layer
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        _scope(cur, tenant_id, workspace_id)
        cur.execute(
            """
            UPDATE pipeline_runs
               SET status = %s, finished_at = NOW(),
                   error_message = %s,
                   extra = %s::jsonb, lease_expires_at = NULL
             WHERE run_id = %s
               AND tenant_id = %s::uuid
               AND workspace_id = %s::uuid
               AND status = 'running'
               AND fencing_token = %s
               AND lease_expires_at > clock_timestamp()
            """,
            (
                "success" if success else "failed",
                None if success else "materialization_failed",
                json.dumps({"result": safe_result} if success else {}),
                slot_id,
                tenant_id,
                workspace_id,
                lease_token,
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError("materialization idempotency slot unavailable")
        conn.commit()


def heartbeat_materialization(
    dsn: str,
    *,
    slot_id: str,
    tenant_id: str,
    workspace_id: str,
    lease_token: int,
    lease_seconds: int = 900,
) -> None:
    import psycopg2

    lease_seconds = max(30, min(int(lease_seconds), 3600))
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        _scope(cur, tenant_id, workspace_id)
        cur.execute(
            """UPDATE pipeline_runs
                  SET heartbeat_at=NOW(),
                      lease_expires_at=NOW() + %s * INTERVAL '1 second'
                WHERE run_id=%s AND tenant_id=%s::uuid AND workspace_id=%s::uuid
                  AND status='running' AND fencing_token=%s
                  AND lease_expires_at > NOW()""",
            (lease_seconds, slot_id, tenant_id, workspace_id, lease_token),
        )
        if cur.rowcount != 1:
            raise RuntimeError("materialization lease is unavailable")
