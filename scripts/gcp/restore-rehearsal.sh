#!/usr/bin/env bash
# Restore a GCP backup into isolated, ephemeral database volumes and verify the
# exact GCS generations without touching the live Compose project.
set -Eeuo pipefail
set +x
umask 077

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: restore-rehearsal.sh must run through sudo" >&2
  exit 10
fi

MANIFEST_URI="${1:-}"
MANIFEST_SHA256="${2:-}"
OBJECT_VERIFY_MODE="${3:-all}"
APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
WORKDIR=""
OP_CONTAINER=""
GOLD_CONTAINER=""
OP_VOLUME=""
GOLD_VOLUME=""

emit() {
  local name="$1" status="$2" evidence="${3:-}"
  evidence="${evidence//$'\t'/ }"
  evidence="${evidence//$'\r'/ }"
  evidence="${evidence//$'\n'/ }"
  printf 'OMEGA_GCP_REHEARSAL_CHECK\t%s\t%s\t%s\n' "$name" "$status" "$evidence"
}

fail() {
  emit "$1" "FAIL" "${2:-}"
  exit "${3:-20}"
}

if [[ ! "$MANIFEST_URI" =~ ^gs://[^/]+/_omega_backups/[^/]+/manifest\.json$ ]]; then
  fail "backup manifest URI" "unexpected URI" 20
fi
if [[ ! "$MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "backup manifest checksum" "invalid sha256" 21
fi
if [[ "$OBJECT_VERIFY_MODE" != "all" && "$OBJECT_VERIFY_MODE" != "sample" ]]; then
  fail "object verification mode" "expected all or sample" 22
fi

cleanup() {
  local rc=$?
  trap - EXIT
  for container in "$OP_CONTAINER" "$GOLD_CONTAINER"; do
    if [[ -n "$container" && "$container" == omega_gcp_rehearsal_* ]]; then
      docker rm -f "$container" >/dev/null 2>&1 || true
    fi
  done
  for volume in "$OP_VOLUME" "$GOLD_VOLUME"; do
    if [[ -n "$volume" && "$volume" == omega_gcp_rehearsal_* ]]; then
      docker volume rm "$volume" >/dev/null 2>&1 || true
    fi
  done
  if [[ -n "$WORKDIR" && "$WORKDIR" == /tmp/omega-gcp-rehearsal.* ]]; then
    rm -rf -- "$WORKDIR"
  fi
  exit "$rc"
}
trap cleanup EXIT

exec 9>"/var/lock/omega-gcp-day2.lock"
if ! flock -n 9; then
  fail "exclusive day-2 lock" "another release/backup operation is active" 23
fi
WORKDIR="$(mktemp -d /tmp/omega-gcp-rehearsal.XXXXXX)"

gcs_download() {
  python3 - "$1" "$2" "${3:-}" <<'PY'
import json
import pathlib
import sys
import urllib.parse
import urllib.request

uri, destination, generation = sys.argv[1:]
parsed = urllib.parse.urlparse(uri)
if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.lstrip("/"):
    raise SystemExit("invalid GCS URI")
token_request = urllib.request.Request(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
)
with urllib.request.urlopen(token_request, timeout=10) as response:
    token = json.load(response)["access_token"]
bucket = urllib.parse.quote(parsed.netloc, safe="")
key = urllib.parse.quote(parsed.path.lstrip("/"), safe="")
params = {"alt": "media"}
if generation:
    params["generation"] = generation
url = f"https://storage.googleapis.com/download/storage/v1/b/{bucket}/o/{key}?{urllib.parse.urlencode(params)}"
request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
path = pathlib.Path(destination)
with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as target:
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        target.write(chunk)
PY
}

# pg_dumpall --clean emits DROP/CREATE for the bootstrap role used by psql.
# Dropping the current session user is impossible, while retaining CREATE after
# omitting DROP fails because the role already exists.  Filter only those two
# byte-exact statements, and only inside pg_dumpall's global role sections.
# Once the Databases section begins, identical application data is preserved.
filter_pg_dump_bootstrap_role() {
  python3 -c '
import sys

drop_header = b"-- Drop roles\n"
roles_header = b"-- Roles\n"
user_config_header = b"-- User Configurations\n"
databases_header = b"-- Databases\n"
drop_statement = b"DROP ROLE IF EXISTS postgres;\n"
create_statement = b"CREATE ROLE postgres;\n"
section = "header"
database_phase = False
drop_count = 0
create_count = 0

for line in sys.stdin.buffer:
    if not database_phase:
        if line == databases_header:
            database_phase = True
            section = "database"
        elif line == drop_header:
            section = "drop"
        elif line == roles_header:
            section = "roles"
        elif line == user_config_header:
            section = "user_config"
    if not database_phase and section == "drop" and line == drop_statement:
        drop_count += 1
        continue
    if not database_phase and section == "roles" and line == create_statement:
        create_count += 1
        continue
    sys.stdout.buffer.write(line)

if drop_count != 1 or create_count != 1:
    sys.stderr.write("bootstrap role filter expected one exact DROP and CREATE statement\n")
    raise SystemExit(42)
'
}

MANIFEST="${WORKDIR}/manifest.json"
gcs_download "$MANIFEST_URI" "$MANIFEST"
ACTUAL_MANIFEST_SHA="$(sha256sum "$MANIFEST" | awk '{print $1}')"
if [[ "$ACTUAL_MANIFEST_SHA" != "$MANIFEST_SHA256" ]]; then
  fail "backup manifest checksum" "actual=${ACTUAL_MANIFEST_SHA}" 24
fi
python3 - "$MANIFEST" <<'PY'
import json, re, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
if payload.get("schema_version") != 1 or payload.get("complete") is not True:
    raise SystemExit("manifest is not complete schema v1")
if payload.get("plaintext_runtime_secrets_included") is not False:
    raise SystemExit("backup must explicitly exclude plaintext runtime secrets")
if not payload.get("object_storage", {}).get("versioning_enabled"):
    raise SystemExit("object storage restore point is not versioned")
for name in ("postgres", "postgres_gold", "object_manifest"):
    artifact = payload.get("artifacts", {}).get(name, {})
    if not re.fullmatch(r"[0-9a-f]{64}", artifact.get("sha256", "")):
        raise SystemExit(f"invalid {name} sha256")
    if not str(artifact.get("generation", "")).isdigit():
        raise SystemExit(f"invalid {name} generation")
PY
emit "backup manifest checksum" "PASS" "sha256=${ACTUAL_MANIFEST_SHA}"

read_manifest_field() {
  python3 - "$MANIFEST" "$1" "$2" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(payload["artifacts"][sys.argv[2]][sys.argv[3]])
PY
}

OP_URI="$(read_manifest_field postgres uri)"
OP_GENERATION="$(read_manifest_field postgres generation)"
OP_SHA="$(read_manifest_field postgres sha256)"
GOLD_URI="$(read_manifest_field postgres_gold uri)"
GOLD_GENERATION="$(read_manifest_field postgres_gold generation)"
GOLD_SHA="$(read_manifest_field postgres_gold sha256)"
OBJECT_URI="$(read_manifest_field object_manifest uri)"
OBJECT_GENERATION="$(read_manifest_field object_manifest generation)"
OBJECT_SHA="$(read_manifest_field object_manifest sha256)"

gcs_download "$OP_URI" "${WORKDIR}/postgres.sql.gz" "$OP_GENERATION"
gcs_download "$GOLD_URI" "${WORKDIR}/postgres_gold.sql.gz" "$GOLD_GENERATION"
gcs_download "$OBJECT_URI" "${WORKDIR}/lakehouse_objects.jsonl" "$OBJECT_GENERATION"
for item in "postgres.sql.gz:${OP_SHA}" "postgres_gold.sql.gz:${GOLD_SHA}" "lakehouse_objects.jsonl:${OBJECT_SHA}"; do
  filename="${item%%:*}"
  expected="${item#*:}"
  actual="$(sha256sum "${WORKDIR}/${filename}" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    fail "backup artifact checksum" "artifact=${filename} actual=${actual}" 25
  fi
done
gzip -t "${WORKDIR}/postgres.sql.gz"
gzip -t "${WORKDIR}/postgres_gold.sql.gz"
for dump in "${WORKDIR}/postgres.sql.gz" "${WORKDIR}/postgres_gold.sql.gz"; do
  gzip -dc "$dump" | filter_pg_dump_bootstrap_role >/dev/null
done
emit "bootstrap role restore filter" "PASS" "one exact DROP and CREATE omitted per dump; ALTER and database payload preserved"
emit "backup artifact checksums" "PASS" "logical dumps and object manifest exact generations verified"

OBJECT_RESULT="$(python3 - "$MANIFEST" "${WORKDIR}/lakehouse_objects.jsonl" "$OBJECT_VERIFY_MODE" <<'PY'
import concurrent.futures
import json
import sys
import time
import urllib.parse
import urllib.request

manifest_path, object_path, mode = sys.argv[1:]
manifest = json.load(open(manifest_path, encoding="utf-8"))
bucket = manifest["object_storage"]["bucket"]
rows = []
last_key = ""
total_bytes = 0
with open(object_path, encoding="utf-8") as stream:
    for line in stream:
        row = json.loads(line)
        key = row.get("key", "")
        if not key or key <= last_key or not str(row.get("generation", "")).isdigit():
            raise SystemExit("object manifest key/generation ordering is invalid")
        if not row.get("crc32c") and not row.get("md5"):
            raise SystemExit(f"object lacks an available checksum: {key}")
        row["size_bytes"] = int(row["size_bytes"])
        total_bytes += row["size_bytes"]
        rows.append(row)
        last_key = key
expected = manifest["object_storage"]
if len(rows) != int(expected["object_count"]) or total_bytes != int(expected["total_bytes"]):
    raise SystemExit("object manifest count/size differs from backup manifest")
if not rows:
    raise SystemExit("canonical lakehouse restore manifest is unexpectedly empty")

if mode == "sample" and len(rows) > 3:
    verify_rows = [rows[0], rows[len(rows) // 2], rows[-1]]
else:
    verify_rows = rows

token_request = urllib.request.Request(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
)
with urllib.request.urlopen(token_request, timeout=10) as response:
    token = json.load(response)["access_token"]
headers = {"Authorization": f"Bearer {token}"}
quoted_bucket = urllib.parse.quote(bucket, safe="")

def verify(row):
    key = urllib.parse.quote(row["key"], safe="")
    params = urllib.parse.urlencode({
        "generation": row["generation"],
        "fields": "name,size,generation,md5Hash,crc32c",
    })
    url = f"https://storage.googleapis.com/storage/v1/b/{quoted_bucket}/o/{key}?{params}"
    last_error = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                actual = json.load(response)
            if str(actual.get("generation")) != row["generation"]:
                raise RuntimeError("generation mismatch")
            if int(actual.get("size", -1)) != row["size_bytes"]:
                raise RuntimeError("size mismatch")
            if row.get("crc32c") and actual.get("crc32c") != row["crc32c"]:
                raise RuntimeError("crc32c mismatch")
            if row.get("md5") and actual.get("md5Hash") != row["md5"]:
                raise RuntimeError("md5 mismatch")
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"{row['key']}#{row['generation']}: {last_error}")

with concurrent.futures.ThreadPoolExecutor(max_workers=24) as executor:
    futures = [executor.submit(verify, row) for row in verify_rows]
    for future in concurrent.futures.as_completed(futures):
        future.result()
print(json.dumps({"objects": len(rows), "bytes": total_bytes, "verified_generations": len(verify_rows), "mode": mode}))
PY
)"
emit "object-generation restore points" "PASS" "$OBJECT_RESULT"

BACKUP_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["backup_id"])' "$MANIFEST")"
SUFFIX="$(printf '%s' "${BACKUP_ID}-${MANIFEST_SHA256}" | sha256sum | cut -c1-16)_$$"
OP_CONTAINER="omega_gcp_rehearsal_${SUFFIX}_op"
GOLD_CONTAINER="omega_gcp_rehearsal_${SUFFIX}_gold"
OP_VOLUME="omega_gcp_rehearsal_${SUFFIX}_op"
GOLD_VOLUME="omega_gcp_rehearsal_${SUFFIX}_gold"
OP_IMAGE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["database_images"]["postgres"])' "$MANIFEST")"
GOLD_IMAGE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["database_images"]["postgres_gold"])' "$MANIFEST")"
if [[ ! "$OP_IMAGE" =~ ^pgvector/pgvector:(pg15|[0-9A-Za-z._-]+)$ ]]; then
  fail "isolated database image" "unexpected operational image" 26
fi
if [[ ! "$GOLD_IMAGE" =~ ^postgres:15([.-][0-9A-Za-z._-]+)?$ ]]; then
  fail "isolated database image" "unexpected Gold image" 27
fi

docker volume create "$OP_VOLUME" >/dev/null
docker volume create "$GOLD_VOLUME" >/dev/null
docker run -d --name "$OP_CONTAINER" --network none \
  --mount "type=volume,source=${OP_VOLUME},target=/var/lib/postgresql/data" \
  -e POSTGRES_HOST_AUTH_METHOD=trust "$OP_IMAGE" >/dev/null
docker run -d --name "$GOLD_CONTAINER" --network none \
  --mount "type=volume,source=${GOLD_VOLUME},target=/var/lib/postgresql/data" \
  -e POSTGRES_HOST_AUTH_METHOD=trust "$GOLD_IMAGE" -p 5433 >/dev/null

for pair in "${OP_CONTAINER}:5432" "${GOLD_CONTAINER}:5433"; do
  container="${pair%%:*}"
  port="${pair#*:}"
  ready=0
  for _ in $(seq 1 60); do
    if docker exec "$container" pg_isready -h 127.0.0.1 -p "$port" -U postgres >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 2
  done
  if [[ "$ready" != "1" ]]; then
    fail "isolated database readiness" "container=${container}" 28
  fi
done

gzip -dc "${WORKDIR}/postgres.sql.gz" \
  | filter_pg_dump_bootstrap_role \
  | docker exec -i "$OP_CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d postgres >/dev/null
gzip -dc "${WORKDIR}/postgres_gold.sql.gz" \
  | filter_pg_dump_bootstrap_role \
  | docker exec -i "$GOLD_CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d postgres -p 5433 >/dev/null

OP_MIGRATIONS="$(docker exec "$OP_CONTAINER" psql -At -U postgres -d modecissions \
  -c 'SELECT count(*) FROM schema_migrations;' | tr -d '\r')"
GOLD_MIGRATIONS="$(docker exec "$GOLD_CONTAINER" psql -At -U postgres -d modecissions_gold -p 5433 \
  -c 'SELECT count(*) FROM schema_migrations;' | tr -d '\r')"
if [[ ! "$OP_MIGRATIONS" =~ ^[1-9][0-9]*$ || ! "$GOLD_MIGRATIONS" =~ ^[1-9][0-9]*$ ]]; then
  fail "isolated logical restore" "schema_migrations missing after restore" 29
fi
PIPELINE_RUNS="$(docker exec "$OP_CONTAINER" psql -At -U postgres -d modecissions \
  -c 'SELECT count(*) FROM pipeline_runs;' | tr -d '\r')"
DATASETS="$(docker exec "$OP_CONTAINER" psql -At -U postgres -d modecissions \
  -c 'SELECT count(*) FROM datasets;' | tr -d '\r')"
GOLD_TABLES="$(docker exec "$GOLD_CONTAINER" psql -At -U postgres -d modecissions_gold -p 5433 \
  -c "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema');" | tr -d '\r')"
for value in "$PIPELINE_RUNS" "$DATASETS" "$GOLD_TABLES"; do
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    fail "isolated logical restore" "restored business/schema cardinality is unexpectedly empty" 30
  fi
done
emit "isolated logical restore" "PASS" "operational_migrations=${OP_MIGRATIONS} gold_migrations=${GOLD_MIGRATIONS} pipeline_runs=${PIPELINE_RUNS} datasets=${DATASETS} gold_tables=${GOLD_TABLES} live_volumes_untouched=true"
printf 'OMEGA_GCP_REHEARSAL_JSON={"status":"PASS","backup_id":"%s","operational_migrations":%s,"gold_migrations":%s,"pipeline_runs":%s,"datasets":%s,"gold_tables":%s,"object_verify_mode":"%s"}\n' \
  "$BACKUP_ID" "$OP_MIGRATIONS" "$GOLD_MIGRATIONS" "$PIPELINE_RUNS" "$DATASETS" "$GOLD_TABLES" "$OBJECT_VERIFY_MODE"
