#!/usr/bin/env bash
# Deploy one already-published immutable release to the canonical GCP host.
#
# This script is sent over IAP SSH by scripts/gcp_release.py.  It deliberately
# does not fetch Git, build images, read GHCR credentials, or mutate DNS/cloud
# topology.  Private registry access is delegated to a server-owned runner.
set -Eeuo pipefail
set +x
umask 077

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: day2-release.sh must run through sudo" >&2
  exit 10
fi

TARGET_TAG="${1:-}"
DEPLOY_REF="${2:-}"
ARTIFACT_URI="${3:-}"
ARTIFACT_SHA256="${4:-}"
EXPECTED_VERSION="${5:-}"
BACKUP_MANIFEST_URI="${6:-}"
BACKUP_MANIFEST_SHA256="${7:-}"
GHCR_OWNER="${8:-emmanuelnavaromero02-commits}"
COMPOSE_PROJECT="${9:-infra}"
GCP_ENVIRONMENT="${10:-}"
GHCR_SECRET_VERSION="${11:-}"

APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
RELEASE_ROOT="${APP_ROOT}/releases"
CURRENT_LINK="${APP_ROOT}/current"
PREVIOUS_LINK="${APP_ROOT}/previous"
SHARED_ROOT="${APP_ROOT}/shared"
SHARED_ENV="${SHARED_ROOT}/infra.env"
GCP_RUNTIME_COMPOSE="${SHARED_ROOT}/docker-compose.gcp.yml"
AUTH_RUNNER="${OMEGA_GCP_GHCR_AUTH_RUNNER:-${SHARED_ROOT}/bin/ghcr-auth-run}"
RELEASE_DIR="${RELEASE_ROOT}/${DEPLOY_REF}"
LOCK_DIR="${SHARED_ROOT}/image-locks/${DEPLOY_REF}"
LOCK_ENV="${LOCK_DIR}/release-images.env"
LOCK_MANIFEST="${LOCK_DIR}/manifest.json"
OPERATION_MARKER="${SHARED_ROOT}/operation-state.json"
DAY2_STATE="${SHARED_ROOT}/day2-initialized.json"
STATE_LINK="${SHARED_ROOT}/runtime-state"
STATE_BUNDLES_ROOT="${SHARED_ROOT}/state-bundles"
BOOTSTRAP_STATE="${STATE_LINK}/bootstrap-state.json"
RUNTIME_PROVENANCE="${STATE_LINK}/runtime-provenance.json"
DEPLOYMENT_ROOT="${SHARED_ROOT}/deployments"
DEPLOYMENT_PROVENANCE="${DEPLOYMENT_ROOT}/${DEPLOY_REF}.json"
MUTATION_STARTED=0
COMPOSE_RUNTIME_READY=0
WORKDIR=""
RELEASE_TMP=""
LOCK_TMP=""
STATE_STAGE=""
STATE_PREVIEW=""
STATE_FINAL=""
CURRENT_PREVIEW=""
WRITER_SERVICES=(airflow-scheduler console workspace refinement vault mcp-infra airflow replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-successfactors sap-s4hana superset)
ONE_SHOT_MUTATORS=(airflow-init minio-init postgres_dev_seed superset-init)
MUTATING_SERVICES=("${WRITER_SERVICES[@]}" "${ONE_SHOT_MUTATORS[@]}")

emit() {
  local name="$1" status="$2" evidence="${3:-}"
  evidence="${evidence//$'\t'/ }"
  evidence="${evidence//$'\r'/ }"
  evidence="${evidence//$'\n'/ }"
  printf 'OMEGA_GCP_RELEASE_CHECK\t%s\t%s\t%s\n' "$name" "$status" "$evidence"
}

cleanup() {
  local rc=$?
  if [[ -n "${WORKDIR}" && "${WORKDIR}" == /tmp/omega-gcp-release.* ]]; then
    rm -rf -- "${WORKDIR}"
  fi
  if [[ -n "${RELEASE_TMP}" && "${RELEASE_TMP}" == "${RELEASE_ROOT}/.${DEPLOY_REF}.tmp."* ]]; then
    rm -rf -- "${RELEASE_TMP}"
  fi
  if [[ -n "${LOCK_TMP}" && "${LOCK_TMP}" == "${SHARED_ROOT}/image-locks/.${DEPLOY_REF}.tmp."* ]]; then
    rm -rf -- "${LOCK_TMP}"
  fi
  if [[ -n "${STATE_PREVIEW}" && "${STATE_PREVIEW}" == "${SHARED_ROOT}/.runtime-state."* ]]; then
    rm -f -- "${STATE_PREVIEW}"
  fi
  if [[ -n "${STATE_STAGE}" && "${STATE_STAGE}" == "${STATE_BUNDLES_ROOT}/."* ]]; then
    rm -rf -- "${STATE_STAGE}"
  fi
  if [[ -n "${STATE_FINAL}" && "${STATE_FINAL}" == "${STATE_BUNDLES_ROOT}/day2-"* ]]; then
    rm -rf -- "${STATE_FINAL}"
  fi
  if [[ -n "${CURRENT_PREVIEW}" && "${CURRENT_PREVIEW}" == "${APP_ROOT}/.candidate-current."* ]]; then
    rm -f -- "${CURRENT_PREVIEW}"
  fi
  if [[ "$rc" -ne 0 && "$MUTATION_STARTED" == "1" ]]; then
    if [[ "$COMPOSE_RUNTIME_READY" == "1" ]]; then
      "${COMPOSE[@]}" stop --timeout 30 "${MUTATING_SERVICES[@]}" >/dev/null 2>&1 || true
    fi
    emit "fail-closed runtime fence" "PASS" "writers, scheduler, and one-shot mutators remain stopped; durable operation marker retained"
  fi
  exit "$rc"
}
trap cleanup EXIT

