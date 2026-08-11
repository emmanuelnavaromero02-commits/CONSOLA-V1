#!/usr/bin/env bash
# Create a writer-fenced, immutable pre-deploy backup on the canonical GCP VM.
set -Eeuo pipefail
set +x
umask 077

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: backup.sh must run through sudo" >&2
  exit 10
fi

LAKEHOUSE_BUCKET="${1:-}"
BACKUP_ID="${2:-}"
COMPOSE_PROJECT="${3:-infra}"
APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
CURRENT_LINK="${APP_ROOT}/current"
SHARED_ROOT="${APP_ROOT}/shared"
SHARED_ENV="${SHARED_ROOT}/infra.env"
GCP_RUNTIME_COMPOSE="${SHARED_ROOT}/docker-compose.gcp.yml"
BACKUP_PREFIX="_omega_backups/${BACKUP_ID}"
WORKDIR=""
RUNTIME_FENCED=0
RUNNING_BEFORE=()

emit() {
  local name="$1" status="$2" evidence="${3:-}"
  evidence="${evidence//$'\t'/ }"
  evidence="${evidence//$'\r'/ }"
  evidence="${evidence//$'\n'/ }"
  printf 'OMEGA_GCP_BACKUP_CHECK\t%s\t%s\t%s\n' "$name" "$status" "$evidence"
}

fail() {
  emit "$1" "FAIL" "${2:-}"
  exit "${3:-20}"
}

if [[ ! "$LAKEHOUSE_BUCKET" =~ ^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$ ]]; then
  fail "lakehouse bucket" "invalid bucket name" 20
fi
if [[ ! "$BACKUP_ID" =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9.-]{0,80}$ ]]; then
  fail "immutable backup id" "expected UTC timestamp and release label" 21
fi
if [[ ! "$COMPOSE_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  fail "Compose project" "invalid project name" 22
fi
if [[ ! -L "$CURRENT_LINK" ]]; then
  fail "canonical runtime inputs" "current release link missing" 23
fi

CURRENT_RELEASE="$(readlink -f "$CURRENT_LINK")"
case "$CURRENT_RELEASE" in
  "${APP_ROOT}/releases/"*) ;;
  *) fail "current release confinement" "current points outside release root" 24 ;;
esac
BASE_COMPOSE="${CURRENT_RELEASE}/infra/docker-compose.yml"
CURRENT_ENV="${CURRENT_RELEASE}/infra/.env"
CURRENT_GCP_COMPOSE="${CURRENT_RELEASE}/infra/docker-compose.gcp.yml"
if [[ ! -s "$BASE_COMPOSE" || ! -s "$CURRENT_ENV" || ! -s "$CURRENT_GCP_COMPOSE" ]]; then
  fail "current runtime inputs" "base Compose, runtime env, or generated GCP override missing" 25
fi

exec 9>"/var/lock/omega-gcp-day2.lock"
if ! flock -n 9; then
  fail "exclusive day-2 lock" "another release/backup operation is active" 26
fi

install -d -m 0755 "$SHARED_ROOT"
if grep -Eq '^(GHCR_[A-Z0-9_]*(TOKEN|PASSWORD|SECRET|CREDENTIAL|AUTH|USER)|GITHUB_TOKEN|DOCKER_AUTH_CONFIG)=' "$CURRENT_ENV"; then
  fail "server-owned registry credential boundary" "registry credential found in runtime env" 26
fi
SHARED_ENV_TMP="${SHARED_ROOT}/.infra.env.backup.$$"
install -m 0600 "$CURRENT_ENV" "$SHARED_ENV_TMP"
mv -Tf "$SHARED_ENV_TMP" "$SHARED_ENV"
GCP_COMPOSE_TMP="${SHARED_ROOT}/.docker-compose.gcp.backup.$$"
install -m 0600 "$CURRENT_GCP_COMPOSE" "$GCP_COMPOSE_TMP"
mv -Tf "$GCP_COMPOSE_TMP" "$GCP_RUNTIME_COMPOSE"
COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
  -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" --profile sap)
