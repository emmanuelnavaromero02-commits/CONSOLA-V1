#!/usr/bin/env bash
set -euo pipefail

image="${1:?refinement image tag is required}"
suffix="${RANDOM}${RANDOM}"
network="omega-duckdb-offline-${suffix}"
postgres="omega-duckdb-postgres-${suffix}"
minio="omega-duckdb-minio-${suffix}"
minio_image="ghcr.io/emmanuelnavaromero02-commits/minio:RELEASE.2024-12-18T13-15-44Z@sha256:f7e035122f930f0c8e120447513bbc550809fd4e909e4304a203fd6e605b1dad"

cleanup() {
  docker rm -f "$postgres" "$minio" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --pull never --rm --network none --read-only --tmpfs /tmp:rw,nosuid,noexec,size=64m "$image" sh -c \
  'test "$(id -u)" -ne 0 && python -c "from app.duckdb_runtime import connect_duckdb_runtime,require_loaded_extensions;c=connect_duckdb_runtime();require_loaded_extensions(c)"'

docker network create --internal "$network" >/dev/null
docker run --pull never -d --rm --name "$postgres" --network "$network" \
  -e POSTGRES_DB=modecissions -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=offline-postgres postgres:15 >/dev/null
docker run --pull never -d --rm --name "$minio" --network "$network" \
  -e MINIO_ROOT_USER=minio -e MINIO_ROOT_PASSWORD=offline-minio-secret \
  "$minio_image" server /data >/dev/null

docker run --pull never --rm --network "$network" \
  -e DATABASE_URL="postgresql://postgres:offline-postgres@${postgres}:5432/modecissions" \
  -e GOLD_DATABASE_URL="postgresql://postgres:offline-postgres@${postgres}:5432/modecissions" \
  -e MINIO_ENDPOINT="${minio}:9000" -e MINIO_ACCESS_KEY=minio \
  -e MINIO_SECRET_KEY=offline-minio-secret -e MINIO_BUCKET=lakehouse \
  -e MINIO_SECURE=false "$image" python -m scripts.duckdb_offline_smoke
