#!/bin/bash -p
# Recover only the exact, previously-proven GCP runtime after a host reboot.
set -Eeuo pipefail
set +x
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
unset BASH_ENV ENV CDPATH GLOBIGNORE
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE \
  PYTHONWARNINGS PYTHONBREAKPOINT PYTHONSAFEPATH
unset SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE SSLKEYLOGFILE
unset DOCKER_CONTEXT DOCKER_TLS DOCKER_TLS_VERIFY DOCKER_CERT_PATH \
  DOCKER_API_VERSION DOCKER_CONFIG DOCKER_AUTH_CONFIG COMPOSE_FILE \
  COMPOSE_PATH_SEPARATOR COMPOSE_PROFILES COMPOSE_PROJECT_NAME
export DOCKER_HOST=unix:///run/docker.sock

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: reboot-runtime.sh must run through sudo" >&2
  exit 10
fi

APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
CURRENT_LINK="${APP_ROOT}/current"
SHARED_ROOT="${APP_ROOT}/shared"
SHARED_ENV="${SHARED_ROOT}/infra.env"
GCP_RUNTIME_COMPOSE="${SHARED_ROOT}/docker-compose.gcp.yml"
OPERATION_MARKER="${SHARED_ROOT}/operation-state.json"
STATE_LINK="${SHARED_ROOT}/runtime-state"
BOOTSTRAP_STATE="${STATE_LINK}/bootstrap-state.json"
RUNTIME_PROVENANCE="${STATE_LINK}/runtime-provenance.json"
RESTART_POLICY_STATE="${SHARED_ROOT}/restart-policy-fence.json"
COMPOSE_PROJECT="${OMEGA_GCP_COMPOSE_PROJECT:-infra}"
MUTATION_STARTED=0
COMPOSE_READY=0
SAFE_IO="${OMEGA_GCP_SAFE_IO:-/usr/local/sbin/omega-safe-io}"
WATCHDOG="${OMEGA_GCP_WATCHDOG:-/usr/local/sbin/omega-operation-watchdog}"
CANONICAL_RUNTIME_CONTRACT="/usr/local/sbin/omega-runtime-contract"
METADATA_FIREWALL="/usr/local/sbin/omega-metadata-firewall"
RUNTIME_AUTH_DIR="/run/systemd/system/docker.service.d"
RUNTIME_AUTH_DROPIN="${RUNTIME_AUTH_DIR}/omega-reboot-recovery.conf"
DAY2_LOCK_DIR="/run/omega-gcp"
DAY2_LOCK="${DAY2_LOCK_DIR}/day2.lock"
TERMINAL_RECEIPT="${OMEGA_GCP_STARTUP_TERMINAL_RECEIPT:?startup terminal receipt is required}"
STARTUP_CONTRACT_SHA256="${OMEGA_GCP_STARTUP_CONTRACT_SHA256:?startup contract checksum is required}"
CONTROLLER_REF="${OMEGA_GCP_CONTROLLER_REF:?reviewed controller ref is required}"

WRITER_SERVICES=(airflow-scheduler console workspace refinement vault mcp-infra airflow replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-successfactors sap-s4hana superset)
ONE_SHOT_MUTATORS=(airflow-init minio-init postgres_dev_seed superset-init)
MUTATING_SERVICES=("${WRITER_SERVICES[@]}" "${ONE_SHOT_MUTATORS[@]}")