"${COMPOSE[@]}" config -q
emit "shared runtime inputs" "PASS" "refreshed from current release without registry credentials"

WRITER_SERVICES=(airflow-scheduler console workspace refinement vault mcp-infra airflow replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-successfactors sap-s4hana superset)
SCHEDULER_COUNT="$(docker ps -q --filter 'label=com.docker.compose.service=airflow-scheduler' | grep -c . || true)"
if [[ "$SCHEDULER_COUNT" != "1" ]]; then
  fail "canonical scheduler baseline" "expected one scheduler, found=${SCHEDULER_COUNT}" 27
fi
SCHEDULER_ID="$(docker ps -q --filter 'label=com.docker.compose.service=airflow-scheduler')"
SCHEDULER_PROJECT="$(docker inspect "$SCHEDULER_ID" --format '{{index .Config.Labels "com.docker.compose.project"}}')"
if [[ "$SCHEDULER_PROJECT" != "$COMPOSE_PROJECT" ]]; then
  fail "canonical scheduler baseline" "scheduler belongs to project=${SCHEDULER_PROJECT}" 27
fi
for container in mode_postgres mode_postgres_gold; do
  actual_project="$(docker inspect "$container" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || true)"
  if [[ "$actual_project" != "$COMPOSE_PROJECT" ]]; then
    fail "Compose project identity" "${container} project=${actual_project:-missing}" 27
  fi
done

for service in "${WRITER_SERVICES[@]}"; do
  if docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
      --filter "label=com.docker.compose.service=${service}" | grep -q .; then
    RUNNING_BEFORE+=("$service")
  fi
done

wait_for_scheduler() {
  local scheduler_id count
  for _ in $(seq 1 60); do
    scheduler_id="$(docker ps -q --filter 'label=com.docker.compose.service=airflow-scheduler')"
    count="$(grep -c . <<<"$scheduler_id" || true)"
    if [[ "$count" == "1" ]] && \
        [[ "$(docker inspect "$scheduler_id" --format '{{.State.Health.Status}}' 2>/dev/null || true)" == "healthy" ]]; then
      return 0
    fi
    sleep 3
  done
  return 1
}

restore_runtime() {
  local rc=$?
  trap - EXIT
  if [[ "$RUNTIME_FENCED" == "1" && "${#RUNNING_BEFORE[@]}" -gt 0 ]]; then
    if ! "${COMPOSE[@]}" start "${RUNNING_BEFORE[@]}" >/dev/null; then
      emit "runtime restored after backup" "FAIL" "one or more previously-running services did not restart"
      rc=70
    else
      if ! wait_for_scheduler; then
        emit "runtime restored after backup" "FAIL" "sole scheduler did not return healthy"
        rc=71
      else
        emit "runtime restored after backup" "PASS" "previous service state restored; scheduler=1 healthy"
      fi
    fi
  fi
  if [[ -n "$WORKDIR" && "$WORKDIR" == /tmp/omega-gcp-backup.* ]]; then
    rm -rf -- "$WORKDIR"
  fi
  exit "$rc"
}
trap restore_runtime EXIT

WORKDIR="$(mktemp -d /tmp/omega-gcp-backup.XXXXXX)"
RUNTIME_FENCED=1
"${COMPOSE[@]}" stop --timeout 60 "${WRITER_SERVICES[@]}"
for service in "${WRITER_SERVICES[@]}"; do
  if docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
      --filter "label=com.docker.compose.service=${service}" | grep -q .; then
    fail "writer fence" "service still running=${service}" 28
  fi
done
emit "writer and scheduler fence" "PASS" "all known application writers stopped"

for pair in "mode_postgres:5432" "mode_postgres_gold:5433"; do
  container="${pair%%:*}"
  port="${pair#*:}"
  quiet=0
  for _ in $(seq 1 30); do
    client_count="$(docker exec "$container" psql -At -U postgres -d postgres -p "$port" \
      -c "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' AND pid <> pg_backend_pid();" | tr -d '\r')"
    if [[ "$client_count" == "0" ]]; then
      quiet=1
      break
    fi
    sleep 1
  done
  if [[ "$quiet" != "1" ]]; then
    fail "database writer quiescence" "${container} still has client sessions" 29
  fi
