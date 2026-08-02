#!/usr/bin/env bash
set -euo pipefail

duckdb_home="${DUCKDB_TEST_HOME:?DUCKDB_TEST_HOME is required}"
image="${REFINEMENT_IMAGE:?REFINEMENT_IMAGE is required}"
if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin="$PYTHON_BIN"
elif [[ -x .venv/bin/python ]]; then
  python_bin=.venv/bin/python
else
  python_bin=python
fi
command -v "$python_bin" >/dev/null
test ! -e "$duckdb_home"

docker build . -f refinement/Dockerfile -t "$image"
docker run --rm -i --network none --read-only \
  --tmpfs /tmp:rw,nosuid,noexec,size=64m \
  "$image" python - <<'PY'
import getpass

import duckdb
from app.duckdb_runtime import connect_duckdb_runtime, require_loaded_extensions

assert getpass.getuser() == "refinement-app"
assert duckdb.__version__ == "1.2.2"
connection = connect_duckdb_runtime()
try:
    require_loaded_extensions(connection)
    settings = dict(
        connection.execute(
            "SELECT name, value FROM duckdb_settings() "
            "WHERE name IN ("
            "'autoinstall_known_extensions',"
            "'autoload_known_extensions')"
        ).fetchall()
    )
    assert settings == {
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
    }
finally:
    connection.close()
PY
scripts/run_refinement_duckdb_offline_smoke.sh "$image"

mkdir -p "$duckdb_home/.duckdb"
container_id="$(docker create "$image")"
cleanup() {
  docker rm -f "$container_id" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker cp "$container_id:/home/refinement-app/.duckdb/." "$duckdb_home/.duckdb/"
docker rm "$container_id" >/dev/null
trap - EXIT

for extension in httpfs postgres_scanner; do
  find "$duckdb_home/.duckdb" -type f -name "$extension.duckdb_extension" \
    | grep -q .
done
if [[ "$(uname -s)" == "Darwin" ]]; then
  docker run --rm -i --network none --read-only \
    --tmpfs /tmp:rw,nosuid,noexec,size=64m \
    -v "$duckdb_home/.duckdb:/home/refinement-app/.duckdb:ro" \
    "$image" python - <<'PY'
from app.duckdb_runtime import connect_duckdb_runtime, require_loaded_extensions

connection = connect_duckdb_runtime()
try:
    require_loaded_extensions(connection)
finally:
    connection.close()
PY
else
  HOME="$duckdb_home" "$python_bin" - <<'PY'
from refinement.app.duckdb_runtime import (
    connect_duckdb_runtime,
    require_loaded_extensions,
)

connection = connect_duckdb_runtime()
try:
    require_loaded_extensions(connection)
finally:
    connection.close()
PY
fi
find "$duckdb_home/.duckdb" -type f -print0 \
  | LC_ALL=C sort -z | xargs -0 sha256sum \
  > /tmp/refinement-duckdb-extensions.before
