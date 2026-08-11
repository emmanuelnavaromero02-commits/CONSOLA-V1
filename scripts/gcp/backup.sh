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
CANDIDATE_REF="${4:-}"
CANDIDATE_ARTIFACT_URI="${5:-}"
CANDIDATE_ARTIFACT_SHA256="${6:-}"
EXPECTED_CURRENT_REF="${7:-}"
APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
CURRENT_LINK="${APP_ROOT}/current"
SHARED_ROOT="${APP_ROOT}/shared"
SHARED_ENV="${SHARED_ROOT}/infra.env"
GCP_RUNTIME_COMPOSE="${SHARED_ROOT}/docker-compose.gcp.yml"
OPERATION_MARKER="${SHARED_ROOT}/operation-state.json"
STATE_LINK="${SHARED_ROOT}/runtime-state"
STATE_BUNDLES_ROOT="${SHARED_ROOT}/state-bundles"
BOOTSTRAP_STATE="${STATE_LINK}/bootstrap-state.json"
RUNTIME_PROVENANCE="${STATE_LINK}/runtime-provenance.json"
BACKUP_PREFIX="_omega_backups/${BACKUP_ID}"
WORKDIR=""
STATE_STAGE=""
STATE_PREVIEW=""
STATE_FINAL=""
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

install_operation_guard() {
  local source="$1" guard_tmp dropin_tmp
  if [[ ! -x "$source" ]]; then
    fail "reboot operation guard" "exact candidate operation gate is missing" 26
  fi
  guard_tmp="$(mktemp /tmp/omega-operation-gate.XXXXXX)"
  dropin_tmp="$(mktemp /tmp/omega-docker-operation-gate.XXXXXX)"
  install -m 0755 "$source" "$guard_tmp"
  printf '%s\n' '[Service]' \
    'ExecStartPre=/usr/local/sbin/omega-operation-gate' > "$dropin_tmp"
  install -m 0755 "$guard_tmp" /usr/local/sbin/omega-operation-gate
  install -d -m 0755 /etc/systemd/system/docker.service.d
  install -m 0644 "$dropin_tmp" /etc/systemd/system/docker.service.d/omega-operation-gate.conf
  rm -f -- "$guard_tmp" "$dropin_tmp"
  systemctl daemon-reload
}

