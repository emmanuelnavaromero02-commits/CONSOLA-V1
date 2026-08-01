from __future__ import annotations

import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.minio_client import upload_file_to_minio
from app.core.request_context import (
    SecurityContextError,
    require_tenant_workspace_scope,
    scoped_prefix,
    scope_values,
)

_SAFE_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


def _path_has_scope(path: str, scope: str) -> bool:
    parts = [part for part in path.strip("/").split("/") if part]
    scope_parts = [part for part in scope.strip("/").split("/") if part]
    return any(
        parts[idx : idx + len(scope_parts)] == scope_parts for idx in range(len(parts))
    )


def _get_duckdb_connection() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute("SET autoinstall_known_extensions=false;")
    conn.execute("SET autoload_known_extensions=false;")
    conn.execute("LOAD httpfs;")
    conn.execute(f"SET s3_endpoint='{settings.minio_endpoint}';")
    conn.execute(f"SET s3_access_key_id='{settings.minio_access_key}';")
    conn.execute(f"SET s3_secret_access_key='{settings.minio_secret_key}';")
    conn.execute(f"SET s3_use_ssl={'true' if settings.minio_secure else 'false'};")
    conn.execute("SET s3_url_style='path';")
    conn.execute("SET lock_configuration=true;")
    return conn


def run_kb_sql(sql: str) -> pd.DataFrame:
    resolved = sql.replace("{bucket}", settings.minio_bucket)
    conn = _get_duckdb_connection()
    try:
        return conn.execute(resolved).df()
    finally:
        conn.close()


def write_kb_parquet(
    df: pd.DataFrame,
    output_path: str,
    kb_id: str,
    run_id: str,
    security_context: dict[str, Any] | None = None,
) -> str:
    security_context = require_tenant_workspace_scope(security_context)
    load_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scope = scoped_prefix(security_context)
    if "tenant_id=" in output_path or "workspace_id=" in output_path:
        if not _path_has_scope(output_path, scope):
            raise SecurityContextError(
                "KB output path is outside the active tenant/workspace scope"
            )
        scope = ""
    object_name = (
        f"{output_path}/{scope}load_date={load_date}/batch_id={run_id}/{kb_id}.parquet"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / f"{kb_id}.parquet"
        df.to_parquet(local_path, index=False, engine="pyarrow", compression="snappy")
        upload_file_to_minio(local_path=str(local_path), object_name=object_name)

    return f"s3://{settings.minio_bucket}/{object_name}"


def write_kb_to_postgres(
    df: pd.DataFrame,
    pg_table: str,
    security_context: dict[str, Any] | None = None,
) -> None:
    security_context = require_tenant_workspace_scope(security_context)
    if not _SAFE_IDENT_RE.match(pg_table):
        raise ValueError(f"Unsafe pg_table identifier: {pg_table!r}")
    tenant, workspace = scope_values(security_context)
    engine = create_engine(settings.database_url)
    try:
        if tenant and workspace:
            scoped_df = df.copy()
            scoped_df["tenant_id"] = tenant
            scoped_df["workspace_id"] = workspace
            table_name = f'knowledge_bits."{pg_table}"'
            with engine.begin() as conn:
                conn.execute(text("CREATE SCHEMA IF NOT EXISTS knowledge_bits"))
                exists = conn.execute(
                    text("SELECT to_regclass(:table_name)"),
                    {"table_name": f"knowledge_bits.{pg_table}"},
                ).scalar()
                if exists:
                    conn.execute(
                        text(
                            f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS tenant_id TEXT"
                        )
                    )
                    conn.execute(
                        text(
                            f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS workspace_id TEXT"
                        )
                    )
                    conn.execute(
                        text(
                            f"DELETE FROM {table_name} WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id"
                        ),
                        {"tenant_id": tenant, "workspace_id": workspace},
                    )
            scoped_df.to_sql(
                name=pg_table,
                con=engine,
                schema="knowledge_bits",
                if_exists="append",
                index=False,
            )
        else:
            with engine.begin() as conn:
                conn.execute(text("CREATE SCHEMA IF NOT EXISTS knowledge_bits"))
            df.to_sql(
                name=pg_table,
                con=engine,
                schema="knowledge_bits",
                if_exists="replace",
                index=False,
            )
    finally:
        engine.dispose()
