#!/usr/bin/env bash
set -euo pipefail

image="${1:?Replicon image tag is required}"
minio_image="quay.io/minio/minio:RELEASE.2024-12-18T13-15-44Z@sha256:1dce27c494a16bae114774f1cec295493f3613142713130c2d22dd5696be6ad3"
suffix="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-$$"
network="replicon-httpfs-${suffix}"
minio_name="replicon-minio-${suffix}"
object_path="raw/replicon/TimeEntry/load_date=2026-07-31/data.parquet"

cleanup() {
  docker rm -f "$minio_name" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --rm -i \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --user appuser \
  --env MINIO_ACCESS_KEY=ci-access \
  --env MINIO_SECRET_KEY=ci-secret-key \
  --env PG_USER=ci-user \
  --env PG_PASSWORD=ci-password \
  --entrypoint python \
  "$image" - <<'PY'
import duckdb
from app.services import duckdb_service

assert duckdb.__version__ == "1.2.2"
conn = duckdb_service._get_duckdb_connection(
    "SELECT * FROM read_parquet('s3://ci-bucket/raw/replicon/TimeEntry/data.parquet')"
)
try:
    settings = {
        name: conn.execute("SELECT current_setting(?)", [name]).fetchone()[0]
        for name in (
            "autoinstall_known_extensions",
            "autoload_known_extensions",
        )
    }
    assert settings == {
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
    }
finally:
    conn.close()
PY

docker pull "$minio_image"
docker network create --internal "$network"
docker run --detach --rm \
  --name "$minio_name" \
  --network "$network" \
  --network-alias minio \
  --read-only \
  --tmpfs /data:rw,noexec,nosuid,size=128m \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --env MINIO_ROOT_USER=ci-access \
  --env MINIO_ROOT_PASSWORD=ci-secret-key \
  "$minio_image" server /data --console-address ":9001"

docker run --rm -i \
  --name "seed-${suffix}" \
  --network "$network" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --user appuser \
  --env MINIO_ENDPOINT=minio:9000 \
  --env MINIO_ACCESS_KEY=ci-access \
  --env MINIO_SECRET_KEY=ci-secret-key \
  --env MINIO_BUCKET=ci-bucket \
  --env PG_USER=ci-user \
  --env PG_PASSWORD=ci-password \
  --env OBJECT_PATH="$object_path" \
  --entrypoint python \
  "$image" - <<'PY'
import os
import time
from pathlib import Path

import pandas as pd
from app.core.minio_client import get_minio_client

client = get_minio_client()
for attempt in range(40):
    try:
        if not client.bucket_exists("ci-bucket"):
            client.make_bucket("ci-bucket")
        break
    except Exception:
        if attempt == 39:
            raise
        time.sleep(0.25)
frame = pd.DataFrame(
    [
        {"project_id": "alpha", "hours": 2.5},
        {"project_id": "beta", "hours": 7.0},
    ]
)
path = Path("/tmp/data.parquet")
frame.to_parquet(path, index=False)
client.fput_object("ci-bucket", os.environ["OBJECT_PATH"], str(path))
PY

run_reader() {
  local case_name="$1"
  local access_key="ci-access"
  if [[ "$case_name" == "invalid_credentials" ]]; then
    access_key="invalid-access"
  fi
  docker run --rm -i \
    --name "${case_name}-${suffix}" \
    --network "$network" \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=32m,mode=1777 \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --user appuser \
    --env MINIO_ENDPOINT=minio:9000 \
    --env MINIO_ACCESS_KEY="$access_key" \
    --env MINIO_SECRET_KEY=ci-secret-key \
    --env MINIO_BUCKET=ci-bucket \
    --env PG_USER=ci-user \
    --env PG_PASSWORD=ci-password \
    --env SMOKE_CASE="$case_name" \
    --env OBJECT_PATH="$object_path" \
    --entrypoint python \
    "$image" - <<'PY'
import getpass
import os

import duckdb
from app.services import duckdb_service

assert os.geteuid() != 0
assert getpass.getuser() == "appuser"
assert duckdb.__version__ == "1.2.2"
case = os.environ["SMOKE_CASE"]
path = os.environ["OBJECT_PATH"]
if case in {"reader-1", "reader-2"}:
    sql = (
        "SELECT project_id, hours FROM read_parquet("
        f"'s3://ci-bucket/{path}') ORDER BY project_id"
    )
    expected_rows = [
        {"project_id": "alpha", "hours": 2.5},
        {"project_id": "beta", "hours": 7.0},
    ]
    assert duckdb_service.run_kb_sql(sql).to_dict("records") == expected_rows
    conn = duckdb_service._get_duckdb_connection(sql)
    try:
        assert conn.execute(
            "SELECT current_setting('s3_url_style')"
        ).fetchone()[0] == "path"
    finally:
        conn.close()
else:
    if case == "missing_object":
        query = "SELECT * FROM read_parquet('s3://ci-bucket/missing.parquet')"
    elif case == "wrong_schema":
        query = (
            "SELECT secret_column FROM read_parquet("
            f"'s3://ci-bucket/{path}')"
        )
    else:
        query = (
            "SELECT * FROM read_parquet("
            f"'s3://ci-bucket/{path}')"
        )
    try:
        duckdb_service.run_kb_sql(query)
    except duckdb_service.DuckDBHTTPFSUnavailable as exc:
        assert str(exc) == "DuckDB remote source unavailable"
        assert exc.__cause__ is None
    else:
        raise AssertionError(f"{case} did not fail closed")
PY
}

run_reader reader-1
run_reader reader-2
run_reader invalid_credentials
run_reader missing_object
run_reader wrong_schema

docker run --rm -i \
  --name "inventory-${suffix}" \
  --network "$network" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m,mode=1777 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --user appuser \
  --env MINIO_ENDPOINT=minio:9000 \
  --env MINIO_ACCESS_KEY=ci-access \
  --env MINIO_SECRET_KEY=ci-secret-key \
  --env MINIO_BUCKET=ci-bucket \
  --env PG_USER=ci-user \
  --env PG_PASSWORD=ci-password \
  --env OBJECT_PATH="$object_path" \
  --entrypoint python \
  "$image" - <<'PY'
import os
from app.core.minio_client import get_minio_client

objects = [
    item.object_name
    for item in get_minio_client().list_objects("ci-bucket", recursive=True)
]
assert objects == [os.environ["OBJECT_PATH"]], objects
assert not any(
    token in item
    for item in objects
    for token in ("gold", "current", "receipt", "head")
)
PY