write_operation_state() {
  local state="$1"
  python3 - "$OPERATION_MARKER" "$state" "$BACKUP_ID" "$CURRENT_REF" <<'PY'
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "operation": "backup",
    "state": sys.argv[2],
    "backup_id": sys.argv[3],
    "deploy_ref": sys.argv[4],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
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
if [[ ! "$CANDIDATE_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$CANDIDATE_ARTIFACT_SHA256" =~ ^[0-9a-f]{64}$ || \
      ! "$CANDIDATE_ARTIFACT_URI" =~ ^gs://[^/]+/deploy-artifacts/${CANDIDATE_REF}/repo\.tar\.gz$ ]]; then
  fail "candidate helper artifact" "exact ref, URI, and sha256 are required" 22
fi
if [[ ! "$EXPECTED_CURRENT_REF" =~ ^[0-9a-f]{40}$ ]]; then
  fail "expected current release" "one exact pre-deploy live ref is required" 22
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
CURRENT_REF="$(basename "$CURRENT_RELEASE")"
CURRENT_VERSION="$(tr -d '\r\n' < "${CURRENT_RELEASE}/VERSION" 2>/dev/null || true)"
if [[ ! "$CURRENT_REF" =~ ^[0-9a-f]{40}$ || -z "$CURRENT_VERSION" || \
      ! -s "$BASE_COMPOSE" || ! -s "$SHARED_ENV" || ! -s "$GCP_RUNTIME_COMPOSE" ]]; then
  fail "current runtime inputs" "exact release or shared host state missing" 25
fi
if [[ "$CURRENT_REF" != "$EXPECTED_CURRENT_REF" ]]; then
  fail "expected current release" "live ref differs from Terraform/operator contract" 25
fi

exec 9>"/var/lock/omega-gcp-day2.lock"
if ! flock -n 9; then
  fail "exclusive day-2 lock" "another release/backup operation is active" 26
fi

install -d -m 0755 "$SHARED_ROOT"
if [[ -e "$OPERATION_MARKER" ]]; then
  fail "durable operation fence" "an incomplete operation marker already exists" 26
fi
if grep -Eq '^(GHCR_[A-Z0-9_]*(TOKEN|PASSWORD|SECRET|CREDENTIAL|AUTH|USER)|GITHUB_TOKEN|DOCKER_AUTH_CONFIG)=' "$SHARED_ENV"; then
  fail "server-owned registry credential boundary" "registry credential found in runtime env" 26
fi

WORKDIR="$(mktemp -d /tmp/omega-gcp-backup.XXXXXX)"
cleanup_early() {
  local rc=$?
  if [[ -n "$STATE_PREVIEW" && "$STATE_PREVIEW" == "${SHARED_ROOT}/.runtime-state."* ]]; then
    rm -f -- "$STATE_PREVIEW"
  fi
  if [[ -n "$STATE_STAGE" && "$STATE_STAGE" == "${STATE_BUNDLES_ROOT}/."* ]]; then
    rm -rf -- "$STATE_STAGE"
  fi
  if [[ -n "$STATE_FINAL" && "$STATE_FINAL" == "${STATE_BUNDLES_ROOT}/legacy-"* ]]; then
    rm -rf -- "$STATE_FINAL"
  fi
  if [[ -n "$WORKDIR" && "$WORKDIR" == /tmp/omega-gcp-backup.* ]]; then
    rm -rf -- "$WORKDIR"
  fi
  exit "$rc"
}
trap cleanup_early EXIT

python3 - "$CANDIDATE_ARTIFACT_URI" "${WORKDIR}/candidate.tar.gz" <<'PY'
import json
import pathlib
import sys
import urllib.parse
import urllib.request

uri, destination = sys.argv[1:]
parsed = urllib.parse.urlparse(uri)
token_request = urllib.request.Request(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
)
with urllib.request.urlopen(token_request, timeout=10) as response:
    token = json.load(response)["access_token"]
url = "https://storage.googleapis.com/download/storage/v1/b/{}/o/{}?alt=media".format(
    urllib.parse.quote(parsed.netloc, safe=""),
    urllib.parse.quote(parsed.path.lstrip("/"), safe=""),
)
request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
with urllib.request.urlopen(request, timeout=120) as response, pathlib.Path(destination).open("wb") as stream:
    while chunk := response.read(1024 * 1024):
        stream.write(chunk)
PY
CANDIDATE_ACTUAL_SHA256="$(sha256sum "${WORKDIR}/candidate.tar.gz" | awk '{print $1}')"
if [[ "$CANDIDATE_ACTUAL_SHA256" != "$CANDIDATE_ARTIFACT_SHA256" ]]; then
  fail "candidate helper artifact" "downloaded artifact checksum mismatch" 26
fi
install -d -m 0700 "${WORKDIR}/candidate"
python3 - "${WORKDIR}/candidate.tar.gz" "${WORKDIR}/candidate" <<'PY'
import pathlib
import sys
import tarfile

destination = pathlib.Path(sys.argv[2])
with tarfile.open(sys.argv[1], "r:gz") as archive:
    members = archive.getmembers()
    if not members:
        raise SystemExit("empty candidate archive")
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.isdev():
            raise SystemExit("unsafe candidate archive")
        if member.issym() or member.islnk():
            link = pathlib.PurePosixPath(member.linkname)
            if link.is_absolute() or ".." in link.parts:
                raise SystemExit("unsafe candidate archive link")
    archive.extractall(destination, members=members)
PY
RUNTIME_CONTRACT="${WORKDIR}/candidate/scripts/gcp/runtime_contract.py"
REBOOT_HELPER="${WORKDIR}/candidate/scripts/gcp/reboot-runtime.sh"
CANDIDATE_GUARD="${WORKDIR}/candidate/infra/terraform-gcp/templates/omega-operation-gate"
if [[ ! -x "$RUNTIME_CONTRACT" || ! -x "$REBOOT_HELPER" || ! -x "$CANDIDATE_GUARD" ]]; then
  fail "candidate helper artifact" "exact candidate runtime/reboot/operation helpers are missing" 26
fi
emit "candidate helper artifact" "PASS" "ref=${CANDIDATE_REF} sha256=${CANDIDATE_ARTIFACT_SHA256}"

# The candidate guard is installed before any legacy state is made visible.
# Docker therefore cannot auto-restart writers against a partial adoption.
install_operation_guard "$CANDIDATE_GUARD"
emit "reboot operation guard" "PASS" "exact candidate ExecStartPre installed before state publication"

LEGACY_BOOTSTRAP_STATE="${SHARED_ROOT}/bootstrap-state.json"
LEGACY_RUNTIME_PROVENANCE="${SHARED_ROOT}/runtime-provenance.json"
if [[ -e "$LEGACY_BOOTSTRAP_STATE" || -e "$LEGACY_RUNTIME_PROVENANCE" ]]; then
  if [[ ! -s "$LEGACY_BOOTSTRAP_STATE" || ! -s "$LEGACY_RUNTIME_PROVENANCE" ]]; then
    fail "legacy adoption state" "one-existing/one-missing direct state pair is corrupt" 26
  fi
  fail "legacy adoption state" "non-atomic direct state pair requires explicit reconciliation" 26
fi

ADOPTION_NEEDED=0
ACTIVE_PROVENANCE="$RUNTIME_PROVENANCE"
if [[ -e "$STATE_LINK" || -L "$STATE_LINK" ]]; then
  if [[ ! -s "$BOOTSTRAP_STATE" || ! -s "$RUNTIME_PROVENANCE" ]]; then
    fail "canonical runtime state" "atomic state link does not expose both required files" 26
  fi
  if ! /usr/local/sbin/omega-operation-gate; then
    fail "canonical runtime state" "operation gate rejected existing state/helper provenance" 26
  fi
else
  ADOPTION_NEEDED=1
  install -d -m 0700 "$STATE_BUNDLES_ROOT" "${SHARED_ROOT}/bin"

  # Stage immutable, ref-addressed helpers. The directory is published before
  # state, but remains inert until the atomic state link references its hashes.
  HELPER_ROOT="${SHARED_ROOT}/bin/${CANDIDATE_REF}"
  HELPER_TMP="${SHARED_ROOT}/bin/.${CANDIDATE_REF}.$$"
  REBOOT_SHA256="$(sha256sum "$REBOOT_HELPER" | awk '{print $1}')"
  CONTRACT_SHA256="$(sha256sum "$RUNTIME_CONTRACT" | awk '{print $1}')"
  if [[ -e "$HELPER_ROOT" ]]; then
    if [[ ! -x "${HELPER_ROOT}/reboot-runtime.sh" || \
          ! -x "${HELPER_ROOT}/runtime_contract.py" || \
          "$(sha256sum "${HELPER_ROOT}/reboot-runtime.sh" | awk '{print $1}')" != "$REBOOT_SHA256" || \
          "$(sha256sum "${HELPER_ROOT}/runtime_contract.py" | awk '{print $1}')" != "$CONTRACT_SHA256" ]]; then
      fail "shared reboot helper bundle" "existing candidate-ref helper bundle differs" 26
    fi
  else
    install -d -m 0700 "$HELPER_TMP"
    install -m 0755 "$REBOOT_HELPER" "${HELPER_TMP}/reboot-runtime.sh"
    install -m 0755 "$RUNTIME_CONTRACT" "${HELPER_TMP}/runtime_contract.py"
    python3 - "${HELPER_TMP}/source.json" "$CANDIDATE_REF" \
      "$CANDIDATE_ARTIFACT_URI" "$CANDIDATE_ARTIFACT_SHA256" \
      "$REBOOT_SHA256" "$CONTRACT_SHA256" <<'PY'
import json
import pathlib
import sys

payload = {
    "schema_version": 1,
    "source_ref": sys.argv[2],
    "source_artifact_uri": sys.argv[3],
    "source_artifact_sha256": sys.argv[4],
    "reboot_runtime_sha256": sys.argv[5],
    "runtime_contract_sha256": sys.argv[6],
}
pathlib.Path(sys.argv[1]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
    chmod 0400 "${HELPER_TMP}/source.json"
    mv "$HELPER_TMP" "$HELPER_ROOT"
  fi
  emit "shared reboot helper bundle" "PASS" "candidate_ref=${CANDIDATE_REF} exact hashes recorded"

  STATE_STAGE="$(mktemp -d "${STATE_BUNDLES_ROOT}/.legacy.${CURRENT_REF}.XXXXXX")"
  ACTIVE_PROVENANCE="${STATE_STAGE}/runtime-provenance.json"
  python3 "$RUNTIME_CONTRACT" bootstrap-record --compose-project "$COMPOSE_PROJECT" \
    --deploy-ref "$CURRENT_REF" --version "$CURRENT_VERSION" \
    --output "$ACTIVE_PROVENANCE"
  python3 - "$ACTIVE_PROVENANCE" "${STATE_STAGE}/bootstrap-state.json" \
    "$CURRENT_REF" "$CURRENT_VERSION" "$CANDIDATE_REF" \
    "$CANDIDATE_ARTIFACT_URI" "$CANDIDATE_ARTIFACT_SHA256" \
    "$REBOOT_SHA256" "$CONTRACT_SHA256" <<'PY'
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

provenance_path = pathlib.Path(sys.argv[1])
bootstrap_path = pathlib.Path(sys.argv[2])
provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
provenance["legacy_adoption"] = {
    "candidate_helper_ref": sys.argv[5],
    "candidate_artifact_uri": sys.argv[6],
    "candidate_artifact_sha256": sys.argv[7],
    "validated_at": datetime.now(timezone.utc).isoformat(),
}
provenance_path.write_text(
    json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
provenance_sha = hashlib.sha256(provenance_path.read_bytes()).hexdigest()
bootstrap = {
    "schema_version": 2,
    "state": "complete",
    "deploy_ref": sys.argv[3],
    "version": sys.argv[4],
    "runtime_provenance_sha256": provenance_sha,
    "reboot_helper": {
        "mode": "shared",
        "source_ref": sys.argv[5],
        "source_artifact_uri": sys.argv[6],
        "source_artifact_sha256": sys.argv[7],
        "reboot_runtime_sha256": sys.argv[8],
        "runtime_contract_sha256": sys.argv[9],
    },
    "completed_at": datetime.now(timezone.utc).isoformat(),
}
bootstrap_path.write_text(
    json.dumps(bootstrap, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
  chmod 0600 "$ACTIVE_PROVENANCE" "${STATE_STAGE}/bootstrap-state.json"
fi

PROVENANCE_MODE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["mode"])' "$ACTIVE_PROVENANCE")"
PROVENANCE_ARGS=()
if [[ "$PROVENANCE_MODE" == "day2" ]]; then
  LOCK_ENV="${SHARED_ROOT}/image-locks/${CURRENT_REF}/release-images.env"
  RELEASE_COMPOSE="${CURRENT_RELEASE}/infra/terraform-gcp/release/docker-compose.release.yml"
  if [[ ! -s "$LOCK_ENV" || ! -s "$RELEASE_COMPOSE" ]]; then
    fail "day-2 runtime inputs" "exact image lock or release overlay missing" 25
  fi
  COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
    --env-file "$LOCK_ENV" -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" \
    -f "$RELEASE_COMPOSE" --profile sap)
  PROVENANCE_ARGS=(--lock-env "$LOCK_ENV")
elif [[ "$PROVENANCE_MODE" == "bootstrap" ]]; then
  COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
    -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" --profile sap)
else
  fail "runtime provenance" "unsupported provenance mode" 25
fi
"${COMPOSE[@]}" config -q
emit "shared runtime inputs" "PASS" "host-owned env and generated GCP overlay are canonical"

if ! python3 - "$CURRENT_VERSION" <<'PY'
import json
import sys
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=5) as response:
    if response.status != 200:
        raise SystemExit(1)
    payload = json.load(response)
if payload.get("version") != sys.argv[1] or payload.get("app_env") != "production":
    raise SystemExit(1)
if payload.get("ok") is not True and payload.get("status") not in {"ok", "healthy"}:
    raise SystemExit(1)
PY
then
  fail "live release coherence" "healthz version/app_env differs from current release" 27
fi
if ! python3 "$RUNTIME_CONTRACT" provenance --provenance "$ACTIVE_PROVENANCE" \
    --compose-project "$COMPOSE_PROJECT" --deploy-ref "$CURRENT_REF" \
    --version "$CURRENT_VERSION" --one-shots "${PROVENANCE_ARGS[@]}" >/dev/null; then
  fail "live runtime provenance" "containers, images, project, health, or scheduler drifted" 27
fi
emit "live release coherence" "PASS" "healthz.version=${CURRENT_VERSION} runtime provenance exact scheduler=1"

if [[ "$ADOPTION_NEEDED" == "1" ]]; then
  STATE_FINAL="${STATE_BUNDLES_ROOT}/legacy-${CURRENT_REF}-by-${CANDIDATE_REF}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  mv "$STATE_STAGE" "$STATE_FINAL"
  STATE_STAGE=""
  STATE_PREVIEW="${SHARED_ROOT}/.runtime-state.${CURRENT_REF}.$$"
  ln -s "$STATE_FINAL" "$STATE_PREVIEW"
  if ! OMEGA_GCP_STATE_LINK_OVERRIDE="$STATE_PREVIEW" /usr/local/sbin/omega-operation-gate; then
    fail "legacy runtime adoption" "staged atomic state/helper verification failed" 27
  fi
  mv -Tf "$STATE_PREVIEW" "$STATE_LINK"
  STATE_PREVIEW=""
  STATE_FINAL=""
  ACTIVE_PROVENANCE="$RUNTIME_PROVENANCE"
  emit "legacy runtime adoption" "PASS" "current=${CURRENT_REF} candidate_helper=${CANDIDATE_REF} state_pair=atomic"
fi

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

wait_for_runtime_restore() {
  local scheduler_id count service ids state health all_green
  for _ in $(seq 1 60); do
    all_green=1
    for service in "${RUNNING_BEFORE[@]}"; do
      ids="$(docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
        --filter "label=com.docker.compose.service=${service}")"
      if [[ "$(grep -c . <<<"$ids" || true)" != "1" ]]; then
        all_green=0
        break
      fi
      state="$(docker inspect "$ids" --format '{{.State.Running}}' 2>/dev/null || true)"
      health="$(docker inspect "$ids" --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || true)"
      if [[ "$state" != "true" || ( -n "$health" && "$health" != "healthy" ) ]]; then
        all_green=0
        break
      fi
    done
    scheduler_id="$(docker ps -q --filter 'label=com.docker.compose.service=airflow-scheduler')"
    count="$(grep -c . <<<"$scheduler_id" || true)"
    if [[ "$all_green" == "1" && "$count" == "1" ]] && \
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
      if ! wait_for_runtime_restore; then
        emit "runtime restored after backup" "FAIL" "not every previously-active service returned running/healthy"
        rc=71
      else
        rm -f -- "$OPERATION_MARKER"
        RUNTIME_FENCED=0
        emit "runtime restored after backup" "PASS" "all previously-active services running/healthy; scheduler=1"
      fi
    fi
  fi
  if [[ -n "$WORKDIR" && "$WORKDIR" == /tmp/omega-gcp-backup.* ]]; then
    rm -rf -- "$WORKDIR"
  fi
  exit "$rc"
}
trap restore_runtime EXIT

write_operation_state "fencing"
RUNTIME_FENCED=1
"${COMPOSE[@]}" stop --timeout 60 "${WRITER_SERVICES[@]}"
for service in "${WRITER_SERVICES[@]}"; do
  if docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
      --filter "label=com.docker.compose.service=${service}" | grep -q .; then
    fail "writer fence" "service still running=${service}" 28
  fi
done
write_operation_state "fenced"
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

python3 - "${WORKDIR}/database-images.json" mode_postgres mode_postgres_gold <<'PY'
import json
import re
import subprocess
import sys

output = sys.argv[1]
images = {}
for name, container in zip(("postgres", "postgres_gold"), sys.argv[2:]):
    info = json.loads(
        subprocess.run(
            ["docker", "inspect", container],
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout
    )[0]
    configured_ref = info["Config"]["Image"]
    image_id = info["Image"]
    image = json.loads(
        subprocess.run(
            ["docker", "image", "inspect", image_id],
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout
    )[0]
    repository = configured_ref.rsplit(":", 1)[0]
    matches = sorted(
        value
        for value in image.get("RepoDigests") or []
        if value.startswith(repository + "@sha256:")
    )
    if len(matches) != 1 or not re.fullmatch(
        re.escape(repository) + r"@sha256:[0-9a-f]{64}", matches[0]
    ):
        raise SystemExit(f"database image lacks one immutable RepoDigest: {name}")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise SystemExit(f"database image ID is invalid: {name}")
    images[name] = {
        "configured_ref": configured_ref,
        "repo_digest": matches[0],
        "image_id": image_id,
    }
with open(output, "w", encoding="utf-8") as stream:
    json.dump(images, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
install -m 0600 "$RUNTIME_PROVENANCE" "${WORKDIR}/runtime-provenance.json"

upload_immutable() {
  local source="$1" key="$2" metadata_output="$3"
  python3 - "$source" "$LAKEHOUSE_BUCKET" "$key" "$metadata_output" <<'PY'
import http.client
import json
import pathlib
import sys
import urllib.parse
import urllib.request

source, bucket, key, metadata_output = sys.argv[1:]
token_request = urllib.request.Request(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
)
with urllib.request.urlopen(token_request, timeout=10) as response:
    token = json.load(response)["access_token"]
target = "/upload/storage/v1/b/{}/o?{}".format(
    urllib.parse.quote(bucket, safe=""),
    urllib.parse.urlencode(
        {"uploadType": "media", "name": key, "ifGenerationMatch": "0"}
    ),
)
path = pathlib.Path(source)
connection = http.client.HTTPSConnection("storage.googleapis.com", timeout=180)
with path.open("rb") as stream:
    connection.request(
        "POST",
        target,
        body=stream,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(path.stat().st_size),
        },
    )
    response = connection.getresponse()
    body = response.read()
connection.close()
if response.status != 200:
    raise SystemExit(f"immutable GCS upload failed: http={response.status}")
payload = json.loads(body)
if not payload.get("generation") or not payload.get("crc32c") or not payload.get("size"):
    raise SystemExit("uploaded object metadata is incomplete")
pathlib.Path(metadata_output).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
}

upload_immutable "${WORKDIR}/postgres.sql.gz" "${BACKUP_PREFIX}/postgres.sql.gz" "${WORKDIR}/postgres.meta.json"
upload_immutable "${WORKDIR}/postgres_gold.sql.gz" "${BACKUP_PREFIX}/postgres_gold.sql.gz" "${WORKDIR}/postgres_gold.meta.json"
upload_immutable "${WORKDIR}/lakehouse_objects.jsonl" "${BACKUP_PREFIX}/lakehouse_objects.jsonl" "${WORKDIR}/objects.meta.json"
upload_immutable "${WORKDIR}/runtime-images.json" "${BACKUP_PREFIX}/runtime-images.json" "${WORKDIR}/images.meta.json"
upload_immutable "${WORKDIR}/runtime-provenance.json" "${BACKUP_PREFIX}/runtime-provenance.json" "${WORKDIR}/provenance.meta.json"

SOURCE_REF="$CURRENT_REF"
SOURCE_VERSION="$CURRENT_VERSION"
python3 - "${WORKDIR}/manifest.json" "$BACKUP_ID" "$LAKEHOUSE_BUCKET" "$BACKUP_PREFIX" \
  "$SOURCE_REF" "$SOURCE_VERSION" "$OBJECT_COUNT" "$OBJECT_BYTES" \
  "${WORKDIR}/postgres.meta.json" "${WORKDIR}/postgres_gold.meta.json" \
  "${WORKDIR}/objects.meta.json" "${WORKDIR}/images.meta.json" "${WORKDIR}/provenance.meta.json" \
  "${WORKDIR}/postgres.sql.gz" "${WORKDIR}/postgres_gold.sql.gz" \
  "${WORKDIR}/lakehouse_objects.jsonl" "${WORKDIR}/runtime-images.json" \
  "${WORKDIR}/runtime-provenance.json" "${WORKDIR}/database-images.json" <<'PY'
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

(output, backup_id, bucket, prefix, source_ref, source_version, object_count,
 object_bytes, *paths) = sys.argv[1:]
meta_paths = paths[:5]
local_paths = paths[5:10]
database_images_path = paths[10]
names = ["postgres", "postgres_gold", "object_manifest", "runtime_images", "runtime_provenance"]
keys = ["postgres.sql.gz", "postgres_gold.sql.gz", "lakehouse_objects.jsonl", "runtime-images.json", "runtime-provenance.json"]
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
    "schema_version": 2,
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
    "database_images": json.load(open(database_images_path, encoding="utf-8")),
    "runtime_provenance_sha256": artifacts["runtime_provenance"]["sha256"],
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

write_operation_state "captured"
"${COMPOSE[@]}" start "${RUNNING_BEFORE[@]}" >/dev/null
if ! wait_for_runtime_restore; then
  fail "runtime restored after backup" "not every previously-active service returned running/healthy" 30
fi
RUNTIME_FENCED=0
write_operation_state "restored"
rm -f -- "$OPERATION_MARKER"
emit "runtime restored after backup" "PASS" "all previously-active services running/healthy; scheduler=1"
printf 'OMEGA_GCP_BACKUP_JSON={"status":"PASS","backup_id":"%s","manifest_uri":"gs://%s/%s/manifest.json","manifest_sha256":"%s","manifest_generation":"%s","object_count":%s}\n' \
  "$BACKUP_ID" "$LAKEHOUSE_BUCKET" "$BACKUP_PREFIX" "$MANIFEST_SHA" "$MANIFEST_GENERATION" "$OBJECT_COUNT"
