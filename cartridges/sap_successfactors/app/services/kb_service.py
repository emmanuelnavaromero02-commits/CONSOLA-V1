from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.pg_client import get_connection
from app.core.request_context import (
    SecurityContextError,
    get_security_context,
    require_tenant_workspace_scope,
    scoped_prefix,
)
from app.core.sql_guard import validate_kb_sql
from app.services.catalog_service import get_all_kbs, get_kb_config
from app.services.duckdb_service import (
    run_kb_sql,
    write_kb_parquet,
    write_kb_to_postgres,
)

CARTRIDGE_ID = "sap_successfactors"
_SQL_STORAGE_PATH_RE = re.compile(
    rf"(s3://[^'\"\s)]+/(?:raw|silver|gold)/{re.escape(CARTRIDGE_ID)}/)([^'\"\s)]*)"
)


def _scope_kb_sql(sql: str, security_context: dict[str, Any] | None = None) -> str:
    security_context = require_tenant_workspace_scope(security_context)
    resolved = str(sql or "").replace("{bucket}", settings.minio_bucket)
    scope = scoped_prefix(security_context)

    def _scope_path(match: re.Match[str]) -> str:
        base, rest = match.group(1), match.group(2)
        if not rest:
            raise SecurityContextError("KB storage path must include an entity or dataset segment")
        head, sep, tail = rest.partition("/")
        if not sep or not head:
            raise SecurityContextError("KB storage path must include an entity or dataset segment")
        if "tenant_id=" in rest or "workspace_id=" in rest:
            if not rest.startswith(f"{head}/{scope}"):
                raise SecurityContextError("KB storage path is outside the active tenant/workspace scope")
            return match.group(0)
        return f"{base}{head}/{scope}{tail}"

    return _SQL_STORAGE_PATH_RE.sub(_scope_path, resolved)


def _kb_allowed_prefixes() -> tuple[str, str, str]:
    bucket = settings.minio_bucket
    return (
        f"s3://{bucket}/raw/{CARTRIDGE_ID}/",
        f"s3://{bucket}/silver/{CARTRIDGE_ID}/",
        f"s3://{bucket}/gold/{CARTRIDGE_ID}/",
    )


def get_all_knowledge_bits() -> list[dict]:
    return [
        {
            "id": kb.get("kb_id") or kb.get("id"),
            "name": kb.get("name"),
            "description": kb.get("description"),
            "source_entities": kb.get("source_entities") or [],
            "pg_table": kb.get("pg_table"),
        }
        for kb in get_all_kbs()
    ]


def _create_kb_run(kb_id: str, started_at: datetime, security_context: dict[str, Any]) -> str:
    run_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO kb_runs
                   (run_id, kb_id, status, started_at, tenant_id, workspace_id, scope_status)
                   VALUES (%s, %s, %s, %s, %s, %s, 'scoped')""",
                (
                    run_id,
                    kb_id,
                    "running",
                    started_at,
                    security_context.get("tenant_id"),
                    security_context.get("workspace_id"),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return run_id


def _finish_kb_run(
    run_id: str, records: int, storage_uri: str, finished_at: datetime
) -> None:
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


def run_knowledge_bit(
    kb_id: str,
    security_context: dict[str, Any] | None = None,
) -> dict:
    config = get_kb_config(kb_id)
    if not config:
        return {"status": "error", "error": f"Knowledge Bit not found: {kb_id}"}

    sql = config.get("sql", "")
    output_path = config.get("output_path", "")
    pg_table = config.get("pg_table")

    if not sql:
        return {"status": "error", "error": f"KB {kb_id} has no SQL defined"}

    try:
        security_context = require_tenant_workspace_scope(
            security_context if isinstance(security_context, dict) else get_security_context()
        )
        resolved_sql = _scope_kb_sql(sql, security_context)
    except SecurityContextError as exc:
        return {"status": "error", "error": str(exc)}
    ok, err = validate_kb_sql(
        resolved_sql,
        _kb_allowed_prefixes(),
        required_scope=scoped_prefix(security_context),
    )
    if not ok:
        return {"status": "error", "error": f"KB SQL blocked by security guard: {err}"}

    started_at = datetime.now(timezone.utc)
    run_id = _create_kb_run(kb_id, started_at, security_context)

    try:
        df = run_kb_sql(resolved_sql)

        storage_uri = write_kb_parquet(df, output_path, kb_id, run_id, security_context)

        if pg_table:
            write_kb_to_postgres(df, pg_table, security_context)

        finished_at = datetime.now(timezone.utc)
        _finish_kb_run(run_id, len(df), storage_uri, finished_at)

        return {
            "run_id": run_id,
            "kb_id": kb_id,
            "status": "completed",
            "records": len(df),
            "storage_uri": storage_uri,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        }

    except Exception as exc:
        finished_at = datetime.now(timezone.utc)
        _fail_kb_run(run_id, str(exc), finished_at)
        return {"run_id": run_id, "kb_id": kb_id, "status": "failed", "error": str(exc)}


def run_all_knowledge_bits(
    security_context: dict[str, Any] | None = None,
) -> list[dict]:
    return [
        run_knowledge_bit(kb.get("kb_id") or kb.get("id"), security_context)
        for kb in get_all_kbs()
    ]


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
                "run_id": r[0],
                "kb_id": r[1],
                "status": r[2],
                "records_output": r[3],
                "storage_uri": r[4],
                "error_message": r[5],
                "started_at": r[6].isoformat() if r[6] else None,
                "finished_at": r[7].isoformat() if r[7] else None,
            }
            for r in rows
        ]
    finally:
        conn.close()
