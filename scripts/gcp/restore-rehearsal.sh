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
MANIFEST_GENERATION="${2:-}"
MANIFEST_SIZE_BYTES="${3:-}"
MANIFEST_SHA256="${4:-}"
OBJECT_VERIFY_MODE="${5:-all}"
EXPECTED_SOURCE_REF="${6:-}"
EXPECTED_SOURCE_VERSION="${7:-}"
EXPECTED_BACKUP_ID="${8:-}"
BACKUP_POLICY_SHA256="${9:-}"
PROJECT_ID="${10:-}"
APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
SHARED_ENV="${APP_ROOT}/shared/infra.env"
WORKDIR=""
OP_CONTAINER=""
GOLD_CONTAINER=""
OP_VOLUME=""
GOLD_VOLUME=""
REHEARSAL_ADMIN=""
REHEARSAL_ID=""
SAFE_IO="${OMEGA_GCP_SAFE_IO:-}"

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
if [[ ! "$MANIFEST_GENERATION" =~ ^[1-9][0-9]*$ || \
      ! "$MANIFEST_SIZE_BYTES" =~ ^[1-9][0-9]*$ || \
      ! "$MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "backup manifest checksum" "invalid sha256" 21
fi
if [[ "$SAFE_IO" != /* || ! -x "$SAFE_IO" ]]; then
  fail "safe I/O helper" "controller-owned helper is unavailable" 22
fi
if [[ "$OBJECT_VERIFY_MODE" != "all" ]]; then
  fail "object verification mode" "formal rehearsal requires all generations" 22
fi
if [[ ! "$EXPECTED_SOURCE_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$EXPECTED_SOURCE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ || \
      ! "$EXPECTED_BACKUP_ID" =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9.-]{0,80}$ ]]; then
  fail "expected backup identity" "source ref/version/backup id must be exact" 22
fi
if [[ ! "$BACKUP_POLICY_SHA256" =~ ^[0-9a-f]{64}$ || \
      ! "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  fail "release backup policy" "controller policy attestation is invalid" 22
fi
if ! CANONICAL_LAKEHOUSE_BUCKET="$(
  "$SAFE_IO" lakehouse-contract --path "$SHARED_ENV"
)"; then
  fail "lakehouse bucket" "live GCP runtime bucket contract is invalid" 22
fi
if ! CANONICAL_RELEASE_BACKUP_BUCKET="$(
  "$SAFE_IO" release-backup-contract --path "$SHARED_ENV"
)"; then
  fail "release backup bucket" "live GCP backup bucket contract is invalid" 22
fi
"$SAFE_IO" gcs-release-backup-permissions \
  --bucket "$CANONICAL_RELEASE_BACKUP_BUCKET" >/dev/null || \
  fail "release backup permissions" "VM has missing or destructive backup permissions" 22

cleanup() {
  local rc=$?
  trap - EXIT
  for container in "$OP_CONTAINER" "$GOLD_CONTAINER"; do
    if [[ -n "$container" && "$container" == omega_gcp_rehearsal_* ]]; then
      label="$(docker inspect "$container" --format '{{index .Config.Labels "omega.gcp.rehearsal-id"}}' 2>/dev/null || true)"
      if [[ "$label" == "$REHEARSAL_ID" ]]; then
        docker rm -f "$container" >/dev/null 2>&1 || rc=70
      elif docker inspect "$container" >/dev/null 2>&1; then
        echo "ERROR: refusing cleanup of foreign rehearsal container" >&2
        rc=70
      fi
    fi
  done
  for volume in "$OP_VOLUME" "$GOLD_VOLUME"; do
    if [[ -n "$volume" && "$volume" == omega_gcp_rehearsal_* ]]; then
      label="$(docker volume inspect "$volume" --format '{{index .Labels "omega.gcp.rehearsal-id"}}' 2>/dev/null || true)"
      if [[ "$label" == "$REHEARSAL_ID" ]]; then
        docker volume rm "$volume" >/dev/null 2>&1 || rc=70
      elif docker volume inspect "$volume" >/dev/null 2>&1; then
        echo "ERROR: refusing cleanup of foreign rehearsal volume" >&2
        rc=70
      fi
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

MANIFEST="${WORKDIR}/manifest.json"
"$SAFE_IO" gcs-download --uri "$MANIFEST_URI" --generation "$MANIFEST_GENERATION" \
  --size "$MANIFEST_SIZE_BYTES" --sha256 "$MANIFEST_SHA256" --output "$MANIFEST"
ACTUAL_MANIFEST_SHA="$MANIFEST_SHA256"
python3 - "$MANIFEST" "$MANIFEST_URI" "$CANONICAL_LAKEHOUSE_BUCKET" \
  "$CANONICAL_RELEASE_BACKUP_BUCKET" "$EXPECTED_SOURCE_REF" \
  "$EXPECTED_SOURCE_VERSION" "$EXPECTED_BACKUP_ID" "$BACKUP_POLICY_SHA256" \
  "$PROJECT_ID" <<'PY'
import hashlib
import json, re, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
if payload.get("schema_version") != 4 or payload.get("complete") is not True:
    raise SystemExit("manifest is not complete schema v4")
backup_id = payload.get("backup_id", "")
storage = payload.get("object_storage", {})
bucket = storage.get("bucket", "")
backup_storage = payload.get("backup_storage", {})
backup_bucket = backup_storage.get("bucket", "")
if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9.-]{0,80}", backup_id):
    raise SystemExit("backup id is invalid")
if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", bucket):
    raise SystemExit("backup bucket is invalid")
if bucket != sys.argv[3]:
    raise SystemExit("backup bucket differs from the live GCP runtime")
if backup_bucket != sys.argv[4] or backup_bucket == bucket:
    raise SystemExit("backup artifacts are not isolated from the live lakehouse")
if backup_id != sys.argv[7]:
    raise SystemExit("backup id differs from the required restore point")
source = payload.get("source_release", {})
if source.get("deploy_ref") != sys.argv[5] or source.get("version") != sys.argv[6]:
    raise SystemExit("backup source release differs from the required restore point")
if sys.argv[2] != f"gs://{backup_bucket}/_omega_backups/{backup_id}/manifest.json":
    raise SystemExit("manifest URI differs from its canonical identity")
expected_backup_controls = {
    "bucket": backup_bucket,
    "location": backup_storage.get("location"),
    "uniform_bucket_level_access": True,
    "public_access_prevention": "enforced",
    "versioning_enabled": True,
    "soft_delete_seconds": 2592000,
    "retention_seconds": 604800,
    "retention_locked": False,
    "vm_role": f"projects/{sys.argv[9]}/roles/omegaReleaseBackupWriter",
    "vm_permissions": [
        "storage.buckets.get",
        "storage.objects.create",
        "storage.objects.get",
    ],
    "policy_sha256": sys.argv[8],
}
if (
    backup_storage != expected_backup_controls
    or re.fullmatch(r"[A-Z0-9-]{2,40}", str(backup_storage.get("location", ""))) is None
):
    raise SystemExit("backup storage policy differs from controller attestation")
policy_payload = dict(backup_storage)
policy_sha256 = policy_payload.pop("policy_sha256")
if hashlib.sha256(
    json.dumps(policy_payload, separators=(",", ":"), sort_keys=True).encode()
).hexdigest() != policy_sha256:
    raise SystemExit("backup storage policy hash is not self-consistent")
if payload.get("plaintext_runtime_secrets_included") is not False:
    raise SystemExit("backup must explicitly exclude plaintext runtime secrets")
restore_settings = payload.get("database_restore_settings", {})
if set(restore_settings) != {
    "schema_version",
    "captured_before_fence",
    "restore_policy",
    "databases",
} or (
    restore_settings.get("schema_version") != 1
    or restore_settings.get("captured_before_fence") is not True
    or restore_settings.get("restore_policy") != "keep-fenced-until-explicit-cutover"
    or set(restore_settings.get("databases", {}))
    != {"modecissions", "modecissions_gold"}
):
    raise SystemExit("pre-fence database restore policy is incomplete")
for database, settings in restore_settings["databases"].items():
    if set(settings) != {
        "connection_limit",
        "database_default_transaction_read_only",
        "effective_default_transaction_read_only",
    }:
        raise SystemExit(f"pre-fence database setting shape is invalid: {database}")
    limit = settings.get("connection_limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < -1:
        raise SystemExit(f"pre-fence connection limit is invalid: {database}")
    if settings.get("database_default_transaction_read_only") not in {
        "absent",
        "on",
        "off",
    } or settings.get("effective_default_transaction_read_only") not in {"on", "off"}:
        raise SystemExit(f"pre-fence read-only policy is invalid: {database}")
    expected_effective = (
        "on"
        if settings["database_default_transaction_read_only"] == "on"
        else "off"
    )
    if settings["effective_default_transaction_read_only"] != expected_effective:
        raise SystemExit(f"pre-fence read-only policy has an unrecorded override: {database}")
restore_settings_sha = hashlib.sha256(
    (json.dumps(restore_settings, indent=2, sort_keys=True) + "\n").encode()
).hexdigest()
if payload.get("database_restore_settings_sha256") != restore_settings_sha:
    raise SystemExit("pre-fence database restore policy checksum differs")
consistency = payload.get("consistency", {})
if (
    consistency.get("writers_fenced") is not True
    or consistency.get("scheduler_fenced") is not True
    or consistency.get("database_default_transaction_read_only") is not True
):
    raise SystemExit("backup consistency fence is incomplete")
if storage.get("versioning_enabled") is not True:
    raise SystemExit("object storage restore point is not versioned")
lakehouse_policy = storage.get("bucket_policy", {})
expected_lifecycle = [
    {"action": {"storageClass": "NEARLINE", "type": "SetStorageClass"}, "condition": {"age": 30}},
    {"action": {"type": "Delete"}, "condition": {"isLive": False, "numNewerVersions": 5}},
]
if (
    set(lakehouse_policy)
    != {
        "bucket",
        "location",
        "metageneration",
        "versioning_enabled",
        "uniform_bucket_level_access",
        "public_access_prevention",
        "soft_delete_seconds",
        "retention_policy",
        "lifecycle_rules",
        "policy_sha256",
    }
    or lakehouse_policy.get("bucket") != bucket
    or lakehouse_policy.get("location") != backup_storage.get("location")
    or re.fullmatch(r"[1-9][0-9]*", str(lakehouse_policy.get("metageneration", "")))
    is None
    or lakehouse_policy.get("versioning_enabled") is not True
    or lakehouse_policy.get("uniform_bucket_level_access") is not True
    or lakehouse_policy.get("public_access_prevention") != "enforced"
    or lakehouse_policy.get("soft_delete_seconds") != 604800
    or lakehouse_policy.get("retention_policy") != "absent"
    or lakehouse_policy.get("lifecycle_rules") != expected_lifecycle
):
    raise SystemExit("live lakehouse policy attestation is incomplete")
lakehouse_policy_payload = dict(lakehouse_policy)
lakehouse_policy_sha = lakehouse_policy_payload.pop("policy_sha256")
if hashlib.sha256(
    json.dumps(lakehouse_policy_payload, separators=(",", ":"), sort_keys=True).encode()
).hexdigest() != lakehouse_policy_sha:
    raise SystemExit("live lakehouse policy hash is not self-consistent")
if storage.get("generation_hold") != {
    "type": "temporaryHold",
    "all_recorded_generations_held": True,
    "release_policy": "explicit-approved-release-only",
    "implicit_release": False,
}:
    raise SystemExit("object storage generation-hold policy is incomplete")
artifact_keys = {
    "postgres": "postgres.sql.gz",
    "postgres_gold": "postgres_gold.sql.gz",
    "object_manifest": "lakehouse_objects.jsonl",
    "runtime_images": "runtime-images.json",
    "runtime_provenance": "runtime-provenance.json",
}
if set(payload.get("artifacts", {})) != set(artifact_keys):
    raise SystemExit("backup artifact inventory is not exact")
for name, key in artifact_keys.items():
    artifact = payload.get("artifacts", {}).get(name, {})
    expected_uri = f"gs://{backup_bucket}/_omega_backups/{backup_id}/{key}"
    if artifact.get("uri") != expected_uri:
        raise SystemExit(f"invalid {name} canonical URI")
    if not re.fullmatch(r"[0-9a-f]{64}", artifact.get("sha256", "")):
        raise SystemExit(f"invalid {name} sha256")
    if not re.fullmatch(r"[1-9][0-9]*", str(artifact.get("generation", ""))):
        raise SystemExit(f"invalid {name} generation")
    if not isinstance(artifact.get("size_bytes"), int) or artifact["size_bytes"] < 1:
        raise SystemExit(f"invalid {name} size")
for name in ("postgres", "postgres_gold"):
    image = payload.get("database_images", {}).get(name, {})
    if not re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", image.get("repo_digest", "")):
        raise SystemExit(f"invalid immutable database image: {name}")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("image_id", "")):
        raise SystemExit(f"invalid database image ID: {name}")
PY
emit "backup manifest checksum" "PASS" "sha256=${ACTUAL_MANIFEST_SHA}"

mapfile -t DB_RESTORE_VALUES < <(python3 - "$MANIFEST" <<'PY'
import json
import sys

settings = json.load(open(sys.argv[1], encoding="utf-8"))["database_restore_settings"]["databases"]
for database in ("modecissions", "modecissions_gold"):
    item = settings[database]
    print(item["connection_limit"])
    print(item["database_default_transaction_read_only"])
    print(item["effective_default_transaction_read_only"])
PY
)
if [[ "${#DB_RESTORE_VALUES[@]}" -ne 6 ]]; then
  fail "pre-fence database restore policy" "manifest setting inventory is incomplete" 23
fi
OP_ORIGINAL_CONN_LIMIT="${DB_RESTORE_VALUES[0]}"
OP_ORIGINAL_DB_READONLY="${DB_RESTORE_VALUES[1]}"
OP_ORIGINAL_EFFECTIVE_READONLY="${DB_RESTORE_VALUES[2]}"
GOLD_ORIGINAL_CONN_LIMIT="${DB_RESTORE_VALUES[3]}"
GOLD_ORIGINAL_DB_READONLY="${DB_RESTORE_VALUES[4]}"
GOLD_ORIGINAL_EFFECTIVE_READONLY="${DB_RESTORE_VALUES[5]}"
emit "pre-fence database restore policy" "PASS" "exact operational and Gold settings checksum-bound"

read_manifest_field() {
  python3 - "$MANIFEST" "$1" "$2" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(payload["artifacts"][sys.argv[2]][sys.argv[3]])
PY
}

OP_URI="$(read_manifest_field postgres uri)"
OP_GENERATION="$(read_manifest_field postgres generation)"
OP_SIZE="$(read_manifest_field postgres size_bytes)"
OP_SHA="$(read_manifest_field postgres sha256)"
GOLD_URI="$(read_manifest_field postgres_gold uri)"
GOLD_GENERATION="$(read_manifest_field postgres_gold generation)"
GOLD_SIZE="$(read_manifest_field postgres_gold size_bytes)"
GOLD_SHA="$(read_manifest_field postgres_gold sha256)"
OBJECT_URI="$(read_manifest_field object_manifest uri)"
OBJECT_GENERATION="$(read_manifest_field object_manifest generation)"
OBJECT_SIZE="$(read_manifest_field object_manifest size_bytes)"
OBJECT_SHA="$(read_manifest_field object_manifest sha256)"
IMAGES_URI="$(read_manifest_field runtime_images uri)"
IMAGES_GENERATION="$(read_manifest_field runtime_images generation)"
IMAGES_SIZE="$(read_manifest_field runtime_images size_bytes)"
IMAGES_SHA="$(read_manifest_field runtime_images sha256)"
PROVENANCE_URI="$(read_manifest_field runtime_provenance uri)"
PROVENANCE_GENERATION="$(read_manifest_field runtime_provenance generation)"
PROVENANCE_SIZE="$(read_manifest_field runtime_provenance size_bytes)"
PROVENANCE_SHA="$(read_manifest_field runtime_provenance sha256)"

for value in "$OP_SIZE" "$GOLD_SIZE" "$OBJECT_SIZE" "$IMAGES_SIZE" "$PROVENANCE_SIZE"; do
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    fail "restore headroom" "manifest artifact sizes are not exact positive integers" 24
  fi
done
LIVE_OP_CONTAINER_ID="$(docker inspect mode_postgres --format '{{.Id}}' 2>/dev/null || true)"
LIVE_GOLD_CONTAINER_ID="$(docker inspect mode_postgres_gold --format '{{.Id}}' 2>/dev/null || true)"
LIVE_OP_VOLUME="$(docker inspect mode_postgres --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' 2>/dev/null || true)"
LIVE_GOLD_VOLUME="$(docker inspect mode_postgres_gold --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' 2>/dev/null || true)"
if [[ ! "$LIVE_OP_CONTAINER_ID" =~ ^[0-9a-f]{64}$ || \
      ! "$LIVE_GOLD_CONTAINER_ID" =~ ^[0-9a-f]{64}$ || \
      -z "$LIVE_OP_VOLUME" || -z "$LIVE_GOLD_VOLUME" || \
      "$LIVE_OP_VOLUME" == "$LIVE_GOLD_VOLUME" ]]; then
  fail "restore headroom" "live database container/volume identity is unavailable" 24
fi
LIVE_OP_SOURCE="$(docker volume inspect "$LIVE_OP_VOLUME" --format '{{.Mountpoint}}' 2>/dev/null || true)"
LIVE_GOLD_SOURCE="$(docker volume inspect "$LIVE_GOLD_VOLUME" --format '{{.Mountpoint}}' 2>/dev/null || true)"
for source in "$LIVE_OP_SOURCE" "$LIVE_GOLD_SOURCE"; do
  if [[ "$source" != /var/lib/docker/volumes/*/_data || ! -d "$source" || -L "$source" ]]; then
    fail "restore headroom" "live database volume path is unsafe or ambiguous" 24
  fi
done
LIVE_OP_BYTES="$(du -sb -- "$LIVE_OP_SOURCE" | awk '{print $1}')"
LIVE_GOLD_BYTES="$(du -sb -- "$LIVE_GOLD_SOURCE" | awk '{print $1}')"
DOCKER_FREE_BYTES="$(df --output=avail -B1 /var/lib/docker | awk 'NR == 2 && $1 ~ /^[0-9]+$/ {print $1}')"
TMP_FREE_BYTES="$(df --output=avail -B1 "$WORKDIR" | awk 'NR == 2 && $1 ~ /^[0-9]+$/ {print $1}')"
for value in "$LIVE_OP_BYTES" "$LIVE_GOLD_BYTES" "$DOCKER_FREE_BYTES" "$TMP_FREE_BYTES"; do
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    fail "restore headroom" "filesystem/volume capacity could not be measured" 24
  fi
done
DUMP_ESTIMATE_BYTES=$(( (OP_SIZE + GOLD_SIZE) * 12 ))
LIVE_ESTIMATE_BYTES=$(( (LIVE_OP_BYTES + LIVE_GOLD_BYTES) * 3 / 2 ))
if [[ "$LIVE_ESTIMATE_BYTES" -gt "$DUMP_ESTIMATE_BYTES" ]]; then
  RESTORE_ESTIMATE_BYTES="$LIVE_ESTIMATE_BYTES"
else
  RESTORE_ESTIMATE_BYTES="$DUMP_ESTIMATE_BYTES"
fi
DOCKER_REQUIRED_BYTES=$(( RESTORE_ESTIMATE_BYTES + 10 * 1024 * 1024 * 1024 ))
TMP_REQUIRED_BYTES=$(( OP_SIZE + GOLD_SIZE + OBJECT_SIZE + IMAGES_SIZE + PROVENANCE_SIZE + 5 * 1024 * 1024 * 1024 ))
if [[ "$DOCKER_FREE_BYTES" -lt "$DOCKER_REQUIRED_BYTES" || \
      "$TMP_FREE_BYTES" -lt "$TMP_REQUIRED_BYTES" ]]; then
  fail "restore headroom" "insufficient measured capacity; use an isolated rehearsal host" 24
fi
emit "restore headroom" "PASS" "docker_required=${DOCKER_REQUIRED_BYTES} docker_free=${DOCKER_FREE_BYTES} tmp_required=${TMP_REQUIRED_BYTES} tmp_free=${TMP_FREE_BYTES}"

"$SAFE_IO" gcs-download --uri "$OP_URI" --generation "$OP_GENERATION" \
  --size "$OP_SIZE" --sha256 "$OP_SHA" --output "${WORKDIR}/postgres.sql.gz"
"$SAFE_IO" gcs-download --uri "$GOLD_URI" --generation "$GOLD_GENERATION" \
  --size "$GOLD_SIZE" --sha256 "$GOLD_SHA" --output "${WORKDIR}/postgres_gold.sql.gz"
"$SAFE_IO" gcs-download --uri "$OBJECT_URI" --generation "$OBJECT_GENERATION" \
  --size "$OBJECT_SIZE" --sha256 "$OBJECT_SHA" --output "${WORKDIR}/lakehouse_objects.jsonl"
"$SAFE_IO" gcs-download --uri "$IMAGES_URI" --generation "$IMAGES_GENERATION" \
  --size "$IMAGES_SIZE" --sha256 "$IMAGES_SHA" --output "${WORKDIR}/runtime-images.json"
"$SAFE_IO" gcs-download --uri "$PROVENANCE_URI" --generation "$PROVENANCE_GENERATION" \
  --size "$PROVENANCE_SIZE" --sha256 "$PROVENANCE_SHA" --output "${WORKDIR}/runtime-provenance.json"
for item in "postgres.sql.gz:${OP_SHA}" "postgres_gold.sql.gz:${GOLD_SHA}" \
  "lakehouse_objects.jsonl:${OBJECT_SHA}" "runtime-images.json:${IMAGES_SHA}" \
  "runtime-provenance.json:${PROVENANCE_SHA}"; do
  filename="${item%%:*}"
  expected="${item#*:}"
  actual="$(sha256sum "${WORKDIR}/${filename}" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    fail "backup artifact checksum" "artifact=${filename} actual=${actual}" 25
  fi
done
python3 - "${WORKDIR}/runtime-images.json" "${WORKDIR}/runtime-provenance.json" \
  "$EXPECTED_SOURCE_REF" "$EXPECTED_SOURCE_VERSION" <<'PY'
import json
import sys

images = json.load(open(sys.argv[1], encoding="utf-8"))
provenance = json.load(open(sys.argv[2], encoding="utf-8"))
if images.get("secret_values_included") is not False or not isinstance(images.get("services"), dict):
    raise SystemExit("runtime image evidence is malformed")
if (
    provenance.get("deploy_ref") != sys.argv[3]
    or provenance.get("version") != sys.argv[4]
):
    raise SystemExit("runtime provenance differs from the required source release")
PY
gzip -t "${WORKDIR}/postgres.sql.gz"
gzip -t "${WORKDIR}/postgres_gold.sql.gz"
emit "backup artifact checksums" "PASS" "all 5/5 artifact generations and checksums verified"

OBJECT_RESULT="$(python3 - "$MANIFEST" "${WORKDIR}/lakehouse_objects.jsonl" "$OBJECT_VERIFY_MODE" <<'PY'
import concurrent.futures
import hashlib
import json
import re
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
        generation = str(row.get("generation", ""))
        metageneration = str(row.get("metageneration", ""))
        if (
            not key
            or key <= last_key
            or re.fullmatch(r"[1-9][0-9]*", generation) is None
            or re.fullmatch(r"[1-9][0-9]*", metageneration) is None
            or row.get("temporaryHold") is not True
        ):
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

bucket_request = urllib.request.Request(
    f"https://storage.googleapis.com/storage/v1/b/{quoted_bucket}?"
    "fields=name,location,metageneration,versioning,iamConfiguration,"
    "softDeletePolicy,retentionPolicy,lifecycle",
    headers=headers,
)
with urllib.request.urlopen(bucket_request, timeout=30) as response:
    live_bucket = json.load(response)
live_policy = {
    "bucket": live_bucket.get("name"),
    "location": live_bucket.get("location"),
    "metageneration": str(live_bucket.get("metageneration", "")),
    "versioning_enabled": live_bucket.get("versioning", {}).get("enabled"),
    "uniform_bucket_level_access": live_bucket.get("iamConfiguration", {})
    .get("uniformBucketLevelAccess", {})
    .get("enabled"),
    "public_access_prevention": live_bucket.get("iamConfiguration", {}).get(
        "publicAccessPrevention"
    ),
    "soft_delete_seconds": int(
        live_bucket.get("softDeletePolicy", {}).get("retentionDurationSeconds", 0)
    ),
    "retention_policy": (
        "absent" if live_bucket.get("retentionPolicy") in (None, {}) else "present"
    ),
    "lifecycle_rules": live_bucket.get("lifecycle", {}).get("rule", []),
}
live_policy["policy_sha256"] = hashlib.sha256(
    json.dumps(live_policy, separators=(",", ":"), sort_keys=True).encode()
).hexdigest()
if live_policy != expected.get("bucket_policy"):
    raise SystemExit("effective live lakehouse bucket policy drifted after backup")

def verify(row):
    key = urllib.parse.quote(row["key"], safe="")
    params = urllib.parse.urlencode({
        "generation": row["generation"],
        "ifMetagenerationMatch": row["metageneration"],
        "fields": "name,size,generation,metageneration,md5Hash,crc32c,temporaryHold",
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
            if str(actual.get("metageneration")) != row["metageneration"]:
                raise RuntimeError("metageneration mismatch")
            if actual.get("temporaryHold") is not True:
                raise RuntimeError("temporary hold missing")
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
print(json.dumps({"objects": len(rows), "bytes": total_bytes, "verified_generations": len(verify_rows), "verified_temporary_holds": len(verify_rows), "mode": mode}))
PY
)"
emit "held object-generation restore points" "PASS" "$OBJECT_RESULT"

BACKUP_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["backup_id"])' "$MANIFEST")"
SUFFIX="$(printf '%s' "${BACKUP_ID}-${MANIFEST_SHA256}" | sha256sum | cut -c1-16)_$$"
REHEARSAL_ID="$SUFFIX"
REHEARSAL_ADMIN="omega_rehearsal_${SUFFIX}"
if [[ ! "$REHEARSAL_ADMIN" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; then
  fail "isolated bootstrap role" "generated role is not a valid PostgreSQL identifier" 26
fi
for dump in "${WORKDIR}/postgres.sql.gz" "${WORKDIR}/postgres_gold.sql.gz"; do
  if ! gzip -dc "$dump" | python3 -c '
import re
import sys

role = re.escape(sys.argv[1].encode())
pattern = re.compile(rb"^(?:CREATE|DROP|ALTER) ROLE \"?" + role + rb"\"?(?:[ ;]|$)")
for line in sys.stdin.buffer:
    if pattern.match(line):
        raise SystemExit(1)
' "$REHEARSAL_ADMIN"
  then
    fail "isolated bootstrap role" "generated role unexpectedly exists in source dump" 26
  fi
done
emit "isolated bootstrap role" "PASS" "unique role absent from both unmodified dumps"
OP_CONTAINER="omega_gcp_rehearsal_${SUFFIX}_op"
GOLD_CONTAINER="omega_gcp_rehearsal_${SUFFIX}_gold"
OP_VOLUME="omega_gcp_rehearsal_${SUFFIX}_op"
GOLD_VOLUME="omega_gcp_rehearsal_${SUFFIX}_gold"
read_database_image_field() {
  python3 - "$MANIFEST" "$1" "$2" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["database_images"][sys.argv[2]][sys.argv[3]])
PY
}
OP_IMAGE="$(read_database_image_field postgres repo_digest)"
OP_IMAGE_ID="$(read_database_image_field postgres image_id)"
GOLD_IMAGE="$(read_database_image_field postgres_gold repo_digest)"
GOLD_IMAGE_ID="$(read_database_image_field postgres_gold image_id)"
if [[ ! "$OP_IMAGE" =~ ^pgvector/pgvector@sha256:[0-9a-f]{64}$ || ! "$OP_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  fail "isolated database image" "unexpected operational image" 26
fi
if [[ ! "$GOLD_IMAGE" =~ ^postgres@sha256:[0-9a-f]{64}$ || ! "$GOLD_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  fail "isolated database image" "unexpected Gold image" 27
fi
if [[ "$(docker image inspect "$OP_IMAGE" --format '{{.Id}}' 2>/dev/null || true)" != "$OP_IMAGE_ID" || \
      "$(docker image inspect "$GOLD_IMAGE" --format '{{.Id}}' 2>/dev/null || true)" != "$GOLD_IMAGE_ID" ]]; then
  fail "isolated database image" "exact backup image digest/image ID is not locally available" 27
fi

for name in "$OP_CONTAINER" "$GOLD_CONTAINER"; do
  if docker inspect "$name" >/dev/null 2>&1; then
    fail "isolated resource names" "pre-existing container name=${name}" 27
  fi
done
for name in "$OP_VOLUME" "$GOLD_VOLUME"; do
  if docker volume inspect "$name" >/dev/null 2>&1; then
    fail "isolated resource names" "pre-existing volume name=${name}" 27
  fi
done
docker volume create --label "omega.gcp.rehearsal-id=$REHEARSAL_ID" "$OP_VOLUME" >/dev/null
docker volume create --label "omega.gcp.rehearsal-id=$REHEARSAL_ID" "$GOLD_VOLUME" >/dev/null
docker run -d --pull never --name "$OP_CONTAINER" --network none \
  --restart no --cpus 1.5 --memory 3g --memory-swap 3g --pids-limit 256 \
  --ulimit nofile=4096:4096 --blkio-weight 200 \
  --label "omega.gcp.rehearsal-id=$REHEARSAL_ID" \
  --mount "type=volume,source=${OP_VOLUME},target=/var/lib/postgresql/data" \
  -e POSTGRES_USER="$REHEARSAL_ADMIN" -e POSTGRES_DB=postgres \
  -e POSTGRES_HOST_AUTH_METHOD=trust "$OP_IMAGE" >/dev/null
docker run -d --pull never --name "$GOLD_CONTAINER" --network none \
  --restart no --cpus 1.5 --memory 3g --memory-swap 3g --pids-limit 256 \
  --ulimit nofile=4096:4096 --blkio-weight 200 \
  --label "omega.gcp.rehearsal-id=$REHEARSAL_ID" \
  --mount "type=volume,source=${GOLD_VOLUME},target=/var/lib/postgresql/data" \
  -e POSTGRES_USER="$REHEARSAL_ADMIN" -e POSTGRES_DB=postgres \
  -e POSTGRES_HOST_AUTH_METHOD=trust "$GOLD_IMAGE" postgres -p 5433 >/dev/null

for pair in "${OP_CONTAINER}:5432" "${GOLD_CONTAINER}:5433"; do
  container="${pair%%:*}"
  port="${pair#*:}"
  ready=0
  for _ in $(seq 1 60); do
    if docker exec "$container" pg_isready -h 127.0.0.1 -p "$port" -U "$REHEARSAL_ADMIN" >/dev/null 2>&1; then
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
  | docker exec -i -e "PGOPTIONS=-c default_transaction_read_only=off" \
    "$OP_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$REHEARSAL_ADMIN" -d postgres >/dev/null
gzip -dc "${WORKDIR}/postgres_gold.sql.gz" \
  | docker exec -i -e "PGOPTIONS=-c default_transaction_read_only=off" \
    "$GOLD_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$REHEARSAL_ADMIN" -d postgres -p 5433 >/dev/null

# The unmodified pg_dumpall stream must have serialized the temporary catalog
# fence. Prove that the restored databases are still connection-blocked and
# read-only before any restore verification or cutover simulation proceeds.
for spec in \
  "${OP_CONTAINER}:5432:modecissions" \
  "${GOLD_CONTAINER}:5433:modecissions_gold"; do
  IFS=: read -r container port database <<<"$spec"
  restored_limit="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
    -U "$REHEARSAL_ADMIN" -d postgres -p "$port" \
    -c "SELECT datconnlimit FROM pg_database WHERE datname = '${database}';" | tr -d '\r')"
  restored_config="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
    -U "$REHEARSAL_ADMIN" -d postgres -p "$port" \
    -c "SELECT COALESCE((SELECT split_part(setting, '=', 2) FROM pg_db_role_setting s CROSS JOIN LATERAL unnest(s.setconfig) setting WHERE s.setdatabase = (SELECT oid FROM pg_database WHERE datname = '${database}') AND s.setrole = 0 AND setting LIKE 'default_transaction_read_only=%'), 'absent');" | tr -d '\r')"
  restored_effective="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
    -U "$REHEARSAL_ADMIN" -d "$database" -p "$port" \
    -c 'SHOW default_transaction_read_only;' | tr -d '\r')"
  if [[ "$restored_limit" != "0" || "$restored_config" != "on" || \
        "$restored_effective" != "on" ]]; then
    fail "restored database fence" "unmodified dump did not preserve the temporary fence" 29
  fi
done
emit "restored database fence" "PASS" "unmodified dumps remain connection-blocked/read-only until cutover"

OP_MIGRATIONS="$(docker exec "$OP_CONTAINER" psql -At -U "$REHEARSAL_ADMIN" -d modecissions \
  -c 'SELECT count(*) FROM schema_migrations;' | tr -d '\r')"
GOLD_MIGRATIONS="$(docker exec "$GOLD_CONTAINER" psql -At -U "$REHEARSAL_ADMIN" -d modecissions_gold -p 5433 \
  -c 'SELECT count(*) FROM schema_migrations;' | tr -d '\r')"
if [[ ! "$OP_MIGRATIONS" =~ ^[1-9][0-9]*$ || ! "$GOLD_MIGRATIONS" =~ ^[1-9][0-9]*$ ]]; then
  fail "isolated logical restore" "schema_migrations missing after restore" 29
fi
POSTGRES_ROLES="$(docker exec "$OP_CONTAINER" psql -At -U "$REHEARSAL_ADMIN" -d postgres \
  -c "SELECT count(*) FROM pg_roles WHERE rolname IN ('postgres', '${REHEARSAL_ADMIN}') AND rolsuper;" | tr -d '\r')"
GOLD_POSTGRES_ROLES="$(docker exec "$GOLD_CONTAINER" psql -At -U "$REHEARSAL_ADMIN" -d postgres -p 5433 \
  -c "SELECT count(*) FROM pg_roles WHERE rolname IN ('postgres', '${REHEARSAL_ADMIN}') AND rolsuper;" | tr -d '\r')"
if [[ "$POSTGRES_ROLES" != "2" || "$GOLD_POSTGRES_ROLES" != "2" ]]; then
  fail "isolated logical restore" "bootstrap and restored postgres roles are not both superusers" 29
fi
PIPELINE_RUNS="$(docker exec "$OP_CONTAINER" psql -At -U "$REHEARSAL_ADMIN" -d modecissions \
  -c 'SELECT count(*) FROM pipeline_runs;' | tr -d '\r')"
DATASETS="$(docker exec "$OP_CONTAINER" psql -At -U "$REHEARSAL_ADMIN" -d modecissions \
  -c 'SELECT count(*) FROM datasets;' | tr -d '\r')"
GOLD_TABLES="$(docker exec "$GOLD_CONTAINER" psql -At -U "$REHEARSAL_ADMIN" -d modecissions_gold -p 5433 \
  -c "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema');" | tr -d '\r')"
for value in "$PIPELINE_RUNS" "$DATASETS" "$GOLD_TABLES"; do
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    fail "isolated logical restore" "restored business/schema cardinality is unexpectedly empty" 30
  fi
done
if [[ "$(docker inspect mode_postgres --format '{{.Id}}' 2>/dev/null || true)" != "$LIVE_OP_CONTAINER_ID" || \
      "$(docker inspect mode_postgres_gold --format '{{.Id}}' 2>/dev/null || true)" != "$LIVE_GOLD_CONTAINER_ID" || \
      "$(docker inspect mode_postgres --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' 2>/dev/null || true)" != "$LIVE_OP_VOLUME" || \
      "$(docker inspect mode_postgres_gold --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' 2>/dev/null || true)" != "$LIVE_GOLD_VOLUME" ]]; then
  fail "live database isolation" "live database container or volume identity changed" 30
fi

apply_original_database_policy() {
  local container="$1" port="$2" database="$3" original_limit="$4"
  local original_config="$5" original_effective="$6" readonly_sql actual
  if [[ "$original_config" == "absent" ]]; then
    readonly_sql="RESET default_transaction_read_only"
  elif [[ "$original_config" == "on" ]]; then
    readonly_sql="SET default_transaction_read_only = on"
  elif [[ "$original_config" == "off" ]]; then
    readonly_sql="SET default_transaction_read_only = off"
  else
    return 1
  fi
  docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
    -U "$REHEARSAL_ADMIN" -d postgres -p "$port" \
    -c "ALTER DATABASE ${database} CONNECTION LIMIT ${original_limit}; ALTER DATABASE ${database} ${readonly_sql};" \
    >/dev/null
  actual="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
    -U "$REHEARSAL_ADMIN" -d postgres -p "$port" \
    -c "SELECT datconnlimit FROM pg_database WHERE datname = '${database}';" | tr -d '\r')"
  [[ "$actual" == "$original_limit" ]] || return 1
  actual="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
    -U "$REHEARSAL_ADMIN" -d postgres -p "$port" \
    -c "SELECT COALESCE((SELECT split_part(setting, '=', 2) FROM pg_db_role_setting s CROSS JOIN LATERAL unnest(s.setconfig) setting WHERE s.setdatabase = (SELECT oid FROM pg_database WHERE datname = '${database}') AND s.setrole = 0 AND setting LIKE 'default_transaction_read_only=%'), 'absent');" | tr -d '\r')"
  [[ "$actual" == "$original_config" ]] || return 1
  actual="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
    -U "$REHEARSAL_ADMIN" -d "$database" -p "$port" \
    -c 'SHOW default_transaction_read_only;' | tr -d '\r')"
  [[ "$actual" == "$original_effective" ]]
}

# This is the isolated equivalent of the explicit rollback cutover. All dump,
# object-generation, schema/data, and live-isolation gates ran while the
# restored databases stayed fenced. Only now reapply and verify the exact
# pre-fence policy; the rehearsal containers still have --network none and are
# destroyed immediately by the cleanup trap.
apply_original_database_policy "$OP_CONTAINER" 5432 modecissions \
  "$OP_ORIGINAL_CONN_LIMIT" "$OP_ORIGINAL_DB_READONLY" \
  "$OP_ORIGINAL_EFFECTIVE_READONLY" || \
  fail "isolated database policy cutover" "operational pre-fence policy was not restored" 30
apply_original_database_policy "$GOLD_CONTAINER" 5433 modecissions_gold \
  "$GOLD_ORIGINAL_CONN_LIMIT" "$GOLD_ORIGINAL_DB_READONLY" \
  "$GOLD_ORIGINAL_EFFECTIVE_READONLY" || \
  fail "isolated database policy cutover" "Gold pre-fence policy was not restored" 30
emit "isolated database policy cutover" "PASS" "exact pre-fence policy reapplied only after all restore gates"
emit "isolated logical restore" "PASS" "operational_migrations=${OP_MIGRATIONS} gold_migrations=${GOLD_MIGRATIONS} pipeline_runs=${PIPELINE_RUNS} datasets=${DATASETS} gold_tables=${GOLD_TABLES} live_volumes_untouched=true"
printf 'OMEGA_GCP_REHEARSAL_JSON={"status":"PASS","backup_id":"%s","operational_migrations":%s,"gold_migrations":%s,"pipeline_runs":%s,"datasets":%s,"gold_tables":%s,"object_verify_mode":"%s"}\n' \
  "$BACKUP_ID" "$OP_MIGRATIONS" "$GOLD_MIGRATIONS" "$PIPELINE_RUNS" "$DATASETS" "$GOLD_TABLES" "$OBJECT_VERIFY_MODE"
