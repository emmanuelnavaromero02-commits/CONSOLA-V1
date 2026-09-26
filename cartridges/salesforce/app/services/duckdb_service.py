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
from app.core.request_context import scoped_prefix, scope_values

_SAFE_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


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


def _get_duckdb_connection() -> duckdb.DuckDBPyConnection:
    storage = settings.resolved_minio
    conn = duckdb.connect()
    conn.execute("SET autoinstall_known_extensions=false;")
    conn.execute("SET autoload_known_extensions=false;")
    conn.execute("LOAD httpfs;")
    try:
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
        conn.execute(
            f"SET s3_use_ssl={'true' if storage['secure'] else 'false'};"
        )
        conn.execute(
            f"SET s3_url_style='{'vhost' if storage['provider'] == 's3' else 'path'}';"
        )
    except Exception:  # noqa: BLE001 - never echo a credential-bearing setup error.
        conn.close()
        raise RuntimeError("storage_access_denied") from None
    _apply_resource_limits(conn)
    _restrict_external_access(conn, storage["bucket"])
    conn.execute("SET lock_configuration=true;")
    return conn


def run_kb_sql(sql: str) -> pd.DataFrame:
    resolved = sql.replace("{bucket}", settings.resolved_minio["bucket"])
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
    load_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scope = "" if "tenant_id=" in output_path else scoped_prefix(security_context)
    object_name = (
        f"{output_path}/{scope}load_date={load_date}/batch_id={run_id}/{kb_id}.parquet"
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
) -> None:
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