write_live_receipt() {
  /usr/bin/python3 -I - "${TERMINAL_RECEIPT}" "${DEPLOY_REF}" "${VERSION}" \
    "${CONTROLLER_REF}" "${STARTUP_CONTRACT_SHA256}" <<'PY'
import json, os, pathlib, re, stat, sys
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
parent = path.parent.lstat()
if (
    not str(path).startswith("/run/omega-gcp-bootstrap.")
    or path.name != "terminal-state.json"
    or stat.S_ISLNK(parent.st_mode)
    or not stat.S_ISDIR(parent.st_mode)
    or parent.st_uid != 0
    or parent.st_gid != 0
    or stat.S_IMODE(parent.st_mode) != 0o700
    or re.fullmatch(r"[0-9a-f]{40}", sys.argv[2]) is None
    or re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+(?:-[0-9A-Za-z.-]+)?", sys.argv[3]) is None
    or re.fullmatch(r"[0-9a-f]{40}", sys.argv[4]) is None
    or re.fullmatch(r"[0-9a-f]{64}", sys.argv[5]) is None
):
    raise SystemExit(1)
payload = {
    "schema_version": 1,
    "terminal_state": "live-recovered",
    "deploy_ref": sys.argv[2],
    "version": sys.argv[3],
    "helper_ref": sys.argv[4],
    "startup_contract_sha256": sys.argv[5],
    "foundation_marker_sha256": "",
    "foundation_watchdog_state_sha256": "",
    "completed_at": datetime.now(timezone.utc).isoformat(),
}
descriptor = os.open(
    path,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
try:
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    view = memoryview(raw)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise SystemExit(1)
        view = view[written:]
    os.fsync(descriptor)
finally:
    os.close(descriptor)
directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}

fail() {
  printf 'OMEGA_GCP_REBOOT_CHECK\t%s\tFAIL\t%s\n' "$1" "${2:-}" >&2
  exit "${3:-20}"
}

cleanup() {
  local rc=$?
  if [[ "$rc" -ne 0 && "$MUTATION_STARTED" == "1" ]]; then
    if OMEGA_GCP_SAFE_IO="$SAFE_IO" \
        "$WATCHDOG" fence-failure "$$" "$OPERATION_MARKER"; then
      printf 'OMEGA_GCP_REBOOT_CHECK\tfail-closed fence\tPASS\tdurable marker retained\n'
    else
      printf 'OMEGA_GCP_REBOOT_CHECK\tfail-closed fence\tFAIL\thard fence incomplete; durable marker retained\n' >&2
      rc=90
    fi
  fi
  exit "$rc"
}
trap cleanup EXIT

if [[ ! "$COMPOSE_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  fail "Compose project" "invalid project name"
fi
if [[ "$SAFE_IO" != /* || ! -x "$SAFE_IO" || "$WATCHDOG" != /* || ! -x "$WATCHDOG" || \
      ! -x "$CANONICAL_RUNTIME_CONTRACT" || ! -x "$METADATA_FIREWALL" ]]; then
  fail "host safety helpers" "safe I/O helper or watchdog is unavailable"
fi
if [[ -e "$OPERATION_MARKER" || -L "$OPERATION_MARKER" ]]; then
  fail "durable operation fence" "incomplete operation blocks reboot recovery" 75
fi
if [[ ! -L "$CURRENT_LINK" || ! -L "$STATE_LINK" || ! -s "$BOOTSTRAP_STATE" || \
      ! -s "$SHARED_ENV" || ! -s "$GCP_RUNTIME_COMPOSE" || ! -s "$RUNTIME_PROVENANCE" ]]; then
  fail "canonical reboot inputs" "current/shared runtime provenance is incomplete"
fi
RELEASE_IDENTITY="$("$CANONICAL_RUNTIME_CONTRACT" release-identity \
  --app-root "$APP_ROOT")" || \
  fail "current release confinement" "current/release/VERSION identity is unsafe"
IFS=$'\t' read -r DEPLOY_REF VERSION CURRENT_RELEASE <<<"$RELEASE_IDENTITY"
if [[ ! "$DEPLOY_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ || \
      "$CURRENT_RELEASE" != "${APP_ROOT}/releases/${DEPLOY_REF}" ]]; then
  fail "current release identity" "canonical verifier returned invalid identity"
fi
BASE_COMPOSE="${CURRENT_RELEASE}/infra/docker-compose.yml"
RUNTIME_CONTRACT="${OMEGA_GCP_RUNTIME_CONTRACT:-${CURRENT_RELEASE}/scripts/gcp/runtime_contract.py}"
case "$RUNTIME_CONTRACT" in
  "${CURRENT_RELEASE}/scripts/gcp/runtime_contract.py"|"${SHARED_ROOT}/bin/"[0-9a-f]*/runtime_contract.py|/run/omega-gcp-bootstrap.*/runtime_contract.py) ;;
  *) fail "runtime verifier confinement" "runtime verifier path is outside current/shared helpers" ;;
esac
case "$RUNTIME_CONTRACT" in
  /run/omega-gcp-bootstrap.*/runtime_contract.py)
    CONTRACT_PARENT="$(dirname "$RUNTIME_CONTRACT")"
    if [[ -L "$CONTRACT_PARENT" || ! -d "$CONTRACT_PARENT" || \
          "$(stat -c '%u:%g:%a' -- "$CONTRACT_PARENT" 2>/dev/null)" != "0:0:700" || \
          -L "$RUNTIME_CONTRACT" || \
          "$(stat -c '%u:%g:%a' -- "$RUNTIME_CONTRACT" 2>/dev/null)" != "0:0:700" ]]; then
      fail "runtime verifier confinement" "metadata candidate helper ownership differs"
    fi
    ;;
esac
if [[ ! -s "$BASE_COMPOSE" || ! -x "$RUNTIME_CONTRACT" ]]; then
  fail "current release files" "base Compose or runtime verifier missing"
fi
if ! cmp -s "$RUNTIME_CONTRACT" "$CANONICAL_RUNTIME_CONTRACT"; then
  fail "runtime verifier identity" "canonical shutdown helper differs from candidate"
fi
if grep -Eq '^(GHCR_[A-Z0-9_]*(TOKEN|PASSWORD|SECRET|CREDENTIAL|AUTH|USER)|GITHUB_TOKEN|DOCKER_AUTH_CONFIG)=' "$SHARED_ENV"; then
  fail "server-owned registry credential boundary" "registry credential found in runtime env"
fi
"$SAFE_IO" env-validate --path "$SHARED_ENV" --forbid-prefix OMEGA_MIGRATION_ >/dev/null || \
  fail "shared runtime env" "env grammar/ownership/control boundary is invalid"

if ! exec 9>>"$DAY2_LOCK"; then
  fail "exclusive day-2 lock" "private lock descriptor cannot be opened"
fi
/usr/bin/python3 -I - "$DAY2_LOCK_DIR" "$DAY2_LOCK" <<'PY' || \
  fail "exclusive day-2 lock" "private lock descriptor is unsafe"
import os
import pathlib
import stat
import sys

directory = pathlib.Path(sys.argv[1])
lock = pathlib.Path(sys.argv[2])
info = directory.lstat()
if (
    stat.S_ISLNK(info.st_mode)
    or not stat.S_ISDIR(info.st_mode)
    or info.st_uid != 0
    or info.st_gid != 0
    or stat.S_IMODE(info.st_mode) != 0o700
):
    raise SystemExit(1)
before = os.fstat(9)
named = lock.lstat()
after = os.fstat(9)
if (
    (before.st_dev, before.st_ino, before.st_mode, before.st_uid,
     before.st_gid, before.st_nlink)
    != (after.st_dev, after.st_ino, after.st_mode, after.st_uid,
        after.st_gid, after.st_nlink)
    or stat.S_ISLNK(named.st_mode)
    or not stat.S_ISREG(named.st_mode)
    or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
    or not stat.S_ISREG(before.st_mode)
    or before.st_uid != 0
    or before.st_gid != 0
    or stat.S_IMODE(before.st_mode) != 0o600
    or before.st_nlink != 1
):
    raise SystemExit(1)
PY
if ! flock -n 9; then
  fail "exclusive day-2 lock" "another canonical operation is active"
fi
/usr/bin/python3 -I - "$DAY2_LOCK" <<'PY' || \
  fail "exclusive day-2 lock" "lock pathname changed before critical section"
import os
import pathlib
import stat
import sys

named = pathlib.Path(sys.argv[1]).lstat()
descriptor = os.fstat(9)
if (
    stat.S_ISLNK(named.st_mode)
    or not stat.S_ISREG(named.st_mode)
    or (named.st_dev, named.st_ino) != (descriptor.st_dev, descriptor.st_ino)
    or descriptor.st_uid != 0
    or descriptor.st_gid != 0
    or stat.S_IMODE(descriptor.st_mode) != 0o600
    or descriptor.st_nlink != 1
):
    raise SystemExit(1)
PY

PROVENANCE_IDENTITY="$("$RUNTIME_CONTRACT" provenance-metadata \
  --provenance "$RUNTIME_PROVENANCE" --compose-project "$COMPOSE_PROJECT")" || \
  fail "runtime provenance" "canonical provenance metadata is unsafe"
IFS=$'\t' read -r PROVENANCE_SCHEMA PROVENANCE_MODE HAS_LEGACY_IMAGE_COMPOSE \
  <<<"$PROVENANCE_IDENTITY"
if [[ ! "$PROVENANCE_SCHEMA" =~ ^[12]$ || \
      ( "$PROVENANCE_MODE" != "bootstrap" && "$PROVENANCE_MODE" != "day2" ) || \
      ! "$HAS_LEGACY_IMAGE_COMPOSE" =~ ^[01]$ ]]; then
  fail "runtime provenance" "canonical provenance metadata output is malformed"
fi
PROVENANCE_ARGS=()
RUNTIME_INPUT_ARGS=(
  --runtime-input "shared_env=${SHARED_ENV}"
  --runtime-input "base_compose=${BASE_COMPOSE}"
  --runtime-input "gcp_compose=${GCP_RUNTIME_COMPOSE}"
)
if [[ "$PROVENANCE_MODE" == "day2" ]]; then
  LOCK_ENV="${SHARED_ROOT}/image-locks/${DEPLOY_REF}/release-images.env"
  RELEASE_COMPOSE="${CURRENT_RELEASE}/infra/terraform-gcp/release/docker-compose.release.yml"
  if [[ ! -s "$LOCK_ENV" || ! -s "$RELEASE_COMPOSE" ]]; then
    fail "day-2 reboot inputs" "exact image lock or release overlay missing"
  fi
  COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
    --env-file "$LOCK_ENV" -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" \
    -f "$RELEASE_COMPOSE" --profile sap)
  PROVENANCE_ARGS=(--lock-env "$LOCK_ENV")
  RUNTIME_INPUT_ARGS+=(--runtime-input "release_compose=${RELEASE_COMPOSE}")
elif [[ "$PROVENANCE_MODE" == "bootstrap" ]]; then
  LEGACY_IMAGE_COMPOSE="${SHARED_ROOT}/docker-compose.legacy-images.gcp.yml"
  if [[ "$HAS_LEGACY_IMAGE_COMPOSE" == "1" ]]; then
    if [[ ! -s "$LEGACY_IMAGE_COMPOSE" ]]; then
      fail "bootstrap reboot inputs" "recorded legacy image overlay is missing"
    fi
    COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
      -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" -f "$LEGACY_IMAGE_COMPOSE" --profile sap)
    RUNTIME_INPUT_ARGS+=(--runtime-input "legacy_image_compose=${LEGACY_IMAGE_COMPOSE}")
  else
    COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
      -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" --profile sap)
  fi
else
  fail "runtime provenance" "unsupported provenance mode"
fi
COMPOSE_READY=1
"${COMPOSE[@]}" config -q

"$SAFE_IO" docker-storage verify-mounted >/dev/null || \
  fail "Docker data storage" "canonical ext4 mount/fstab contract differs before runtime start"

"$RUNTIME_CONTRACT" restart-policy verify-fenced \
  --app-root "$APP_ROOT" --compose-project "$COMPOSE_PROJECT" \
  --state "$RESTART_POLICY_STATE" >/dev/null || \
  fail "restart-policy pre-fence" "inactive runtime lacks an exact durable policy fence"

/usr/bin/python3 -I - "$OPERATION_MARKER" "$DEPLOY_REF" <<'PY'
import json
import os
import pathlib
import stat
import sys
import tempfile
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
parent_info = path.parent.lstat()
if (
    not stat.S_ISDIR(parent_info.st_mode)
    or parent_info.st_uid != 0
    or parent_info.st_gid != 0
    or stat.S_IMODE(parent_info.st_mode) & 0o022
):
    raise SystemExit("unsafe operation marker parent")
try:
    path.lstat()
except FileNotFoundError:
    pass
else:
    raise SystemExit("operation marker already exists")
payload = {
    "schema_version": 1,
    "operation": "reboot-recovery",
    "state": "fencing",
    "deploy_ref": sys.argv[2],
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
    # Publish without replacing any marker/symlink that appears after lstat.
    os.link(temporary, path, follow_symlinks=False)
    os.unlink(temporary)
    temporary = ""
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if temporary and os.path.lexists(temporary):
        os.unlink(temporary)
PY
MUTATION_STARTED=1

# The marker is durable while the authorization is deliberately volatile. The
# watchdog must own the process before Docker can become live or revive any
# restart-policy container.
"$SAFE_IO" runtime-auth-paths >/dev/null || \
  fail "runtime authorization path" "volatile systemd drop-in parent is unsafe"
AUTH_TMP="$(mktemp "${RUNTIME_AUTH_DIR}/.omega-reboot-recovery.conf.XXXXXX")"
{
  printf '%s\n' '[Service]' \
    'Environment=OMEGA_GCP_ALLOW_OPERATION_MARKER=1' \
    'Environment=OMEGA_GCP_RUNTIME_START_AUTHORIZED=1'
  if [[ "$PROVENANCE_SCHEMA" == "1" ]]; then
    printf '%s\n' 'Environment=OMEGA_GCP_LEGACY_PROVENANCE_UPGRADE=1'
  fi
} > "$AUTH_TMP"
chmod 0644 "$AUTH_TMP"
"$SAFE_IO" fsync-file "$AUTH_TMP"
mv -Tf "$AUTH_TMP" "$RUNTIME_AUTH_DROPIN"
"$SAFE_IO" fsync-dir "$RUNTIME_AUTH_DIR"
"$WATCHDOG" arm "$$" "$OPERATION_MARKER" 1800
systemctl disable docker.service docker.socket containerd.service >/dev/null
"$SAFE_IO" docker-runtime verify-prestart >/dev/null || \
  fail "Docker daemon locality" "daemon/socket/drop-in contract differs before start"
systemctl unmask --runtime docker.service docker.socket containerd.service
systemctl daemon-reload
for service in docker.service docker.socket containerd.service; do
  unit_state="$(systemctl is-enabled "$service" 2>/dev/null || true)"
  case "$unit_state" in
    disabled|static|indirect) ;;
    *) fail "persistent runtime autostart" "${service} remains ${unit_state:-unknown}" ;;
  esac
done
systemctl start containerd.service docker.socket docker.service
systemctl is-active --quiet containerd.service || fail "container runtime" "containerd did not start"
systemctl is-active --quiet docker.socket || fail "container runtime" "Docker socket did not start"
systemctl is-active --quiet docker.service || fail "container runtime" "Docker daemon did not start"
"$SAFE_IO" docker-runtime verify-live >/dev/null || \
  fail "Docker daemon locality" "live socket or data-root contract differs"
"$METADATA_FIREWALL" verify >/dev/null || \
  fail "metadata firewall" "host metadata firewall differs after daemon start"

# Before Compose may start even a dependency, validate the exact persisted
# 26-container inventory, every image/config/ID recorded in provenance, the
# durable restart-policy normalization, and the stopped state of all writers,
# scheduler, and successful one-shots. A dependency that Docker already
# restarted is tolerated only when it is one of the canonical five and healthy.
PRESTART_GREEN=0
for _ in $(seq 1 120); do
  if "$RUNTIME_CONTRACT" prestart --provenance "$RUNTIME_PROVENANCE" \
      --compose-project "$COMPOSE_PROJECT" --deploy-ref "$DEPLOY_REF" --version "$VERSION" \
      "${RUNTIME_INPUT_ARGS[@]}" --restart-fenced-app-root "$APP_ROOT" \
      "${PROVENANCE_ARGS[@]}" >/dev/null 2>&1; then
    PRESTART_GREEN=1
    break
  fi
  sleep 1
done
if [[ "$PRESTART_GREEN" != "1" ]]; then
  fail "reboot prestart provenance" "persisted container identity/config/state drifted before Compose start"
fi

# Docker restart policy cannot be assumed to revive these dependencies: four
# use the Compose default `no`. Start only the exact dependency set first, then
# prove the global five-container writer fence before any application starts.
DEPENDENCY_SERVICES=(postgres postgres_gold redis minio mailhog)
"${COMPOSE[@]}" start "${DEPENDENCY_SERVICES[@]}" >/dev/null

WRITER_FENCE_GREEN=0
for _ in $(seq 1 120); do
  if "$RUNTIME_CONTRACT" writer-fence \
      --compose-project "$COMPOSE_PROJECT" >/dev/null 2>&1; then
    WRITER_FENCE_GREEN=1
    break
  fi
  sleep 1
done
if [[ "$WRITER_FENCE_GREEN" != "1" ]]; then
  fail "reboot writer fence" "exact five dependency containers did not become healthy with every mutator stopped"
fi

# Reuse existing containers only. A missing/replaced container is provenance
# drift and requires an explicit day-2 operation; reboot never builds or pulls.
start_exact_application_services() {
  local services_file selection service
  local -a application_services=()
  services_file="$(mktemp /run/omega-gcp-compose-services.XXXXXX)" || \
    fail "reboot service selection" "cannot allocate private service inventory"
  chmod 0600 "$services_file" || {
    rm -f -- "$services_file"
    fail "reboot service selection" "cannot protect service inventory"
  }
  if ! "${COMPOSE[@]}" config --services >"$services_file"; then
    rm -f -- "$services_file"
    fail "reboot service selection" "Compose service inventory command failed"
  fi
  selection="$("$SAFE_IO" compose-reboot-services --path "$services_file")" || {
    rm -f -- "$services_file"
    fail "reboot service selection" "Compose service inventory is not exact"
  }
  rm -f -- "$services_file"
  while IFS= read -r service; do
    application_services+=("$service")
  done <<<"$selection"
  if [[ "${#application_services[@]}" -ne 16 ]]; then
    fail "reboot service selection" "application service allowlist is empty/incomplete"
  fi
  "${COMPOSE[@]}" start -- "${application_services[@]}" >/dev/null
}

start_exact_application_services

RUNTIME_GREEN=0
LEGACY_PROVENANCE_ARGS=()
if [[ "$PROVENANCE_SCHEMA" == "1" ]]; then
  LEGACY_PROVENANCE_ARGS=(--legacy-adoption)
fi
for _ in $(seq 1 120); do
  if "$RUNTIME_CONTRACT" provenance --provenance "$RUNTIME_PROVENANCE" \
      --compose-project "$COMPOSE_PROJECT" --deploy-ref "$DEPLOY_REF" --version "$VERSION" \
      --scheduler stopped "${RUNTIME_INPUT_ARGS[@]}" \
      --restart-fenced-app-root "$APP_ROOT" \
      "${LEGACY_PROVENANCE_ARGS[@]}" \
      "${PROVENANCE_ARGS[@]}" >/dev/null 2>&1; then
    RUNTIME_GREEN=1
    break
  fi
  sleep 3
done
if [[ "$RUNTIME_GREEN" != "1" ]]; then
  fail "reboot runtime recovery" "15 exact services did not return healthy with scheduler fenced"
fi

"${COMPOSE[@]}" start airflow-scheduler >/dev/null
if [[ "$PROVENANCE_SCHEMA" == "1" ]]; then
  ADOPTION_GREEN=0
  for _ in $(seq 1 60); do
    if "$RUNTIME_CONTRACT" adopt-provenance \
        --app-root "$APP_ROOT" --compose-project "$COMPOSE_PROJECT" \
        --restart-fenced >/dev/null 2>&1; then
      ADOPTION_GREEN=1
      break
    fi
    sleep 3
  done
  if [[ "$ADOPTION_GREEN" != "1" ]]; then
    fail "runtime provenance adoption" "legacy provenance did not adopt exactly under the restart fence"
  fi
  PROVENANCE_SCHEMA=2
fi
FINAL_GREEN=0
for _ in $(seq 1 60); do
  if "$RUNTIME_CONTRACT" provenance --provenance "$RUNTIME_PROVENANCE" \
      --compose-project "$COMPOSE_PROJECT" --deploy-ref "$DEPLOY_REF" --version "$VERSION" \
      --one-shots "${RUNTIME_INPUT_ARGS[@]}" \
      --restart-fenced-app-root "$APP_ROOT" \
      "${PROVENANCE_ARGS[@]}" >/dev/null 2>&1; then
    FINAL_GREEN=1
    break
  fi
  sleep 3
done
if [[ "$FINAL_GREEN" != "1" ]]; then
  fail "reboot runtime recovery" "exact runtime/scheduler/one-shot provenance did not recover"
fi

"$METADATA_FIREWALL" verify-container >/dev/null || \
  fail "container metadata firewall" "runtime container reached host metadata"
"$RUNTIME_CONTRACT" restart-policy restore \
  --app-root "$APP_ROOT" --compose-project "$COMPOSE_PROJECT" \
  --state "$RESTART_POLICY_STATE" >/dev/null || \
  fail "restart-policy restore" "exact pre-stop policies were not restored"

rm -f -- "$RUNTIME_AUTH_DROPIN"
"$SAFE_IO" fsync-dir "$RUNTIME_AUTH_DIR"
systemctl daemon-reload
"$WATCHDOG" complete "$$" "$OPERATION_MARKER"
write_live_receipt
MUTATION_STARTED=0
printf 'OMEGA_GCP_REBOOT_CHECK\texact runtime recovery\tPASS\tref=%s version=%s mode=%s\n' \
  "$DEPLOY_REF" "$VERSION" "$PROVENANCE_MODE"
