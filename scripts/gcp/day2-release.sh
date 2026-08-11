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
MUTATION_STARTED=0
COMPOSE_RUNTIME_READY=0
WORKDIR=""
RELEASE_TMP=""
LOCK_TMP=""
SHARED_ENV_TMP=""

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
  if [[ -n "${SHARED_ENV_TMP}" && "${SHARED_ENV_TMP}" == "${SHARED_ROOT}/.infra.env.${DEPLOY_REF}."* ]]; then
    rm -f -- "$SHARED_ENV_TMP"
  fi
  if [[ "$rc" -ne 0 && "$MUTATION_STARTED" == "1" ]]; then
    if [[ "$COMPOSE_RUNTIME_READY" == "1" ]]; then
      "${COMPOSE[@]}" stop --timeout 30 "${WRITER_SERVICES[@]}" >/dev/null 2>&1 || true
    fi
    emit "fail-closed runtime fence" "PASS" "writers and scheduler remain stopped; current was not promoted"
  fi
  exit "$rc"
}
trap cleanup EXIT

fail() {
  emit "$1" "FAIL" "${2:-}"
  exit "${3:-20}"
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
CURRENT_ENV="${OLD_RELEASE}/infra/.env"
if [[ ! -s "$CURRENT_ENV" ]]; then
  fail "current runtime env" "${CURRENT_ENV} is missing" 29
fi
emit "current release captured" "PASS" "ref=${OLD_REF} version=${OLD_VERSION:-unknown}"

install -d -m 0755 "$RELEASE_ROOT" "$SHARED_ROOT" "${SHARED_ROOT}/airflow/logs" \
  "${SHARED_ROOT}/airflow/plugins" "${SHARED_ROOT}/gcp-local-minio" \
  "${SHARED_ROOT}/image-locks"

exec 9>"/var/lock/omega-gcp-day2.lock"
if ! flock -n 9; then
  fail "exclusive day-2 lock" "another release/backup operation is active" 33
fi
emit "exclusive day-2 lock" "PASS" "acquired"

if grep -Eq '^(GHCR_[A-Z0-9_]*(TOKEN|PASSWORD|SECRET|CREDENTIAL|AUTH|USER)|GITHUB_TOKEN|DOCKER_AUTH_CONFIG)=' "$CURRENT_ENV"; then
  fail "server-owned registry credential boundary" "registry credential found in runtime env" 32
fi
SHARED_ENV_TMP="${SHARED_ROOT}/.infra.env.${DEPLOY_REF}.$$"
install -m 0600 "$CURRENT_ENV" "$SHARED_ENV_TMP"
mv -Tf "$SHARED_ENV_TMP" "$SHARED_ENV"
SHARED_ENV_TMP=""
emit "shared runtime env" "PASS" "atomically refreshed from current release without registry credentials"

if [[ ! -s "${OLD_RELEASE}/infra/docker-compose.gcp.yml" ]]; then
  fail "GCP runtime Compose" "current generated override does not exist" 32
fi
GCP_COMPOSE_TMP="${SHARED_ROOT}/.docker-compose.gcp.${DEPLOY_REF}.$$"
install -m 0600 "${OLD_RELEASE}/infra/docker-compose.gcp.yml" "$GCP_COMPOSE_TMP"
mv -Tf "$GCP_COMPOSE_TMP" "$GCP_RUNTIME_COMPOSE"

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
if manifest.get("schema_version") != 1 or manifest.get("complete") is not True:
    raise SystemExit("backup manifest is not complete schema v1")
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
RELEASE_COMPOSE="${RELEASE_DIR}/infra/terraform-gcp/deploy/docker-compose.release.yml"
if [[ ! -s "$BASE_COMPOSE" || ! -s "$RELEASE_COMPOSE" ]]; then
  fail "release Compose files" "candidate Compose inputs missing" 38
fi

CANDIDATE_AUTH_RUNNER="${RELEASE_DIR}/infra/terraform-gcp/release/ghcr-auth-run.sh"
if [[ ! -x "$CANDIDATE_AUTH_RUNNER" ]]; then
  fail "candidate GHCR auth runner" "release does not contain the audited helper" 38
fi
install -d -m 0700 "$(dirname "$AUTH_RUNNER")"
AUTH_RUNNER_TMP="$(dirname "$AUTH_RUNNER")/.ghcr-auth-run.${DEPLOY_REF}.$$"
install -m 0700 "$CANDIDATE_AUTH_RUNNER" "$AUTH_RUNNER_TMP"
mv -Tf "$AUTH_RUNNER_TMP" "$AUTH_RUNNER"
emit "server-owned GHCR auth runner" "PASS" "installed from exact release artifact"

COMPOSE_TAGGED=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
  -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" -f "$RELEASE_COMPOSE" --profile sap)
export IMAGE_TAG="$TARGET_TAG" GHCR_OWNER
"${COMPOSE_TAGGED[@]}" config -q

RELEASE_SERVICES=(console workspace refinement vault mcp-infra airflow replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-successfactors sap-s4hana)
RELEASE_IMAGES=(console workspace refinement vault mcp-infra airflow replicon hubspot salesforce banxico inegi sec_edgar sap_hcm sap_successfactors sap_s4hana)
if [[ "${#RELEASE_SERVICES[@]}" -ne 15 || "${#RELEASE_IMAGES[@]}" -ne 15 ]]; then
  fail "release image inventory" "expected exactly 15 services and images" 39
fi
AVAILABLE_SERVICES="$("${COMPOSE_TAGGED[@]}" config --services)"
for service in "${RELEASE_SERVICES[@]}"; do
  if ! grep -qx "$service" <<<"$AVAILABLE_SERVICES"; then
    fail "release image inventory" "missing service=${service}" 40
  fi
done

if ! OMEGA_GCP_ENVIRONMENT="$GCP_ENVIRONMENT" \
  OMEGA_GHCR_PULL_SECRET_VERSION="$GHCR_SECRET_VERSION" \
  "$AUTH_RUNNER" "${COMPOSE_TAGGED[@]}" pull --quiet "${RELEASE_SERVICES[@]}" >/dev/null 2>&1; then
  fail "private GHCR release pull" "less than 15/15 images pullable; credential output suppressed" 41
fi
emit "private GHCR release pull" "PASS" "15/15 tag=${TARGET_TAG}"

LOCK_COMPOSE="${LOCK_DIR}/docker-compose.image-lock.yml"
LOCK_MANIFEST="${LOCK_DIR}/manifest.json"
LOCK_TMP="${SHARED_ROOT}/image-locks/.${DEPLOY_REF}.tmp.$$"
install -d -m 0700 "$LOCK_TMP"
NEW_LOCK_COMPOSE="${LOCK_TMP}/docker-compose.image-lock.yml"
NEW_LOCK_MANIFEST="${LOCK_TMP}/manifest.json"
python3 - "$GHCR_OWNER" "$TARGET_TAG" "$NEW_LOCK_COMPOSE" "$NEW_LOCK_MANIFEST" <<'PY'
import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone

owner, tag, compose_path, manifest_path = sys.argv[1:]
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
unique_images = sorted(set(service_images.values()))
if len(unique_images) != 15:
    raise SystemExit("release lock does not contain exactly 15 unique images")
resolved = {}
for image in unique_images:
    repository = f"ghcr.io/{owner}/{image}"
    result = subprocess.run(
        ["docker", "image", "inspect", f"{repository}:{tag}", "--format", "{{json .RepoDigests}}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    digests = json.loads(result.stdout)
    matches = sorted(value for value in digests if value.startswith(repository + "@sha256:"))
    if len(matches) != 1 or not re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", matches[0]):
        raise SystemExit(f"could not resolve one exact RepoDigest for {repository}")
    resolved[image] = matches[0]

lines = ["services:"]
for service, image in service_images.items():
    lines.extend([f"  {service}:", f"    image: {resolved[image]}"])
pathlib.Path(compose_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
payload = {
    "schema_version": 1,
    "tag": tag,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "unique_image_count": len(resolved),
    "images": resolved,
}
pathlib.Path(manifest_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
chmod a-w "$NEW_LOCK_COMPOSE" "$NEW_LOCK_MANIFEST"
if [[ -d "$LOCK_DIR" ]]; then
  if [[ ! -s "$LOCK_COMPOSE" || ! -s "$LOCK_MANIFEST" ]]; then
    fail "immutable image lock" "existing lock is incomplete" 42
  fi
  python3 - "$LOCK_MANIFEST" "$NEW_LOCK_MANIFEST" <<'PY'
import json
import sys

existing = json.load(open(sys.argv[1], encoding="utf-8"))
candidate = json.load(open(sys.argv[2], encoding="utf-8"))
for key in ("schema_version", "tag", "unique_image_count", "images"):
    if existing.get(key) != candidate.get(key):
        raise SystemExit(f"immutable image lock mismatch: {key}")
PY
  rm -rf -- "$LOCK_TMP"
  LOCK_TMP=""
  emit "immutable image digest lock" "PASS" "existing 15/15 lock revalidated against fresh pulls"
else
  mv "$LOCK_TMP" "$LOCK_DIR"
  LOCK_TMP=""
fi
LOCK_SHA256="$(sha256sum "$LOCK_MANIFEST" | awk '{print $1}')"
emit "immutable image digest lock" "PASS" "15/15 manifest_sha256=${LOCK_SHA256}"

COMPOSE=("${COMPOSE_TAGGED[@]}" -f "$LOCK_COMPOSE")
COMPOSE_RUNTIME_READY=1
"${COMPOSE[@]}" config -q

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

WRITER_SERVICES=(airflow-scheduler console workspace refinement vault mcp-infra airflow replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-successfactors sap-s4hana superset)
MUTATION_STARTED=1
"${COMPOSE[@]}" stop --timeout 60 "${WRITER_SERVICES[@]}"
for service in "${WRITER_SERVICES[@]}"; do
  if docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
      --filter "label=com.docker.compose.service=${service}" | grep -q .; then
    fail "writer fence" "service still running=${service}" 44
  fi
done
emit "writer and scheduler fence" "PASS" "all application writers stopped"

"${COMPOSE[@]}" up -d --no-build --no-deps --force-recreate postgres postgres_gold
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
  bash "${RELEASE_DIR}/scripts/apply_db_migrations.sh"
emit "canonical database migrations" "PASS" "operational and Gold runner completed"

readarray -t SERVICES_WITHOUT_SCHEDULER < <("${COMPOSE[@]}" config --services | grep -vx airflow-scheduler)
"${COMPOSE[@]}" up -d --no-build "${SERVICES_WITHOUT_SCHEDULER[@]}"

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

"${COMPOSE[@]}" up -d --no-build airflow-scheduler
SCHEDULER_IDS="$(docker ps -q --filter "label=com.docker.compose.service=airflow-scheduler")"
SCHEDULER_COUNT="$(grep -c . <<<"$SCHEDULER_IDS" || true)"
if [[ "$SCHEDULER_COUNT" != "1" ]]; then
  "${COMPOSE[@]}" stop --timeout 30 airflow-scheduler || true
  fail "canonical scheduler cardinality" "running schedulers=${SCHEDULER_COUNT}" 49
fi
SCHEDULER_PROJECT="$(docker inspect "$SCHEDULER_IDS" --format '{{index .Config.Labels "com.docker.compose.project"}}')"
if [[ "$SCHEDULER_PROJECT" != "$COMPOSE_PROJECT" ]]; then
  "${COMPOSE[@]}" stop --timeout 30 airflow-scheduler || true
  fail "canonical scheduler project" "scheduler belongs to project=${SCHEDULER_PROJECT}" 49
fi
SCHEDULER_REF="$(docker inspect "$SCHEDULER_IDS" --format '{{.Config.Image}}')"
if [[ ! "$SCHEDULER_REF" =~ ^ghcr\.io/${GHCR_OWNER}/airflow@sha256:[0-9a-f]{64}$ ]]; then
  "${COMPOSE[@]}" stop --timeout 30 airflow-scheduler || true
  fail "canonical scheduler image" "scheduler is not pinned to the release Airflow digest" 49
fi
SCHEDULER_HEALTHY=0
for _ in $(seq 1 60); do
  if [[ "$(docker inspect "$SCHEDULER_IDS" --format '{{.State.Health.Status}}' 2>/dev/null || true)" == "healthy" ]]; then
    SCHEDULER_HEALTHY=1
    break
  fi
  sleep 3
done
if [[ "$SCHEDULER_HEALTHY" != "1" ]]; then
  "${COMPOSE[@]}" stop --timeout 30 airflow-scheduler || true
  fail "canonical scheduler health" "the sole scheduler did not become healthy" 49
fi
emit "canonical scheduler cardinality" "PASS" "exactly one scheduler"

for service in "${RELEASE_SERVICES[@]}"; do
  container_id="$("${COMPOSE[@]}" ps -aq "$service" | head -n 1)"
  if [[ -z "$container_id" ]]; then
    fail "running image digest" "missing container service=${service}" 50
  fi
  configured_ref="$(docker inspect "$container_id" --format '{{.Config.Image}}')"
  if [[ ! "$configured_ref" =~ ^ghcr\.io/${GHCR_OWNER}/[a-z0-9_-]+@sha256:[0-9a-f]{64}$ ]]; then
    fail "running image digest" "service=${service} is not digest pinned" 51
  fi
done
emit "running image digests" "PASS" "15/15 services use exact RepoDigests"

PREVIOUS_TMP="${APP_ROOT}/.previous.${DEPLOY_REF}"
CURRENT_TMP="${APP_ROOT}/.current.${DEPLOY_REF}"
ln -s "$OLD_RELEASE" "$PREVIOUS_TMP"
mv -Tf "$PREVIOUS_TMP" "$PREVIOUS_LINK"
ln -s "$RELEASE_DIR" "$CURRENT_TMP"
mv -Tf "$CURRENT_TMP" "$CURRENT_LINK"
date -u +%Y-%m-%dT%H:%M:%SZ > "${APP_ROOT}/DEPLOYED"
MUTATION_STARTED=0
emit "atomic current promotion" "PASS" "current=${DEPLOY_REF} previous=${OLD_REF}"
printf 'OMEGA_GCP_RELEASE_JSON={"status":"PASS","tag":"%s","deploy_ref":"%s","version":"%s","previous_ref":"%s","image_lock_sha256":"%s"}\n' \
  "$TARGET_TAG" "$DEPLOY_REF" "$EXPECTED_VERSION" "$OLD_REF" "$LOCK_SHA256"
