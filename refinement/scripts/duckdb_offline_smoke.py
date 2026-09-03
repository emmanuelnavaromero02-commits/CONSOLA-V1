#!/usr/bin/env python3
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import boto3
import duckdb
import psycopg2

from app.duckdb_engine import DuckDBEngine
from app.duckdb_runtime import DUCKDB_VERSION, require_loaded_extensions


def _wait_postgres() -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with psycopg2.connect(os.environ["DATABASE_URL"]) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "CREATE TABLE IF NOT EXISTS offline_extension_probe(value integer)"
                    )
                    cursor.execute("TRUNCATE offline_extension_probe")
                    cursor.execute("INSERT INTO offline_extension_probe VALUES (17)")
                return
        except psycopg2.OperationalError:
            time.sleep(0.25)
    raise RuntimeError("offline PostgreSQL did not become ready")


def _write_minio_probe() -> None:
    endpoint = "http://" + os.environ["MINIO_ENDPOINT"]
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        region_name="us-east-1",
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            client.create_bucket(Bucket=os.environ["MINIO_BUCKET"])
            break
        except client.exceptions.BucketAlreadyOwnedByYou:
            break
        except Exception:
            time.sleep(0.25)
    else:
        raise RuntimeError("offline MinIO did not become ready")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "probe.parquet"
        connection = duckdb.connect()
        connection.execute(f"COPY (SELECT 23 AS value) TO '{path}' (FORMAT PARQUET)")
        connection.close()
        client.upload_file(
            str(path), os.environ["MINIO_BUCKET"], "offline/probe.parquet"
        )


def main() -> None:
    if duckdb.__version__ != DUCKDB_VERSION:
        raise RuntimeError("offline DuckDB version mismatch")
    _wait_postgres()
    _write_minio_probe()
    engine = DuckDBEngine()
    connection = engine._conn()
    require_loaded_extensions(connection)
    installed = dict(
        connection.execute(
            "SELECT extension_name, installed FROM duckdb_extensions() "
            "WHERE extension_name IN ('httpfs','postgres_scanner','aws')"
        ).fetchall()
    )
    if installed != {"aws": True, "httpfs": True, "postgres_scanner": True}:
        raise RuntimeError("offline DuckDB extension preload is incomplete")
    loaded = {
        str(row[0])
        for row in connection.execute(
            "SELECT extension_name FROM duckdb_extensions() WHERE loaded"
        ).fetchall()
    }
    if "aws" in loaded:
        raise RuntimeError("AWS extension must be lazy-loaded only for role credentials")
    engine._pg_attach(connection)
    pg_value = connection.execute(
        "SELECT value FROM pgdb.offline_extension_probe"
    ).fetchone()
    s3_value = connection.execute(
        "SELECT value FROM read_parquet('s3://lakehouse/offline/probe.parquet')"
    ).fetchone()
    if pg_value != (17,) or s3_value != (23,):
        raise RuntimeError("offline DuckDB extension smoke returned invalid data")


if __name__ == "__main__":
    main()
