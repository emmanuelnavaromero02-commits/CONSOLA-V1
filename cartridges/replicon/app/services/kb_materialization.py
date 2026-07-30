from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from app.core.pg_client import get_connection


MANAGED_WIP_IDS = {"kb_wip_mensual", "kb_wip_resumen"}


@dataclass(frozen=True)
class MaterializationRun:
    run_id: str
    kb_id: str
    package_version: str
    sql_digest: str
    input_digest: str
    generation: int
    tenant_id: str
    workspace_id: str


def sql_digest(sql: str) -> str:
    return hashlib.sha256(str(sql).encode("utf-8")).hexdigest()


def _input_digest(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_base_currency_config(
    security_context: dict[str, Any],
) -> tuple[pd.DataFrame, str]:
    tenant = str(security_context.get("tenant_id") or "")
    workspace = str(security_context.get("workspace_id") or "")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT effective_from, effective_to, currency, authority_source,
                          verified_at
                     FROM replicon_base_currency_config
                    WHERE tenant_id = %s AND workspace_id = %s
                    ORDER BY effective_from""",
                (tenant, workspace),
            )
            rows = [
                {
                    "effective_from": row[0],
                    "effective_to": row[1],
                    "currency": row[2],
                    "authority_source": row[3],
                    "verified_at": row[4],
                }
                for row in cur.fetchall()
            ]
    finally:
        conn.close()
    frame = pd.DataFrame(
        rows,
        columns=[
            "effective_from",
            "effective_to",
            "currency",
            "authority_source",
            "verified_at",
        ],
    )
    return frame, _input_digest(rows)


def create_kb_run(
    kb_id: str,
    started_at: datetime,
    security_context: dict[str, Any],
    config: dict[str, Any],
    input_digest: str,
) -> MaterializationRun:
    run = MaterializationRun(
        run_id=str(uuid.uuid4()),
        kb_id=kb_id,
        package_version=str(config.get("package_version") or ""),
        sql_digest=sql_digest(str(config.get("sql") or "")),
        input_digest=input_digest,
        generation=time.time_ns(),
        tenant_id=str(security_context.get("tenant_id") or ""),
        workspace_id=str(security_context.get("workspace_id") or ""),
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_runs
                   (run_id, cartridge_id, kb_id, status, started_at,
                    tenant_id, workspace_id, scope_status, package_version,
                    sql_digest, input_digest, generation, artifact_status)
                   VALUES (%s, 'replicon', %s, 'running', %s, %s, %s, 'scoped',
                           %s, %s, %s, %s, 'pending')""",
                (
                    run.run_id,
                    run.kb_id,
                    started_at,
                    run.tenant_id,
                    run.workspace_id,
                    run.package_version,
                    run.sql_digest,
                    run.input_digest,
                    run.generation,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return run


def finish_kb_run(
    run: MaterializationRun,
    records: int,
    storage_uri: str,
    finished_at: datetime,
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM kb_config
                    WHERE cartridge_id='replicon' AND kb_id=%s
                      AND package_version=%s AND package_sql_digest=%s
                    FOR UPDATE""",
                (run.kb_id, run.package_version, run.sql_digest),
            )
            if cur.fetchone() is None:
                raise RuntimeError(
                    "KB package provenance changed during materialization"
                )
            cur.execute(
                """UPDATE kb_runs SET status='completed', records_output=%s,
                          storage_uri=%s, finished_at=%s, artifact_status='current',
                          invalid_reason=NULL WHERE run_id=%s""",
                (records, storage_uri, finished_at, run.run_id),
            )
            cur.execute(
                """INSERT INTO kb_materialization_heads
                   (cartridge_id, kb_id, tenant_id, workspace_id, current_run_id,
                    package_version, sql_digest, input_digest, generation, state)
                   VALUES ('replicon', %s, %s, %s, %s, %s, %s, %s, %s, 'current')
                   ON CONFLICT (cartridge_id, kb_id, tenant_id, workspace_id)
                   DO UPDATE SET current_run_id=EXCLUDED.current_run_id,
                     package_version=EXCLUDED.package_version,
                     sql_digest=EXCLUDED.sql_digest,
                     input_digest=EXCLUDED.input_digest,
                     generation=EXCLUDED.generation, state='current', updated_at=NOW()
                   WHERE kb_materialization_heads.generation < EXCLUDED.generation""",
                (
                    run.kb_id,
                    run.tenant_id,
                    run.workspace_id,
                    run.run_id,
                    run.package_version,
                    run.sql_digest,
                    run.input_digest,
                    run.generation,
                ),
            )
            if cur.rowcount:
                cur.execute(
                    """UPDATE kb_config SET materialization_status='current',
                              current_run_id=%s, invalid_reason=NULL
                        WHERE cartridge_id='replicon' AND kb_id=%s
                          AND package_version=%s AND package_sql_digest=%s""",
                    (run.run_id, run.kb_id, run.package_version, run.sql_digest),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("KB config activation was not singular")
            else:
                cur.execute(
                    """UPDATE kb_runs SET artifact_status='quarantined',
                              invalid_reason='superseded_generation'
                        WHERE run_id=%s""",
                    (run.run_id,),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fail_kb_run(run: MaterializationRun, error: str, finished_at: datetime) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE kb_runs SET status='failed', error_message=%s,
                          finished_at=%s, artifact_status='quarantined',
                          invalid_reason='materialization_failed' WHERE run_id=%s""",
                (error[:4000], finished_at, run.run_id),
            )
        conn.commit()
    finally:
        conn.close()
