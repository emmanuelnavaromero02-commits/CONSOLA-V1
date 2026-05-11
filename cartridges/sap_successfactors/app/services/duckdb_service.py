from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.minio_client import upload_file_to_minio


def _get_duckdb_connection() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    try:
        conn.execute("LOAD httpfs;")
    except Exception:
        conn.execute("INSTALL httpfs; LOAD httpfs;")
    conn.execute(f"SET s3_endpoint='{settings.minio_endpoint}';")
    conn.execute(f"SET s3_access_key_id='{settings.minio_access_key}';")
    conn.execute(f"SET s3_secret_access_key='{settings.minio_secret_key}';")
    conn.execute(f"SET s3_use_ssl={'true' if settings.minio_secure else 'false'};")
    conn.execute("SET s3_url_style='path';")
    return conn


def run_kb_sql(sql: str) -> pd.DataFrame:
    resolved = sql.replace("{bucket}", settings.minio_bucket)
    conn = _get_duckdb_connection()
    try:
        return conn.execute(resolved).df()
    finally:
        conn.close()


def write_kb_parquet(df: pd.DataFrame, output_path: str, kb_id: str, run_id: str) -> str:
    load_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    object_name = f"{output_path}/load_date={load_date}/batch_id={run_id}/{kb_id}.parquet"

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / f"{kb_id}.parquet"
        df.to_parquet(local_path, index=False, engine="pyarrow", compression="snappy")
        upload_file_to_minio(local_path=str(local_path), object_name=object_name)

    return f"s3://{settings.minio_bucket}/{object_name}"


def write_kb_to_postgres(df: pd.DataFrame, pg_table: str) -> None:
    engine = create_engine(settings.database_url)
    try:
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