done
emit "database writer quiescence" "PASS" "operational and Gold have zero external client sessions"

docker exec mode_postgres pg_dumpall -U postgres --clean --if-exists \
  | gzip -9 > "${WORKDIR}/postgres.sql.gz"
docker exec mode_postgres_gold pg_dumpall -U postgres -p 5433 --clean --if-exists \
  | gzip -9 > "${WORKDIR}/postgres_gold.sql.gz"
gzip -t "${WORKDIR}/postgres.sql.gz"
gzip -t "${WORKDIR}/postgres_gold.sql.gz"
if [[ ! -s "${WORKDIR}/postgres.sql.gz" || ! -s "${WORKDIR}/postgres_gold.sql.gz" ]]; then
  fail "logical database snapshots" "one or more dumps are empty" 29
fi
emit "logical database snapshots" "PASS" "operational and Gold dumps completed under writer fence"

python3 - "$LAKEHOUSE_BUCKET" "$BACKUP_PREFIX" "${WORKDIR}/lakehouse_objects.jsonl" "${WORKDIR}/bucket.json" <<'PY'
import hashlib
import json
import pathlib
import sys
import urllib.parse
import urllib.request

bucket, excluded_prefix, output_name, bucket_output = sys.argv[1:]
token_request = urllib.request.Request(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
)
with urllib.request.urlopen(token_request, timeout=10) as response:
    token = json.load(response)["access_token"]
headers = {"Authorization": f"Bearer {token}"}
quoted_bucket = urllib.parse.quote(bucket, safe="")

bucket_request = urllib.request.Request(
    f"https://storage.googleapis.com/storage/v1/b/{quoted_bucket}?fields=name,location,versioning",
    headers=headers,
)
with urllib.request.urlopen(bucket_request, timeout=30) as response:
    bucket_payload = json.load(response)
if bucket_payload.get("versioning", {}).get("enabled") is not True:
    raise SystemExit("GCS versioning must be enabled before backup")
