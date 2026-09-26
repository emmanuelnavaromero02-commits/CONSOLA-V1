from __future__ import annotations

import os
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
_S3_READER_RE = re.compile(
    r"\b(?:read_parquet|read_csv(?:_auto)?)\s*\(\s*(['\"])s3://[^'\"]+\1",
    re.IGNORECASE,
)
_DUCKDB_EXTENSION_CONFIG = {
    "autoinstall_known_extensions": "false",
    "autoload_known_extensions": "false",
}
_REMOTE_SOURCE_UNAVAILABLE = "DuckDB remote source unavailable"


class DuckDBHTTPFSUnavailable(RuntimeError):
    pass


def _path_has_scope(path: str, scope: str) -> bool:
    parts = [part for part in path.strip("/").split("/") if part]
    scope_parts = [part for part in scope.strip("/").split("/") if part]
    return any(
        parts[idx : idx + len(scope_parts)] == scope_parts for idx in range(len(parts))
    )


def _sql_text(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _create_s3_secret(
    conn: duckdb.DuckDBPyConnection,
    *,
    key_id: str,
    secret: str,
    session_token: str | None,
    region: str,
    endpoint: str | None,
    use_ssl: bool,
    url_style: str,
    bucket: str,
) -> None:
    options = [
        "TYPE S3",
        f"KEY_ID {_sql_text(key_id)}",
        f"SECRET {_sql_text(secret)}",
        f"REGION {_sql_text(region)}",
        f"URL_STYLE {_sql_text(url_style)}",
        f"USE_SSL {'true' if use_ssl else 'false'}",
        f"SCOPE {_sql_text(f's3://{bucket}/')}",
    ]
    if session_token:
        options.append(f"SESSION_TOKEN {_sql_text(session_token)}")
    if endpoint:
        options.append(f"ENDPOINT {_sql_text(endpoint)}")
    conn.execute(f"CREATE OR REPLACE SECRET omega_s3_keys ({', '.join(options)});")


_DUCKDB_SIZE_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
_DUCKDB_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([A-Za-z]+)")
_CGROUP_MEMORY_LIMIT_FILES = (
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
)
_DUCKDB_FALLBACK_MEMORY_LIMIT = "1GB"


def _duckdb_size_bytes(value: str) -> int:
    match = _DUCKDB_SIZE_RE.fullmatch(value.strip())
    unit = match.group(2).lower() if match else ""
    if unit not in _DUCKDB_SIZE_UNITS:
        raise RuntimeError("duckdb_resource_limits_invalid")
    return int(float(match.group(1)) * _DUCKDB_SIZE_UNITS[unit])


def _container_memory_limit_bytes() -> int | None:
    for path in _CGROUP_MEMORY_LIMIT_FILES:
        try:
            raw = Path(path).read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            continue
        return int(raw) if raw.isdigit() and 0 < int(raw) < 1 << 60 else None
    return None


def _duckdb_memory_limit() -> str:
    candidates: list[tuple[int, str]] = []
    configured = os.environ.get("DUCKDB_MEMORY_LIMIT", "").strip()
    if configured:
        candidates.append((_duckdb_size_bytes(configured), configured))
    container = _container_memory_limit_bytes()
    if container:
        mib = max(1, int(container * 0.7) // 1024**2)
        candidates.append((mib * 1024**2, f"{mib}MiB"))
    if not candidates:
        return _DUCKDB_FALLBACK_MEMORY_LIMIT
    return min(candidates, key=lambda item: item[0])[1]


def _apply_resource_limits(conn: duckdb.DuckDBPyConnection) -> None:
    try:
        conn.execute("SET preserve_insertion_order=false;")
        conn.execute(f"SET memory_limit='{_duckdb_memory_limit()}';")
        try:
            spill = os.environ.get("DUCKDB_TEMP_DIRECTORY", "").strip() or os.path.join(
                tempfile.gettempdir(), "omega-duckdb-spill"
            )
        except OSError:
            # read-only filesystem: there is nowhere to spill, keep DuckDB's default
            return
        if not spill.startswith("/") or any(ch in spill for ch in "'\"\0\n\r"):
            raise RuntimeError("duckdb_resource_limits_invalid")
        try:
            os.makedirs(spill, mode=0o700, exist_ok=True)
        except OSError:
            return
        private = os.path.join(spill, os.urandom(16).hex())
        conn.execute(f"SET temp_directory='{private}';")
    except Exception:
        conn.close()
        raise RuntimeError("duckdb_resource_limits_invalid") from None


_BUCKET_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]")


def _restrict_external_access(conn: duckdb.DuckDBPyConnection, bucket: str | None) -> None:
    if not _BUCKET_NAME_RE.fullmatch(str(bucket or "")):
        conn.close()
        raise RuntimeError("storage_access_denied")
    roots = ", ".join(f"'{scheme}://{bucket}/'" for scheme in ("s3", "gs", "gcs"))
    conn.execute(f"SET allowed_directories=[{roots}];")
    conn.execute("SET enable_external_access=false;")


def _get_duckdb_connection(resolved_sql: str) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(config=_DUCKDB_EXTENSION_CONFIG)
    if _S3_READER_RE.search(resolved_sql):
        storage = settings.resolved_minio
        try:
            conn.execute("LOAD httpfs;")
            conn.execute("SET s3_endpoint=?;", [storage["endpoint"]])
            conn.execute("SET s3_region=?;", [storage["region"] or "us-east-1"])
            if storage["access_key"] and storage["secret_key"]:
                _create_s3_secret(
                    conn,
                    key_id=storage["access_key"],
                    secret=storage["secret_key"],
                    session_token=storage["session_token"],
                    region=storage["region"] or "us-east-1",
                    endpoint=storage["endpoint"],
                    use_ssl=bool(storage["secure"]),
                    url_style="vhost" if storage["provider"] == "s3" else "path",
                    bucket=storage["bucket"],
                )
            elif storage["provider"] == "s3":
                conn.execute("LOAD aws;")
                conn.execute(
                    "CREATE OR REPLACE SECRET omega_s3_role "
                    "(TYPE S3, PROVIDER credential_chain);"
                )
            conn.execute("SET s3_use_ssl=?;", [storage["secure"]])
            conn.execute(
                f"SET s3_url_style='{'vhost' if storage['provider'] == 's3' else 'path'}';"
            )
        except duckdb.Error:
            conn.close()
            raise DuckDBHTTPFSUnavailable(_REMOTE_SOURCE_UNAVAILABLE) from None
    _apply_resource_limits(conn)
    _restrict_external_access(conn, settings.resolved_minio["bucket"])
    conn.execute("SET lock_configuration=true;")
    return conn


def run_kb_sql(
    sql: str, *, runtime_tables: dict[str, pd.DataFrame] | None = None
) -> pd.DataFrame:
    resolved = sql.replace("{bucket}", settings.resolved_minio["bucket"])
    remote = _S3_READER_RE.search(resolved) is not None
    conn = _get_duckdb_connection(resolved)
    try:
        for name, frame in (runtime_tables or {}).items():
            if not _SAFE_IDENT_RE.match(name):
                raise ValueError(f"Unsafe runtime table identifier: {name!r}")
            conn.register(name, frame)
        try:
            return conn.execute(resolved).df()
        except duckdb.Error:
            if remote:
                raise DuckDBHTTPFSUnavailable(_REMOTE_SOURCE_UNAVAILABLE) from None
            raise
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

    return f"s3://{settings.resolved_minio['bucket']}/{object_name}"


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
                conn.execute(
                    text(f"ALTER TABLE {history_table} ENABLE ROW LEVEL SECURITY")
                )
                conn.execute(
                    text(f"ALTER TABLE {history_table} FORCE ROW LEVEL SECURITY")
                )
                conn.execute(
                    text(f"DROP POLICY IF EXISTS workspace_scope ON {history_table}")
                )
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