fail() {
  emit "$1" "FAIL" "${2:-}"
  exit "${3:-20}"
}

install_operation_guard() {
  local source="$1" guard_tmp dropin_tmp
  if [[ ! -x "$source" ]]; then
    fail "reboot operation guard" "exact candidate operation gate is missing" 38
  fi
  guard_tmp="$(mktemp /tmp/omega-operation-gate.XXXXXX)"
  dropin_tmp="$(mktemp /tmp/omega-docker-operation-gate.XXXXXX)"
  install -m 0755 "$source" "$guard_tmp"
  printf '%s\n' \
    '[Service]' \
    'ExecStartPre=/usr/local/sbin/omega-operation-gate' > "$dropin_tmp"
  install -m 0755 "$guard_tmp" /usr/local/sbin/omega-operation-gate
  install -d -m 0755 /etc/systemd/system/docker.service.d
  install -m 0644 "$dropin_tmp" /etc/systemd/system/docker.service.d/omega-operation-gate.conf
  rm -f -- "$guard_tmp" "$dropin_tmp"
  systemctl daemon-reload
}

write_operation_state() {
  local state="$1"
  python3 - "$OPERATION_MARKER" "$state" "$DEPLOY_REF" "$OLD_REF" <<'PY'
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "operation": "deploy",
    "state": sys.argv[2],
    "deploy_ref": sys.argv[3],
    "previous_ref": sys.argv[4],
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

if [[ ! "$TARGET_TAG" =~ ^v[0-9][0-9A-Za-z._-]*$ || "$TARGET_TAG" == *latest* ]]; then
  fail "immutable release tag" "invalid tag" 20
fi
if [[ ! "$DEPLOY_REF" =~ ^[0-9a-f]{40}$ ]]; then
  fail "exact deploy ref" "must be a full lowercase SHA" 21
fi
if [[ ! "$ARTIFACT_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "artifact checksum input" "invalid sha256" 22
fi
if [[ ! "$BACKUP_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "backup checksum input" "invalid sha256" 23
fi
if [[ ! "$ARTIFACT_URI" =~ ^gs://[^/]+/deploy-artifacts/${DEPLOY_REF}/repo\.tar\.gz$ ]]; then
  fail "immutable artifact URI" "URI is not bound to DEPLOY_REF" 24
fi
if [[ ! "$BACKUP_MANIFEST_URI" =~ ^gs://[^/]+/_omega_backups/[^/]+/manifest\.json$ ]]; then
  fail "pre-deploy backup URI" "unexpected backup manifest URI" 25
fi
if [[ ! "$GHCR_OWNER" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  fail "GHCR owner" "invalid owner" 26
fi
if [[ ! "$COMPOSE_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  fail "Compose project" "invalid project name" 27
fi
if [[ ! "$GCP_ENVIRONMENT" =~ ^[a-z][a-z0-9-]*$ ]]; then
  fail "GCP environment" "explicit environment is required" 27
fi
if [[ ! "$GHCR_SECRET_VERSION" =~ ^[1-9][0-9]*$ ]]; then
  fail "GHCR secret version" "an explicit numeric Secret Manager version is required" 27
fi
if [[ ! -L "$CURRENT_LINK" ]]; then
  fail "current release link" "${CURRENT_LINK} must be a symlink" 28
fi
OLD_RELEASE="$(readlink -f "$CURRENT_LINK")"
case "$OLD_RELEASE" in
  "${RELEASE_ROOT}/"*) ;;
  *) fail "current release confinement" "current points outside release root" 31 ;;
esac
OLD_REF="$(basename "$OLD_RELEASE")"
OLD_VERSION="$(tr -d '\r\n' < "${OLD_RELEASE}/VERSION" 2>/dev/null || true)"
if [[ ! "$OLD_REF" =~ ^[0-9a-f]{40}$ || -z "$OLD_VERSION" ]]; then
  fail "current release identity" "current release ref or VERSION is invalid" 29
fi
emit "current release captured" "PASS" "ref=${OLD_REF} version=${OLD_VERSION:-unknown}"

install -d -m 0755 "$RELEASE_ROOT" "$SHARED_ROOT" "${SHARED_ROOT}/airflow/logs" \
  "${SHARED_ROOT}/airflow/plugins" "${SHARED_ROOT}/gcp-local-minio" \
  "${SHARED_ROOT}/image-locks" "$DEPLOYMENT_ROOT"
install -d -m 0700 "$STATE_BUNDLES_ROOT"

exec 9>"/var/lock/omega-gcp-day2.lock"
if ! flock -n 9; then
  fail "exclusive day-2 lock" "another release/backup operation is active" 33
fi
emit "exclusive day-2 lock" "PASS" "acquired"

if [[ -e "$OPERATION_MARKER" ]]; then
  fail "durable operation fence" "an incomplete operation marker already exists" 32
fi
if [[ ! -s "$SHARED_ENV" || ! -s "$GCP_RUNTIME_COMPOSE" || \
      ! -s "$BOOTSTRAP_STATE" || ! -s "$RUNTIME_PROVENANCE" ]]; then
  fail "shared runtime inputs" "host-owned env, overlay, or atomic runtime state is missing" 32
fi
if grep -Eq '^(GHCR_[A-Z0-9_]*(TOKEN|PASSWORD|SECRET|CREDENTIAL|AUTH|USER)|GITHUB_TOKEN|DOCKER_AUTH_CONFIG)=' "$SHARED_ENV"; then
  fail "server-owned registry credential boundary" "registry credential found in runtime env" 32
fi
emit "shared runtime inputs" "PASS" "host-owned env and generated GCP overlay are canonical"
if [[ ! -x /usr/local/sbin/omega-operation-gate ]] || \
    ! /usr/local/sbin/omega-operation-gate; then
  fail "reboot operation guard" "installed Docker gate rejected the current atomic state" 32
fi

AIRFLOW_MIGRATION_MARKER="${SHARED_ROOT}/.airflow-runtime-migrated"
if [[ ! -e "$AIRFLOW_MIGRATION_MARKER" ]]; then
  for runtime_dir in logs plugins; do
    if [[ -d "${OLD_RELEASE}/airflow/${runtime_dir}" ]]; then
      cp -a "${OLD_RELEASE}/airflow/${runtime_dir}/." "${SHARED_ROOT}/airflow/${runtime_dir}/"
    fi
  done
  chown -R 50000:0 "${SHARED_ROOT}/airflow/logs" "${SHARED_ROOT}/airflow/plugins"
  touch "$AIRFLOW_MIGRATION_MARKER"
  emit "mutable Airflow mounts" "PASS" "existing logs/plugins preserved outside immutable releases"
fi

WORKDIR="$(mktemp -d /tmp/omega-gcp-release.XXXXXX)"

gcs_download() {
  python3 - "$1" "$2" <<'PY'
import json
import pathlib
import sys
import urllib.parse
import urllib.request

uri, destination = sys.argv[1:]
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
url = f"https://storage.googleapis.com/download/storage/v1/b/{bucket}/o/{key}?alt=media"
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

BACKUP_MANIFEST="${WORKDIR}/backup-manifest.json"
gcs_download "$BACKUP_MANIFEST_URI" "$BACKUP_MANIFEST"
BACKUP_ACTUAL_SHA="$(sha256sum "$BACKUP_MANIFEST" | awk '{print $1}')"
if [[ "$BACKUP_ACTUAL_SHA" != "$BACKUP_MANIFEST_SHA256" ]]; then
  fail "pre-deploy backup checksum" "actual=${BACKUP_ACTUAL_SHA}" 34
fi
python3 - "$BACKUP_MANIFEST" "$OLD_REF" "$OLD_VERSION" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if manifest.get("schema_version") != 2 or manifest.get("complete") is not True:
    raise SystemExit("backup manifest is not complete schema v2")
consistency = manifest.get("consistency", {})
if not consistency.get("writers_fenced") or not consistency.get("scheduler_fenced"):
    raise SystemExit("backup was not captured under the canonical writer fence")
if not manifest.get("object_storage", {}).get("versioning_enabled"):
    raise SystemExit("backup lacks a versioned object-storage restore point")
if manifest.get("source_release", {}).get("deploy_ref") != sys.argv[2]:
    raise SystemExit("backup source commit does not match current release")
if manifest.get("source_release", {}).get("version") != sys.argv[3]:
    raise SystemExit("backup source version does not match current release")
artifacts = manifest.get("artifacts", {})
for name in ("postgres", "postgres_gold", "object_manifest"):
    artifact = artifacts.get(name, {})
    if not artifact.get("uri") or not artifact.get("sha256") or not artifact.get("generation"):
        raise SystemExit(f"backup artifact {name} is incomplete")
PY
emit "pre-deploy backup verified" "PASS" "manifest_sha256=${BACKUP_ACTUAL_SHA} source=${OLD_REF}"

ARTIFACT="${WORKDIR}/repo.tar.gz"
gcs_download "$ARTIFACT_URI" "$ARTIFACT"
ARTIFACT_ACTUAL_SHA="$(sha256sum "$ARTIFACT" | awk '{print $1}')"
if [[ "$ARTIFACT_ACTUAL_SHA" != "$ARTIFACT_SHA256" ]]; then
  fail "release artifact checksum" "actual=${ARTIFACT_ACTUAL_SHA}" 35
fi
emit "release artifact checksum" "PASS" "sha256=${ARTIFACT_ACTUAL_SHA}"

python3 - "$ARTIFACT" <<'PY'
import pathlib
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:gz") as archive:
    members = archive.getmembers()
    if not members:
        raise SystemExit("empty release archive")
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk():
            link = pathlib.PurePosixPath(member.linkname)
            if link.is_absolute() or ".." in link.parts:
                raise SystemExit(f"unsafe archive link: {member.name}")
PY

RELEASE_MARKER="${RELEASE_DIR}/.omega-release.json"
if [[ -d "$RELEASE_DIR" ]]; then
  python3 - "$RELEASE_MARKER" "$DEPLOY_REF" "$TARGET_TAG" "$ARTIFACT_SHA256" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
expected = {"deploy_ref": sys.argv[2], "tag": sys.argv[3], "artifact_sha256": sys.argv[4]}
for key, value in expected.items():
    if payload.get(key) != value:
        raise SystemExit(f"existing immutable release marker mismatch: {key}")
PY
  emit "immutable release directory" "PASS" "existing verified release reused"
else
  RELEASE_TMP="${RELEASE_ROOT}/.${DEPLOY_REF}.tmp.$$"
  if [[ -e "$RELEASE_TMP" ]]; then
    fail "immutable release directory" "unexpected temporary path already exists" 36
  fi
  install -d -m 0755 "$RELEASE_TMP"
  tar -xzf "$ARTIFACT" -C "$RELEASE_TMP"
  python3 - "$RELEASE_TMP/.omega-release.json" "$DEPLOY_REF" "$TARGET_TAG" "$ARTIFACT_SHA256" "$EXPECTED_VERSION" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "deploy_ref": sys.argv[2],
    "tag": sys.argv[3],
    "artifact_sha256": sys.argv[4],
    "version": sys.argv[5],
    "installed_at": datetime.now(timezone.utc).isoformat(),
}
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  chmod -R a-w "$RELEASE_TMP"
  mv "$RELEASE_TMP" "$RELEASE_DIR"
  RELEASE_TMP=""
  emit "immutable release directory" "PASS" "created=${RELEASE_DIR}"
fi

ACTUAL_VERSION="$(tr -d '\r\n' < "${RELEASE_DIR}/VERSION")"
if [[ "$ACTUAL_VERSION" != "$EXPECTED_VERSION" || "v${ACTUAL_VERSION}" != "$TARGET_TAG" ]]; then
  fail "release VERSION" "actual=${ACTUAL_VERSION} expected=${EXPECTED_VERSION} tag=${TARGET_TAG}" 37
fi
emit "release VERSION" "PASS" "version=${ACTUAL_VERSION}"

BASE_COMPOSE="${RELEASE_DIR}/infra/docker-compose.yml"
RELEASE_COMPOSE="${RELEASE_DIR}/infra/terraform-gcp/release/docker-compose.release.yml"
if [[ ! -s "$BASE_COMPOSE" || ! -s "$RELEASE_COMPOSE" ]]; then
  fail "release Compose files" "candidate Compose inputs missing" 38
fi

CANDIDATE_AUTH_RUNNER="${RELEASE_DIR}/infra/terraform-gcp/release/ghcr-auth-run.sh"
CANDIDATE_PREFLIGHT="${RELEASE_DIR}/infra/terraform-gcp/release/preflight-release-images.sh"
RUNTIME_CONTRACT="${RELEASE_DIR}/scripts/gcp/runtime_contract.py"
CANDIDATE_REBOOT_HELPER="${RELEASE_DIR}/scripts/gcp/reboot-runtime.sh"
CANDIDATE_OPERATION_GUARD="${RELEASE_DIR}/infra/terraform-gcp/templates/omega-operation-gate"
if [[ ! -x "$CANDIDATE_AUTH_RUNNER" || ! -x "$CANDIDATE_PREFLIGHT" || \
      ! -x "$RUNTIME_CONTRACT" || ! -x "$CANDIDATE_REBOOT_HELPER" || \
      ! -x "$CANDIDATE_OPERATION_GUARD" ]]; then
  fail "candidate release helpers" "release does not contain the audited auth, preflight, and runtime helpers" 38
fi
install_operation_guard "$CANDIDATE_OPERATION_GUARD"
if ! /usr/local/sbin/omega-operation-gate; then
  fail "reboot operation guard" "candidate guard rejected the current atomic state" 38
fi
emit "reboot operation guard" "PASS" "exact candidate ExecStartPre installed and current state verified"
install -d -m 0700 "$(dirname "$AUTH_RUNNER")"
AUTH_RUNNER_TMP="$(dirname "$AUTH_RUNNER")/.ghcr-auth-run.${DEPLOY_REF}.$$"
install -m 0700 "$CANDIDATE_AUTH_RUNNER" "$AUTH_RUNNER_TMP"
mv -Tf "$AUTH_RUNNER_TMP" "$AUTH_RUNNER"
emit "server-owned GHCR auth runner" "PASS" "installed from exact release artifact"

RELEASE_SERVICES=(console workspace refinement vault mcp-infra airflow replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-successfactors sap-s4hana)
LOCK_TMP="${SHARED_ROOT}/image-locks/.${DEPLOY_REF}.tmp.$$"
install -d -m 0700 "$LOCK_TMP"
NEW_LOCK_ENV="${LOCK_TMP}/release-images.env"
NEW_LOCK_MANIFEST="${LOCK_TMP}/manifest.json"
if ! OMEGA_GCP_ENVIRONMENT="$GCP_ENVIRONMENT" \
  OMEGA_GHCR_PULL_SECRET_VERSION="$GHCR_SECRET_VERSION" \
  "$AUTH_RUNNER" "$CANDIDATE_PREFLIGHT" "$GHCR_OWNER" "$TARGET_TAG" "$NEW_LOCK_ENV" >/dev/null 2>&1; then
  fail "private GHCR release pull" "less than 15/15 images pullable; credential output suppressed" 41
fi
emit "private GHCR release pull" "PASS" "15/15 tag=${TARGET_TAG}"

python3 - "$NEW_LOCK_ENV" "$NEW_LOCK_MANIFEST" "$DEPLOY_REF" "$EXPECTED_VERSION" \
  "$ARTIFACT_SHA256" "$GHCR_OWNER" "$COMPOSE_PROJECT" "$TARGET_TAG" <<'PY'
import json
import pathlib
import re
import sys

lock_path, manifest_path, deploy_ref, version, artifact_sha, owner, project, tag = sys.argv[1:]
assignments = {}
for raw_line in pathlib.Path(lock_path).read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    key, value = line.split("=", 1)
    if key in assignments:
        raise SystemExit("duplicate image lock key")
    assignments[key] = value
image_keys = {
    "airflow": "OMEGA_GCP_IMAGE_AIRFLOW",
    "banxico": "OMEGA_GCP_IMAGE_BANXICO",
    "console": "OMEGA_GCP_IMAGE_CONSOLE",
    "hubspot": "OMEGA_GCP_IMAGE_HUBSPOT",
    "inegi": "OMEGA_GCP_IMAGE_INEGI",
    "mcp-infra": "OMEGA_GCP_IMAGE_MCP_INFRA",
    "refinement": "OMEGA_GCP_IMAGE_REFINEMENT",
    "replicon": "OMEGA_GCP_IMAGE_REPLICON",
    "salesforce": "OMEGA_GCP_IMAGE_SALESFORCE",
    "sap_hcm": "OMEGA_GCP_IMAGE_SAP_HCM",
    "sap_s4hana": "OMEGA_GCP_IMAGE_SAP_S4HANA",
    "sap_successfactors": "OMEGA_GCP_IMAGE_SAP_SUCCESSFACTORS",
    "sec_edgar": "OMEGA_GCP_IMAGE_SEC_EDGAR",
    "vault": "OMEGA_GCP_IMAGE_VAULT",
    "workspace": "OMEGA_GCP_IMAGE_WORKSPACE",
}
if set(assignments) != set(image_keys.values()):
    raise SystemExit("release lock does not contain exactly 15 expected keys")
images = {name: assignments[key] for name, key in image_keys.items()}
for name, value in images.items():
    if not re.fullmatch(
        rf"ghcr\.io/{re.escape(owner)}/{re.escape(name)}:{re.escape(tag)}@sha256:[0-9a-f]{{64}}",
        value,
    ):
        raise SystemExit(f"invalid locked digest for {name}")
service_images = {
    "console": "console",
    "workspace": "workspace",
    "refinement": "refinement",
    "vault": "vault",
    "mcp-infra": "mcp-infra",
    "airflow-init": "airflow",
    "airflow": "airflow",
    "airflow-scheduler": "airflow",
    "replicon": "replicon",
    "hubspot": "hubspot",
    "salesforce": "salesforce",
    "banxico": "banxico",
    "inegi": "inegi",
    "sec-edgar": "sec_edgar",
    "sap-hcm": "sap_hcm",
    "sap-successfactors": "sap_successfactors",
    "sap-s4hana": "sap_s4hana",
}
payload = {
    "schema_version": 2,
    "deploy_ref": deploy_ref,
    "version": version,
    "artifact_sha256": artifact_sha,
    "owner": owner,
    "compose_project": project,
    "tag": tag,
    "unique_image_count": len(images),
    "service_binding_count": len(service_images),
    "images": images,
    "services": {service: images[image] for service, image in service_images.items()},
}
pathlib.Path(manifest_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
chmod 0400 "$NEW_LOCK_ENV" "$NEW_LOCK_MANIFEST"
if [[ -d "$LOCK_DIR" ]]; then
  if [[ ! -s "$LOCK_ENV" || ! -s "$LOCK_MANIFEST" ]]; then
    fail "immutable image lock" "existing lock is incomplete" 42
  fi
  cmp "$LOCK_ENV" "$NEW_LOCK_ENV" >/dev/null || fail "immutable image lock" "existing env lock differs from authenticated pulls" 42
  cmp "$LOCK_MANIFEST" "$NEW_LOCK_MANIFEST" >/dev/null || fail "immutable image lock" "existing manifest differs from candidate identity" 42
  rm -rf -- "$LOCK_TMP"
  LOCK_TMP=""
  emit "immutable image digest lock" "PASS" "existing 15/15 lock revalidated against fresh pulls"
else
  mv "$LOCK_TMP" "$LOCK_DIR"
  LOCK_TMP=""
fi
LOCK_SHA256="$(sha256sum "$LOCK_MANIFEST" | awk '{print $1}')"
emit "immutable image digest lock" "PASS" "15/15 manifest_sha256=${LOCK_SHA256}"

COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
  --env-file "$LOCK_ENV" -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" \
  -f "$RELEASE_COMPOSE" --profile sap)
COMPOSE_RUNTIME_READY=1
"${COMPOSE[@]}" config -q
AVAILABLE_SERVICES="$("${COMPOSE[@]}" config --services)"
for service in "${RELEASE_SERVICES[@]}"; do
  if ! grep -qx "$service" <<<"$AVAILABLE_SERVICES"; then
    fail "release image inventory" "missing service=${service}" 40
  fi
done

volume_for() {
  docker inspect "$1" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}'
}
for container in mode_postgres mode_postgres_gold; do
  actual_project="$(docker inspect "$container" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || true)"
  if [[ "$actual_project" != "$COMPOSE_PROJECT" ]]; then
    fail "Compose project identity" "${container} project=${actual_project:-missing}" 43
  fi
done
OLD_POSTGRES_VOLUME="$(volume_for mode_postgres)"
OLD_GOLD_VOLUME="$(volume_for mode_postgres_gold)"
if [[ -z "$OLD_POSTGRES_VOLUME" || -z "$OLD_GOLD_VOLUME" ]]; then
  fail "database volume baseline" "named operational and Gold volumes are required" 43
fi

write_operation_state "fencing"
MUTATION_STARTED=1
"${COMPOSE[@]}" stop --timeout 60 "${MUTATING_SERVICES[@]}"
for service in "${MUTATING_SERVICES[@]}"; do
  if docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
      --filter "label=com.docker.compose.service=${service}" | grep -q .; then
    fail "writer and mutator fence" "service still running=${service}" 44
  fi
done
write_operation_state "fenced"
emit "writer and scheduler fence" "PASS" "all application writers and one-shot mutators stopped"

"${COMPOSE[@]}" up -d --no-build --pull never --no-deps --force-recreate postgres postgres_gold
NEW_POSTGRES_VOLUME="$(volume_for mode_postgres)"
NEW_GOLD_VOLUME="$(volume_for mode_postgres_gold)"
if [[ "$NEW_POSTGRES_VOLUME" != "$OLD_POSTGRES_VOLUME" || "$NEW_GOLD_VOLUME" != "$OLD_GOLD_VOLUME" ]]; then
  fail "database volume preservation" "named volume identity changed" 45
fi
for pair in "mode_postgres:${RELEASE_DIR}/infra/init" "mode_postgres_gold:${RELEASE_DIR}/infra/init_gold"; do
  container="${pair%%:*}"
  expected="${pair#*:}"
  actual="$(docker inspect "$container" --format '{{range .Mounts}}{{if eq .Destination "/docker-entrypoint-initdb.d"}}{{.Source}}{{end}}{{end}}')"
  if [[ "$(readlink -f "$actual")" != "$(readlink -f "$expected")" ]]; then
    fail "database migration mount" "${container} is not bound to candidate migrations" 46
  fi
done
emit "database recreation" "PASS" "named volumes preserved; candidate migration mounts active"

for container in mode_postgres mode_postgres_gold; do
  healthy=0
  for _ in $(seq 1 60); do
    if [[ "$(docker inspect "$container" --format '{{.State.Health.Status}}' 2>/dev/null || true)" == "healthy" ]]; then
      healthy=1
      break
    fi
    sleep 2
  done
  if [[ "$healthy" != "1" ]]; then
    fail "database readiness" "${container} did not become healthy" 47
  fi
done

OMEGA_MIGRATION_ENV_FILE="$SHARED_ENV" \
OMEGA_MIGRATION_COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT" \
OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT=1 \
OMEGA_MIGRATION_BOOTSTRAP_MODE=0 \
OMEGA_MIGRATION_OLD_REF="$OLD_REF" \
OMEGA_MIGRATION_CANDIDATE_REF="$DEPLOY_REF" \
OMEGA_MIGRATION_RELEASE_VERSION="$EXPECTED_VERSION" \
OMEGA_MIGRATION_BASELINE_MANIFEST="${RELEASE_DIR}/infra/migrations/manifests/gcp-live-6b12883c5b5ea0537120279ccbee4947137998a2.json" \
OMEGA_MIGRATION_BASELINE_MANIFEST_SHA256="b3b984fb88f48a5d75e172196e1981a45b54aba90eb3365d7e5afbcdac929221" \
OMEGA_MIGRATION_RELEASE_MANIFEST="${RELEASE_DIR}/infra/migrations/manifests/v1.45.207-beta.json" \
OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256="aefda14599840b6ee419eb6b76878478e6be64e2706a80363d7cfac19cdf79a6" \
  bash "${RELEASE_DIR}/scripts/apply_db_migrations.sh"
write_operation_state "migrated"
emit "canonical database migrations" "PASS" "operational and Gold runner completed"

readarray -t SERVICES_WITHOUT_SCHEDULER < <("${COMPOSE[@]}" config --services | grep -vx airflow-scheduler)
"${COMPOSE[@]}" up -d --no-build --pull never "${SERVICES_WITHOUT_SCHEDULER[@]}"

READY=0
for _ in $(seq 1 180); do
  if python3 - "$EXPECTED_VERSION" <<'PY'
import json
import sys
import urllib.request

expected = sys.argv[1]
for path in ("/healthz", "/readyz", "/readyz?require_data=1"):
    with urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=5) as response:
        if response.status != 200:
            raise SystemExit(1)
        payload = json.load(response)
        if path.endswith("require_data=1") and payload.get("ok") is not True:
            raise SystemExit(1)
        if not path.endswith("require_data=1") and payload.get("ok") is not True and payload.get("status") not in {"ok", "healthy", "ready"}:
            raise SystemExit(1)
with urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=5) as response:
    payload = json.load(response)
if payload.get("version") != expected:
    raise SystemExit(1)
if payload.get("app_env") != "production":
    raise SystemExit(1)
PY
  then
    READY=1
    break
  fi
  sleep 5
done
if [[ "$READY" != "1" ]]; then
  fail "candidate data readiness" "healthz/readyz/require_data did not become exact-version green" 48
fi
emit "candidate data readiness" "PASS" "healthz=200 readyz=200 require_data=200 version=${EXPECTED_VERSION}"

RUNTIME_GREEN=0
for _ in $(seq 1 60); do
  if python3 "$RUNTIME_CONTRACT" lock --lock-env "$LOCK_ENV" \
      --compose-project "$COMPOSE_PROJECT" --deploy-ref "$DEPLOY_REF" \
      --version "$EXPECTED_VERSION" --scheduler stopped --one-shots >/dev/null 2>&1; then
    RUNTIME_GREEN=1
    break
  fi
  sleep 3
done
if [[ "$RUNTIME_GREEN" != "1" ]]; then
  fail "exact pre-scheduler runtime" "15/15 services must be running, healthy, and equal to the lock; one-shots must exit zero" 49
fi
emit "exact pre-scheduler runtime" "PASS" "15/15 running+healthy+digest exact; four one-shot mutators exited zero; scheduler fenced"

"${COMPOSE[@]}" up -d --no-build --pull never airflow-scheduler
FINAL_RUNTIME_GREEN=0
for _ in $(seq 1 60); do
  if python3 "$RUNTIME_CONTRACT" lock --lock-env "$LOCK_ENV" \
      --compose-project "$COMPOSE_PROJECT" --deploy-ref "$DEPLOY_REF" \
      --version "$EXPECTED_VERSION" --scheduler required --one-shots \
      --write-provenance "$DEPLOYMENT_PROVENANCE" >/dev/null 2>&1; then
    FINAL_RUNTIME_GREEN=1
    break
  fi
  sleep 3
done
if [[ "$FINAL_RUNTIME_GREEN" != "1" ]]; then
  fail "exact final runtime" "16 running services, one healthy scheduler, and 15 exact image digests are required" 50
fi
write_operation_state "validated"
emit "exact final runtime" "PASS" "images=15/15 running=16 healthy=16 scheduler=1 exact_lock=true"

# Stage both canonical state files in one directory. The runtime-state symlink
# is the sole commit point, so no observer can see a bootstrap state without
# its matching runtime provenance.
STATE_STAGE="$(mktemp -d "${STATE_BUNDLES_ROOT}/.day2.${DEPLOY_REF}.XXXXXX")"
install -m 0600 "$DEPLOYMENT_PROVENANCE" "${STATE_STAGE}/runtime-provenance.json"
REBOOT_SHA256="$(sha256sum "$CANDIDATE_REBOOT_HELPER" | awk '{print $1}')"
CONTRACT_SHA256="$(sha256sum "$RUNTIME_CONTRACT" | awk '{print $1}')"
python3 - "${STATE_STAGE}/bootstrap-state.json" \
  "${STATE_STAGE}/runtime-provenance.json" "$DEPLOY_REF" "$EXPECTED_VERSION" \
  "$ARTIFACT_URI" "$ARTIFACT_SHA256" "$REBOOT_SHA256" "$CONTRACT_SHA256" <<'PY'
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

provenance = pathlib.Path(sys.argv[2])
payload = {
    "schema_version": 2,
    "state": "complete",
    "deploy_ref": sys.argv[3],
    "version": sys.argv[4],
    "runtime_provenance_sha256": hashlib.sha256(provenance.read_bytes()).hexdigest(),
    "reboot_helper": {
        "mode": "current",
        "source_ref": sys.argv[3],
        "source_artifact_uri": sys.argv[5],
        "source_artifact_sha256": sys.argv[6],
        "reboot_runtime_sha256": sys.argv[7],
        "runtime_contract_sha256": sys.argv[8],
    },
    "completed_at": datetime.now(timezone.utc).isoformat(),
}
pathlib.Path(sys.argv[1]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
chmod 0600 "${STATE_STAGE}/bootstrap-state.json"
STATE_FINAL="${STATE_BUNDLES_ROOT}/day2-${DEPLOY_REF}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mv "$STATE_STAGE" "$STATE_FINAL"
STATE_STAGE=""
STATE_PREVIEW="${SHARED_ROOT}/.runtime-state.${DEPLOY_REF}.$$"
CURRENT_PREVIEW="${APP_ROOT}/.candidate-current.${DEPLOY_REF}.$$"
ln -s "$STATE_FINAL" "$STATE_PREVIEW"
ln -s "$RELEASE_DIR" "$CURRENT_PREVIEW"
if ! OMEGA_GCP_ALLOW_OPERATION_MARKER=1 \
    OMEGA_GCP_STATE_LINK_OVERRIDE="$STATE_PREVIEW" \
    OMEGA_GCP_CURRENT_LINK_OVERRIDE="$CURRENT_PREVIEW" \
    /usr/local/sbin/omega-operation-gate; then
  fail "atomic runtime state" "candidate state pair/current/helper verification failed" 51
fi

PREVIOUS_TMP="${APP_ROOT}/.previous.${DEPLOY_REF}"
ln -s "$OLD_RELEASE" "$PREVIOUS_TMP"
mv -Tf "$PREVIOUS_TMP" "$PREVIOUS_LINK"
mv -Tf "$CURRENT_PREVIEW" "$CURRENT_LINK"
CURRENT_PREVIEW=""
mv -Tf "$STATE_PREVIEW" "$STATE_LINK"
STATE_PREVIEW=""
STATE_FINAL=""
if [[ "$(readlink -f "$CURRENT_LINK")" != "$RELEASE_DIR" ]]; then
  fail "atomic current promotion" "read-back does not match candidate release" 51
fi
if ! OMEGA_GCP_ALLOW_OPERATION_MARKER=1 /usr/local/sbin/omega-operation-gate; then
  fail "atomic runtime state" "published state pair/current/helper read-back failed" 51
fi
LOCK_ENV_SHA256="$(sha256sum "$LOCK_ENV" | awk '{print $1}')"
python3 - "$DAY2_STATE" "$DEPLOY_REF" "$EXPECTED_VERSION" "$LOCK_ENV_SHA256" "$LOCK_SHA256" <<'PY'
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "state": "complete",
    "deploy_ref": sys.argv[2],
    "version": sys.argv[3],
    "image_lock_sha256": sys.argv[4],
    "image_manifest_sha256": sys.argv[5],
    "completed_at": datetime.now(timezone.utc).isoformat(),
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
date -u +%Y-%m-%dT%H:%M:%SZ > "${APP_ROOT}/DEPLOYED"
write_operation_state "promoted"
rm -f -- "$OPERATION_MARKER"
MUTATION_STARTED=0
emit "atomic current promotion" "PASS" "current=${DEPLOY_REF} previous=${OLD_REF}"
printf 'OMEGA_GCP_RELEASE_JSON={"status":"PASS","tag":"%s","deploy_ref":"%s","version":"%s","previous_ref":"%s","image_lock_sha256":"%s"}\n' \
  "$TARGET_TAG" "$DEPLOY_REF" "$EXPECTED_VERSION" "$OLD_REF" "$LOCK_SHA256"
