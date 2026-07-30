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
from app.services.kb_materialization import MaterializationRun

_SAFE_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


def _path_has_scope(path: str, scope: str) -> bool:
    parts = [part for part in path.strip("/").split("/") if part]
    scope_parts = [part for part in scope.strip("/").split("/") if part]
    return any(
        parts[idx : idx + len(scope_parts)] == scope_parts for idx in range(len(parts))
    )


def _get_duckdb_connection() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute("LOAD httpfs;")
    conn.execute(f"SET s3_endpoint='{settings.minio_endpoint}';")
    conn.execute(f"SET s3_access_key_id='{settings.minio_access_key}';")
    conn.execute(f"SET s3_secret_access_key='{settings.minio_secret_key}';")
    conn.execute(f"SET s3_use_ssl={'true' if settings.minio_secure else 'false'};")
    conn.execute("SET s3_url_style='path';")
    conn.execute("SET lock_configuration=true;")
    return conn


def run_kb_sql(
    sql: str, *, runtime_tables: dict[str, pd.DataFrame] | None = None
) -> pd.DataFrame:
    resolved = sql.replace("{bucket}", settings.minio_bucket)
    conn = _get_duckdb_connection()
    try:
        for name, frame in (runtime_tables or {}).items():
            if not _SAFE_IDENT_RE.match(name):
                raise ValueError(f"Unsafe runtime table identifier: {name!r}")
            conn.register(name, frame)
        return conn.execute(resolved).df()
    finally:
        conn.close()


def write_kb_parquet(
    df: pd.DataFrame,
    output_path: str,
    kb_id: str,
    run_id: str,
    security_context: dict[str, Any] | None = None,
    *,
    package_version: str = "unversioned",
    sql_digest: str = "unknown",
    input_digest: str = "unknown",
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
        f"{output_path}/package_version={package_version}/"
        f"sql_digest={sql_digest}/input_digest={input_digest}/"
        f"{scope}load_date={load_date}/batch_id={run_id}/{kb_id}.parquet"
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
    *,
    materialization_run: MaterializationRun | None = None,
) -> None:
    security_context = require_tenant_workspace_scope(security_context)
    if not _SAFE_IDENT_RE.match(pg_table):
        raise ValueError(f"Unsafe pg_table identifier: {pg_table!r}")
    tenant, workspace = scope_values(security_context)
    engine = create_engine(settings.database_url)
    try:
        if materialization_run is not None:
            if materialization_run.kb_id not in {
                "kb_wip_mensual",
                "kb_wip_resumen",
            }:
                raise ValueError("unsupported managed WIP materialization")
            managed_kb_id = materialization_run.kb_id
            history_schema = "knowledge_bits_history"
            history_table = f'{history_schema}."{pg_table}"'
            public_table = f'knowledge_bits."{pg_table}"'
            history = df.copy()
            history["tenant_id"] = tenant
            history["workspace_id"] = workspace
            history["kb_run_id"] = materialization_run.run_id
            history["package_version"] = materialization_run.package_version
            history["sql_digest"] = materialization_run.sql_digest
            history["input_digest"] = materialization_run.input_digest
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "SELECT set_config('app.tenant_id', :tenant, true), "
                        "set_config('app.workspace_id', :workspace, true)"
                    ),
                    {"tenant": tenant, "workspace": workspace},
                )
                relation_kind = conn.execute(
                    text(
                        """SELECT relkind FROM pg_class rel JOIN pg_namespace ns
                             ON ns.oid=rel.relnamespace
                            WHERE ns.nspname='knowledge_bits' AND rel.relname=:table"""
                    ),
                    {"table": pg_table},
                ).scalar()
                if relation_kind not in (None, "v"):
                    raise SecurityContextError(
                        "legacy WIP materialization is not quarantined"
                    )
                history.to_sql(
                    name=pg_table,
                    con=conn,
                    schema=history_schema,
                    if_exists="append",
                    index=False,
                )
                conn.execute(text(f"ALTER TABLE {history_table} ENABLE ROW LEVEL SECURITY"))
                conn.execute(text(f"ALTER TABLE {history_table} FORCE ROW LEVEL SECURITY"))
                conn.execute(text(f"DROP POLICY IF EXISTS workspace_scope ON {history_table}"))
                conn.execute(
                    text(
                        f"""CREATE POLICY workspace_scope ON {history_table}
                            TO omega_cartridge_replicon
                            USING (omega_rls_workspace_matches(
                                tenant_id::uuid, workspace_id::uuid))
                            WITH CHECK (omega_rls_workspace_matches(
                                tenant_id::uuid, workspace_id::uuid))"""
                    )
                )
                conn.execute(
                    text(
                        f"""CREATE OR REPLACE VIEW {public_table} WITH (security_invoker=true) AS
                            SELECT history.* FROM {history_table} history
                            JOIN public.kb_materialization_heads head
                              ON head.cartridge_id='replicon'
                             AND head.kb_id='{managed_kb_id}'
                             AND head.tenant_id=history.tenant_id::uuid
                             AND head.workspace_id=history.workspace_id::uuid
                             AND head.current_run_id=history.kb_run_id
                             AND head.package_version=history.package_version
                             AND head.sql_digest=history.sql_digest
                             AND head.input_digest=history.input_digest
                             AND head.state='current'"""
                    )
                )
            return
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