pathlib.Path(bucket_output).write_text(
    json.dumps(bucket_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)

fields = "nextPageToken,items(name,size,generation,metageneration,md5Hash,crc32c,etag,updated,storageClass)"

def objects():
    page_token = ""
    last_key = ""
    while True:
        params = {"maxResults": "1000", "fields": fields}
        if page_token:
            params["pageToken"] = page_token
        url = f"https://storage.googleapis.com/storage/v1/b/{quoted_bucket}/o?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        for item in payload.get("items", []):
            key = item["name"]
            if key.startswith("_omega_backups/"):
                continue
            if key < last_key:
                raise SystemExit("GCS listing was not monotonic")
            last_key = key
            yield {
                "key": key,
                "size_bytes": int(item.get("size", 0)),
                "generation": str(item["generation"]),
                "metageneration": str(item.get("metageneration", "")),
                "md5": item.get("md5Hash"),
                "crc32c": item.get("crc32c"),
                "etag": item.get("etag"),
                "updated": item.get("updated"),
                "storage_class": item.get("storageClass"),
            }
        page_token = payload.get("nextPageToken", "")
        if not page_token:
            break

output = pathlib.Path(output_name)
first_hash = hashlib.sha256()
count = 0
total_bytes = 0
with output.open("wb") as stream:
    for row in objects():
        encoded = (json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n").encode()
        stream.write(encoded)
        first_hash.update(encoded)
        count += 1
        total_bytes += row["size_bytes"]

# A second generation listing under the same writer fence detects concurrent
# object changes instead of blessing a torn manifest.
second_hash = hashlib.sha256()
second_count = 0
for row in objects():
    second_hash.update((json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n").encode())
    second_count += 1
if second_count != count or second_hash.digest() != first_hash.digest():
    raise SystemExit("GCS object generations changed while the backup manifest was captured")

print(json.dumps({"object_count": count, "total_bytes": total_bytes, "sha256": first_hash.hexdigest()}))
PY
# Recompute locally rather than trusting stdout plumbing; the manifest itself
# is the authoritative byte sequence.
OBJECT_COUNT="$(wc -l < "${WORKDIR}/lakehouse_objects.jsonl" | tr -d ' ')"
if [[ ! "$OBJECT_COUNT" =~ ^[1-9][0-9]*$ ]]; then
  fail "versioned object manifest" "canonical lakehouse unexpectedly contains no live objects" 29
fi
OBJECT_BYTES="$(python3 - "${WORKDIR}/lakehouse_objects.jsonl" <<'PY'
import json, sys
print(sum(json.loads(line)["size_bytes"] for line in open(sys.argv[1], encoding="utf-8")))
PY
)"
OBJECT_SHA="$(sha256sum "${WORKDIR}/lakehouse_objects.jsonl" | awk '{print $1}')"
emit "versioned object manifest" "PASS" "objects=${OBJECT_COUNT} bytes=${OBJECT_BYTES} sha256=${OBJECT_SHA} stable_passes=2"

python3 - "${WORKDIR}/runtime-images.json" "$COMPOSE_PROJECT" <<'PY'
import json
import subprocess
import sys

project = sys.argv[2]
result = subprocess.run(
    ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.ID}}"],
    text=True,
    stdout=subprocess.PIPE,
    check=True,
)
images = {}
for container_id in filter(None, result.stdout.splitlines()):
    inspect = subprocess.run(
        ["docker", "inspect", container_id, "--format", "{{json .Config.Labels}}\t{{.Config.Image}}\t{{.Image}}"],
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    labels_json, configured, image_id = inspect.split("\t", 2)
    labels = json.loads(labels_json)
    service = labels.get("com.docker.compose.service")
    if service:
        images[service] = {"configured_ref": configured, "image_id": image_id}
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"services": images, "secret_values_included": False}, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY

gcs_token() {
  curl -fsS -H 'Metadata-Flavor: Google' \
    'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
}

upload_immutable() {
  local source="$1" key="$2" metadata_output="$3" token encoded_key http_code
  token="$(gcs_token)"
  encoded_key="$(python3 - "$key" <<'PY'
import sys
from urllib.parse import quote
print(quote(sys.argv[1], safe=""))
PY
)"
  http_code="$(curl -sS -o "$metadata_output" -w '%{http_code}' \
    -X POST -H "Authorization: Bearer ${token}" -H 'Content-Type: application/octet-stream' \
    --data-binary "@${source}" \
    "https://storage.googleapis.com/upload/storage/v1/b/${LAKEHOUSE_BUCKET}/o?uploadType=media&name=${encoded_key}&ifGenerationMatch=0")"
  unset token
  if [[ "$http_code" != "200" ]]; then
    fail "immutable backup upload" "object=${key} http=${http_code}" 29
  fi
  python3 - "$metadata_output" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
if not payload.get("generation") or not payload.get("crc32c") or not payload.get("size"):
    raise SystemExit("uploaded object metadata is incomplete")
PY
}

upload_immutable "${WORKDIR}/postgres.sql.gz" "${BACKUP_PREFIX}/postgres.sql.gz" "${WORKDIR}/postgres.meta.json"
upload_immutable "${WORKDIR}/postgres_gold.sql.gz" "${BACKUP_PREFIX}/postgres_gold.sql.gz" "${WORKDIR}/postgres_gold.meta.json"
upload_immutable "${WORKDIR}/lakehouse_objects.jsonl" "${BACKUP_PREFIX}/lakehouse_objects.jsonl" "${WORKDIR}/objects.meta.json"
upload_immutable "${WORKDIR}/runtime-images.json" "${BACKUP_PREFIX}/runtime-images.json" "${WORKDIR}/images.meta.json"

SOURCE_REF="$(basename "$CURRENT_RELEASE")"
SOURCE_VERSION="$(tr -d '\r\n' < "${CURRENT_RELEASE}/VERSION")"
OP_IMAGE="$(docker inspect mode_postgres --format '{{.Config.Image}}')"
GOLD_IMAGE="$(docker inspect mode_postgres_gold --format '{{.Config.Image}}')"
python3 - "${WORKDIR}/manifest.json" "$BACKUP_ID" "$LAKEHOUSE_BUCKET" "$BACKUP_PREFIX" \
  "$SOURCE_REF" "$SOURCE_VERSION" "$OBJECT_COUNT" "$OBJECT_BYTES" "$OP_IMAGE" "$GOLD_IMAGE" \
  "${WORKDIR}/postgres.meta.json" "${WORKDIR}/postgres_gold.meta.json" \
  "${WORKDIR}/objects.meta.json" "${WORKDIR}/images.meta.json" \
  "${WORKDIR}/postgres.sql.gz" "${WORKDIR}/postgres_gold.sql.gz" \
  "${WORKDIR}/lakehouse_objects.jsonl" "${WORKDIR}/runtime-images.json" <<'PY'
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

(output, backup_id, bucket, prefix, source_ref, source_version, object_count,
 object_bytes, op_image, gold_image, *paths) = sys.argv[1:]
meta_paths = paths[:4]
local_paths = paths[4:]
names = ["postgres", "postgres_gold", "object_manifest", "runtime_images"]
keys = ["postgres.sql.gz", "postgres_gold.sql.gz", "lakehouse_objects.jsonl", "runtime-images.json"]
artifacts = {}
for name, key, meta_path, local_path in zip(names, keys, meta_paths, local_paths):
    metadata = json.load(open(meta_path, encoding="utf-8"))
    digest = hashlib.sha256(pathlib.Path(local_path).read_bytes()).hexdigest()
    artifacts[name] = {
        "uri": f"gs://{bucket}/{prefix}/{key}",
        "generation": str(metadata["generation"]),
        "size_bytes": int(metadata["size"]),
        "crc32c": metadata.get("crc32c"),
        "md5": metadata.get("md5Hash"),
        "sha256": digest,
    }
payload = {
    "schema_version": 1,
    "complete": True,
    "backup_id": backup_id,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "source_release": {"deploy_ref": source_ref, "version": source_version},
    "consistency": {
        "writers_fenced": True,
        "scheduler_fenced": True,
        "database_mode": "logical dumps while all known application writers were stopped",
        "object_manifest_passes": 2,
    },
    "object_storage": {
        "bucket": bucket,
        "versioning_enabled": True,
        "object_count": int(object_count),
        "total_bytes": int(object_bytes),
        "restore_point": "exact live object generations recorded in object_manifest",
    },
    "database_images": {"postgres": op_image, "postgres_gold": gold_image},
    "artifacts": artifacts,
    "plaintext_runtime_secrets_included": False,
    "database_contents_sensitive": True,
}
pathlib.Path(output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
MANIFEST_SHA="$(sha256sum "${WORKDIR}/manifest.json" | awk '{print $1}')"
upload_immutable "${WORKDIR}/manifest.json" "${BACKUP_PREFIX}/manifest.json" "${WORKDIR}/manifest.meta.json"
MANIFEST_GENERATION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generation"])' "${WORKDIR}/manifest.meta.json")"
emit "immutable backup manifest" "PASS" "sha256=${MANIFEST_SHA} generation=${MANIFEST_GENERATION}"

"${COMPOSE[@]}" start "${RUNNING_BEFORE[@]}" >/dev/null
if ! wait_for_scheduler; then
  fail "runtime restored after backup" "sole scheduler did not return healthy" 30
fi
RUNTIME_FENCED=0
emit "runtime restored after backup" "PASS" "previous service state restored; scheduler=1 healthy"
printf 'OMEGA_GCP_BACKUP_JSON={"status":"PASS","backup_id":"%s","manifest_uri":"gs://%s/%s/manifest.json","manifest_sha256":"%s","manifest_generation":"%s","object_count":%s}\n' \
  "$BACKUP_ID" "$LAKEHOUSE_BUCKET" "$BACKUP_PREFIX" "$MANIFEST_SHA" "$MANIFEST_GENERATION" "$OBJECT_COUNT"
