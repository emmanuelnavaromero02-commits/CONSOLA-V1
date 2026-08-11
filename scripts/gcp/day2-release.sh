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
ARTIFACT_GENERATION="${4:-}"
ARTIFACT_SIZE_BYTES="${5:-}"
ARTIFACT_SHA256="${6:-}"
EXPECTED_VERSION="${7:-}"
BACKUP_MANIFEST_URI="${8:-}"
BACKUP_MANIFEST_GENERATION="${9:-}"
BACKUP_MANIFEST_SIZE_BYTES="${10:-}"
BACKUP_MANIFEST_SHA256="${11:-}"
GHCR_OWNER="${12:-emmanuelnavaromero02-commits}"
COMPOSE_PROJECT="${13:-infra}"
GCP_ENVIRONMENT="${14:-}"
GHCR_SECRET_VERSION="${15:-}"
BACKUP_POLICY_SHA256="${16:-}"
PROJECT_ID="${17:-}"
PUBLISHED_MANIFEST_SHA256="${18:-}"
PUBLISHED_TAG_OBJECT_SHA="${19:-}"

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
PREDEPLOY_ATTESTATION="${SHARED_ROOT}/predeploy-backup.json"
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
SAFE_IO="${OMEGA_GCP_SAFE_IO:-}"
WATCHDOG="/usr/local/sbin/omega-operation-watchdog"
DB_FENCE_ACTIVE=0
DB_CONN_LIMIT_MODECISSIONS=""
DB_CONN_LIMIT_GOLD=""
DB_READONLY_MODECISSIONS=""
DB_READONLY_GOLD=""
DB_READONLY_CONFIG_MODECISSIONS=""
DB_READONLY_CONFIG_GOLD=""
RECONCILIATION_HANDOFF=0
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
  local rc=$? safe=1
  trap - EXIT
  set +e
  # Safety recovery precedes best-effort temporary-file cleanup. A cleanup I/O
  # error must never bypass the writer/DB fence.
  if [[ "$rc" -ne 0 && "$MUTATION_STARTED" == "1" ]]; then
    if ! write_operation_state "failed"; then
      safe=0
    fi
    if [[ "$COMPOSE_RUNTIME_READY" != "1" ]] || \
        ! "${COMPOSE[@]}" stop --timeout 30 "${MUTATING_SERVICES[@]}" >/dev/null 2>&1; then
      safe=0
    fi
    if ! python3 "$RUNTIME_CONTRACT" writer-fence \
        --compose-project "$COMPOSE_PROJECT" >/dev/null 2>&1; then
      safe=0
    fi
    DB_FENCE_ACTIVE=1
    if ! database_fence on >/dev/null 2>&1; then
      safe=0
    fi
    if [[ "$safe" == "1" ]]; then
      emit "fail-closed runtime fence" "PASS" "writers, scheduler, and one-shot mutators stopped; both databases persistently read-only; durable marker retained"
    else
      emit "fail-closed runtime fence" "FAIL" "manual recovery required; one or more stop/fence/marker checks failed"
      rc=90
    fi
  fi
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
  if [[ -n "${STATE_FINAL}" && "${STATE_FINAL}" == "${STATE_BUNDLES_ROOT}/day2-"* && \
        "$(readlink -f "$STATE_LINK" 2>/dev/null)" != "$(readlink -f "$STATE_FINAL" 2>/dev/null)" ]]; then
    rm -rf -- "${STATE_FINAL}"
  fi
  if [[ -n "${CURRENT_PREVIEW}" && "${CURRENT_PREVIEW}" == "${APP_ROOT}/.candidate-current."* ]]; then
    rm -f -- "${CURRENT_PREVIEW}"
  fi
  exit "$rc"
}
trap cleanup EXIT

fail() {
  emit "$1" "FAIL" "${2:-}"
  exit "${3:-20}"
}

validate_managed_directory() {
  local path="$1" required="${2:-required}"
  python3 - "$path" "$required" <<'PY'
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
required = sys.argv[2] == "required"
if not path.is_absolute():
    raise SystemExit("managed path must be absolute")
if not os.path.lexists(path):
    if required:
        raise SystemExit("managed directory is missing")
    raise SystemExit(0)
info = path.lstat()
if (
    not stat.S_ISDIR(info.st_mode)
    or info.st_uid != 0
    or info.st_gid != 0
    or stat.S_IMODE(info.st_mode) & 0o022
    or path.resolve(strict=True) != pathlib.Path(os.path.abspath(path))
):
    raise SystemExit("managed directory is linked, escaped, unowned, or writable")
PY
}

install_operation_guard() {
  local source="$1" guard_tmp dropin_tmp
  if [[ ! -x "$source" ]]; then
    fail "reboot operation guard" "exact candidate operation gate is missing" 38
  fi
  install -d -m 0755 /usr/local/sbin /etc/systemd/system/docker.service.d
  guard_tmp="$(mktemp /usr/local/sbin/.omega-operation-gate.XXXXXX)"
  dropin_tmp="$(mktemp /etc/systemd/system/docker.service.d/.omega-operation-gate.conf.XXXXXX)"
  install -m 0755 "$source" "$guard_tmp"
  printf '%s\n' \
    '[Service]' \
    'ExecStartPre=/usr/local/sbin/omega-operation-gate' > "$dropin_tmp"
  chmod 0644 "$dropin_tmp"
  "$SAFE_IO" fsync-file "$guard_tmp" "$dropin_tmp"
  mv -Tf "$dropin_tmp" /etc/systemd/system/docker.service.d/omega-operation-gate.conf
  "$SAFE_IO" fsync-dir /etc/systemd/system/docker.service.d
  mv -Tf "$guard_tmp" /usr/local/sbin/omega-operation-gate
  "$SAFE_IO" fsync-dir /usr/local/sbin
  systemctl daemon-reload
}

install_operation_watchdog() {
  local source="$1" temporary
  if [[ ! -x "$source" ]]; then
    fail "operation watchdog" "exact candidate watchdog is missing" 38
  fi
  install -d -m 0755 "$(dirname "$WATCHDOG")"
  temporary="$(mktemp "$(dirname "$WATCHDOG")/.omega-operation-watchdog.XXXXXX")"
  install -m 0755 "$source" "$temporary"
  "$SAFE_IO" fsync-file "$temporary"
  mv -Tf "$temporary" "$WATCHDOG"
  "$SAFE_IO" fsync-dir "$(dirname "$WATCHDOG")"
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
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

consume_predeploy_attestation() {
  python3 - "$PREDEPLOY_ATTESTATION" "$DEPLOY_REF" "$OLD_REF" \
    "$BACKUP_MANIFEST_URI" "$BACKUP_MANIFEST_GENERATION" \
    "$BACKUP_MANIFEST_SIZE_BYTES" "$BACKUP_MANIFEST_SHA256" <<'PY'
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

path = pathlib.Path(sys.argv[1])
payload = json.load(open(path, encoding="utf-8"))
if (
    payload.get("state") != "ready"
    or payload.get("candidate_ref") != sys.argv[2]
    or payload.get("source_ref") != sys.argv[3]
    or payload.get("manifest_uri") != sys.argv[4]
    or payload.get("manifest_generation") != sys.argv[5]
    or payload.get("manifest_size_bytes") != int(sys.argv[6])
    or payload.get("manifest_sha256") != sys.argv[7]
):
    raise SystemExit("pre-deploy backup attestation changed before consumption")
attested = datetime.fromisoformat(payload["attested_at"].replace("Z", "+00:00"))
now = datetime.now(timezone.utc)
if (
    attested.tzinfo is None
    or now - attested > timedelta(minutes=30)
    or attested > now
):
    raise SystemExit("pre-deploy backup attestation expired before consumption")
payload["state"] = "consumed"
payload["consumed_at"] = now.isoformat()
descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

database_fence() {
  local action="$1" setting database container port limit_var readonly_var config_var
  local prior_limit prior_readonly prior_config readonly_sql superuser_count
  local remaining_sessions app_role role_override_count config_readback
  local rc=0
  [[ "$action" == "on" || "$action" == "off" ]] || return 2
  for spec in "mode_postgres:5432:modecissions" "mode_postgres_gold:5433:modecissions_gold"; do
    IFS=: read -r container port database <<<"$spec"
    if [[ "$database" == "modecissions" ]]; then
      limit_var="DB_CONN_LIMIT_MODECISSIONS"
      readonly_var="DB_READONLY_MODECISSIONS"
      config_var="DB_READONLY_CONFIG_MODECISSIONS"
    else
      limit_var="DB_CONN_LIMIT_GOLD"
      readonly_var="DB_READONLY_GOLD"
      config_var="DB_READONLY_CONFIG_GOLD"
    fi
    if [[ "$action" == "on" ]]; then
      prior_limit="${!limit_var}"
      prior_readonly="${!readonly_var}"
      prior_config="${!config_var}"
      if [[ -z "$prior_limit" ]]; then
        prior_limit="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
          -U postgres -d postgres -p "$port" \
          -c "SELECT datconnlimit FROM pg_database WHERE datname = '${database}';" | tr -d '\r')" || rc=1
        if [[ ! "$prior_limit" =~ ^-1$|^[0-9]+$ ]]; then
          rc=1
          continue
        fi
        printf -v "$limit_var" '%s' "$prior_limit"
      fi
      if [[ -z "$prior_readonly" ]]; then
        prior_readonly="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
          -U postgres -d "$database" -p "$port" \
          -c 'SHOW default_transaction_read_only;' | tr -d '\r')" || rc=1
        if [[ "$prior_readonly" != "on" && "$prior_readonly" != "off" ]]; then
          rc=1
          continue
        fi
        printf -v "$readonly_var" '%s' "$prior_readonly"
      fi
      if [[ -z "$prior_config" ]]; then
        prior_config="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
          -U postgres -d postgres -p "$port" \
          -c "SELECT COALESCE((SELECT split_part(setting, '=', 2) FROM pg_db_role_setting s CROSS JOIN LATERAL unnest(s.setconfig) setting WHERE s.setdatabase = (SELECT oid FROM pg_database WHERE datname = '${database}') AND s.setrole = 0 AND setting LIKE 'default_transaction_read_only=%'), 'absent');" | tr -d '\r')" || rc=1
        if [[ "$prior_config" != "absent" && "$prior_config" != "on" && "$prior_config" != "off" ]]; then
          rc=1
          continue
        fi
        printf -v "$config_var" '%s' "$prior_config"
      fi
      role_override_count="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
        -U postgres -d postgres -p "$port" \
        -c "SELECT count(*) FROM pg_db_role_setting s CROSS JOIN LATERAL unnest(s.setconfig) setting WHERE s.setrole = (SELECT oid FROM pg_roles WHERE rolname = 'postgres') AND s.setdatabase IN (0, (SELECT oid FROM pg_database WHERE datname = '${database}')) AND setting LIKE 'default_transaction_read_only=%';" | tr -d '\r')" || rc=1
      if [[ "$role_override_count" != "0" ]]; then
        rc=1
        continue
      fi
      superuser_count="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
        -U postgres -d postgres -p "$port" \
        -c "SELECT count(*) FROM pg_roles WHERE rolcanlogin AND rolsuper AND rolname <> 'postgres';" | tr -d '\r')" || rc=1
      if [[ "$superuser_count" != "0" ]]; then
        rc=1
        continue
      fi
      if ! docker exec "$container" psql -v ON_ERROR_STOP=1 -At -U postgres -d postgres -p "$port" \
          -c "ALTER DATABASE ${database} CONNECTION LIMIT 0; ALTER DATABASE ${database} SET default_transaction_read_only = on;" >/dev/null; then
        rc=1
      fi
      if ! docker exec "$container" psql -v ON_ERROR_STOP=1 -At -U postgres -d postgres -p "$port" \
          -c "SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a JOIN pg_roles r ON r.rolname = a.usename WHERE a.datname = '${database}' AND a.pid <> pg_backend_pid() AND NOT r.rolsuper;" >/dev/null; then
        rc=1
      fi
      if ! setting="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At -U postgres -d "$database" -p "$port" -c 'SHOW default_transaction_read_only;' | tr -d '\r')"; then
        rc=1
      elif [[ "$setting" != "on" ]]; then
        rc=1
      fi
      setting="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
        -U postgres -d postgres -p "$port" \
        -c "SELECT datconnlimit FROM pg_database WHERE datname = '${database}';" | tr -d '\r')" || rc=1
      [[ "$setting" == "0" ]] || rc=1
      remaining_sessions="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
        -U postgres -d postgres -p "$port" \
        -c "SELECT count(*) FROM pg_stat_activity a JOIN pg_roles r ON r.rolname = a.usename WHERE a.datname = '${database}' AND NOT r.rolsuper;" | tr -d '\r')" || rc=1
      [[ "$remaining_sessions" == "0" ]] || rc=1
      app_role="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
        -U postgres -d postgres -p "$port" \
        -c "SELECT rolname FROM pg_roles WHERE rolcanlogin AND NOT rolsuper AND has_database_privilege(rolname, '${database}', 'CONNECT') ORDER BY rolname LIMIT 1;" | tr -d '\r')" || rc=1
      if [[ -n "$app_role" ]] && docker exec -e PGCONNECT_TIMEOUT=3 "$container" \
          psql -v ON_ERROR_STOP=1 -At -U "$app_role" -d "$database" -p "$port" \
          -c 'SET default_transaction_read_only=off; CREATE TEMP TABLE omega_fence_probe(id integer);' \
          >/dev/null 2>&1; then
        rc=1
      fi
    else
      prior_limit="${!limit_var}"
      prior_readonly="${!readonly_var}"
      prior_config="${!config_var}"
      if [[ ! "$prior_limit" =~ ^-1$|^[0-9]+$ || \
            ( "$prior_readonly" != "on" && "$prior_readonly" != "off" ) || \
            ( "$prior_config" != "absent" && "$prior_config" != "on" && "$prior_config" != "off" ) ]]; then
        rc=1
        continue
      fi
      if [[ "$prior_config" == "absent" ]]; then
        readonly_sql="RESET default_transaction_read_only"
      elif [[ "$prior_config" == "on" ]]; then
        readonly_sql="SET default_transaction_read_only = on"
      else
        readonly_sql="SET default_transaction_read_only = off"
      fi
      if ! docker exec "$container" psql -v ON_ERROR_STOP=1 -At -U postgres -d postgres -p "$port" \
          -c "ALTER DATABASE ${database} CONNECTION LIMIT ${prior_limit}; ALTER DATABASE ${database} ${readonly_sql};" >/dev/null; then
        rc=1
      fi
      if ! setting="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At -U postgres -d "$database" -p "$port" -c 'SHOW default_transaction_read_only;' | tr -d '\r')"; then
        rc=1
      elif [[ "$setting" != "$prior_readonly" ]]; then
        rc=1
      fi
      setting="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
        -U postgres -d postgres -p "$port" \
        -c "SELECT datconnlimit FROM pg_database WHERE datname = '${database}';" | tr -d '\r')" || rc=1
      [[ "$setting" == "$prior_limit" ]] || rc=1
      config_readback="$(docker exec "$container" psql -v ON_ERROR_STOP=1 -At \
        -U postgres -d postgres -p "$port" \
        -c "SELECT COALESCE((SELECT split_part(setting, '=', 2) FROM pg_db_role_setting s CROSS JOIN LATERAL unnest(s.setconfig) setting WHERE s.setdatabase = (SELECT oid FROM pg_database WHERE datname = '${database}') AND s.setrole = 0 AND setting LIKE 'default_transaction_read_only=%'), 'absent');" | tr -d '\r')" || rc=1
      [[ "$config_readback" == "$prior_config" ]] || rc=1
    fi
  done
  return "$rc"
}

if [[ ! "$TARGET_TAG" =~ ^v[0-9][0-9A-Za-z._-]*$ || "$TARGET_TAG" == *latest* ]]; then
  fail "immutable release tag" "invalid tag" 20
fi
if [[ ! "$DEPLOY_REF" =~ ^[0-9a-f]{40}$ ]]; then
  fail "exact deploy ref" "must be a full lowercase SHA" 21
fi
if [[ ! "$ARTIFACT_GENERATION" =~ ^[1-9][0-9]*$ || \
      ! "$ARTIFACT_SIZE_BYTES" =~ ^[1-9][0-9]*$ || \
      ! "$ARTIFACT_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "artifact checksum input" "invalid sha256" 22
fi
if [[ ! "$BACKUP_MANIFEST_GENERATION" =~ ^[1-9][0-9]*$ || \
      ! "$BACKUP_MANIFEST_SIZE_BYTES" =~ ^[1-9][0-9]*$ || \
      ! "$BACKUP_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "backup checksum input" "invalid sha256" 23
fi
if [[ "$SAFE_IO" != /* || ! -x "$SAFE_IO" ]]; then
  fail "safe I/O helper" "controller-owned helper is unavailable" 23
fi
if [[ ! "$ARTIFACT_URI" =~ ^gs://[^/]+/deploy-artifacts/${DEPLOY_REF}/repo\.tar\.gz$ ]]; then
  fail "immutable artifact URI" "URI is not bound to DEPLOY_REF" 24
fi
if [[ ! "$BACKUP_MANIFEST_URI" =~ ^gs://[^/]+/_omega_backups/[^/]+/manifest\.json$ ]]; then
  fail "pre-deploy backup URI" "unexpected backup manifest URI" 25
fi
if [[ "$GHCR_OWNER" != "emmanuelnavaromero02-commits" ]]; then
  fail "GHCR owner" "owner differs from the canonical private namespace" 26
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
if [[ ! "$BACKUP_POLICY_SHA256" =~ ^[0-9a-f]{64}$ || \
      ! "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  fail "release backup policy" "controller policy attestation is invalid" 27
fi
if [[ ! "$PUBLISHED_MANIFEST_SHA256" =~ ^sha256:[0-9a-f]{64}$ || \
      ! "$PUBLISHED_TAG_OBJECT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  fail "published image authority" "controller-verified annotated tag binding is required" 27
fi
for managed in "$APP_ROOT" "$RELEASE_ROOT" "$SHARED_ROOT"; do
  validate_managed_directory "$managed" required || \
    fail "managed host path" "${managed} is linked, escaped, unowned, or writable" 28
done
for managed in "${SHARED_ROOT}/image-locks" "$DEPLOYMENT_ROOT" "$STATE_BUNDLES_ROOT"; do
  validate_managed_directory "$managed" optional || \
    fail "managed host path" "${managed} is linked, escaped, unowned, or writable" 28
done
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

install -d -m 0755 "$RELEASE_ROOT" "$SHARED_ROOT" "${SHARED_ROOT}/airflow/dags" \
  "${SHARED_ROOT}/airflow/logs" \
  "${SHARED_ROOT}/airflow/plugins" "${SHARED_ROOT}/gcp-local-minio" \
  "${SHARED_ROOT}/image-locks" "$DEPLOYMENT_ROOT"
install -d -m 0700 "$STATE_BUNDLES_ROOT"
for managed in "$APP_ROOT" "$RELEASE_ROOT" "$SHARED_ROOT" \
    "${SHARED_ROOT}/image-locks" "$DEPLOYMENT_ROOT" "$STATE_BUNDLES_ROOT"; do
  validate_managed_directory "$managed" required || \
    fail "managed host path" "${managed} failed post-create ownership/confinement checks" 28
done

exec 9>"/var/lock/omega-gcp-day2.lock"
if ! flock -n 9; then
  fail "exclusive day-2 lock" "another release/backup operation is active" 33
fi
emit "exclusive day-2 lock" "PASS" "acquired"

if [[ -e "$OPERATION_MARKER" || -L "$OPERATION_MARKER" ]]; then
  if ! python3 - "$OPERATION_MARKER" "$OLD_REF" "$DEPLOY_REF" "$TARGET_TAG" \
      "$BACKUP_MANIFEST_URI" "$BACKUP_MANIFEST_SHA256" "$SHARED_ROOT" \
      "$PUBLISHED_MANIFEST_SHA256" "$PUBLISHED_TAG_OBJECT_SHA" <<'PY'
import hashlib
import json
import pathlib
import stat
import sys
from datetime import datetime, timezone

marker_path = pathlib.Path(sys.argv[1])
info = marker_path.lstat()
if (
    not stat.S_ISREG(info.st_mode)
    or info.st_uid != 0
    or info.st_gid != 0
    or stat.S_IMODE(info.st_mode) != 0o600
    or info.st_nlink != 1
):
    raise SystemExit("handoff marker ownership differs")
marker = json.load(open(marker_path, encoding="utf-8"))
published_preflight = marker.get("published_release_preflight")
if (
    marker.get("schema_version") != 2
    or marker.get("operation") != "pipeline-run-reconciliation"
    or marker.get("state") != "handoff-ready"
    or marker.get("current_ref") != sys.argv[2]
    or marker.get("candidate_ref") != sys.argv[3]
    or marker.get("published_release_tag") != sys.argv[4]
    or marker.get("backup_manifest", {}).get("uri") != sys.argv[5]
    or marker.get("backup_manifest", {}).get("sha256") != sys.argv[6]
    or not isinstance(published_preflight, dict)
    or published_preflight.get("release_candidate_manifest_digest") != sys.argv[8]
    or published_preflight.get("release_tag_object_sha") != sys.argv[9]
):
    raise SystemExit("handoff identity differs")
expires = datetime.fromisoformat(str(marker.get("expires_at", "")).replace("Z", "+00:00"))
if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
    raise SystemExit("handoff expired")
receipt_identity = marker.get("receipt")
if not isinstance(receipt_identity, dict) or set(receipt_identity) != {"path", "sha256"}:
    raise SystemExit("handoff receipt identity is invalid")
receipt_path = pathlib.Path(receipt_identity["path"])
receipt_root = (pathlib.Path(sys.argv[7]) / "reconciliation-receipts").resolve()
if receipt_path.resolve().parent != receipt_root:
    raise SystemExit("handoff receipt escaped its server-owned directory")
receipt_info = receipt_path.lstat()
raw = receipt_path.read_bytes()
if (
    not stat.S_ISREG(receipt_info.st_mode)
    or receipt_info.st_uid != 0
    or receipt_info.st_gid != 0
    or stat.S_IMODE(receipt_info.st_mode) != 0o600
    or receipt_info.st_nlink != 1
    or hashlib.sha256(raw).hexdigest() != receipt_identity["sha256"]
):
    raise SystemExit("handoff receipt bytes or ownership differ")
receipt = json.loads(raw)
if (
    receipt.get("status") != "PASS"
    or receipt.get("current_ref") != sys.argv[2]
    or receipt.get("candidate_ref") != sys.argv[3]
    or receipt.get("published_release_tag") != sys.argv[4]
    or receipt.get("backup_manifest") != marker.get("backup_manifest")
    or receipt.get("manifest") != marker.get("manifest")
    or receipt.get("external_routing_scheduler_attestation")
       != marker.get("external_routing_scheduler_attestation")
    or receipt.get("published_release_preflight")
       != marker.get("published_release_preflight")
    or receipt.get("dry_run") != "would_apply"
    or receipt.get("apply") != "applied"
    or receipt.get("poststate") != "already_applied"
    or receipt.get("gcp_host_local_writer_fence") != "held"
    or receipt.get("checkpoint_zero_aws_writer_gate") != "PASS"
    or receipt.get("pre_release_runtime_restart_attempted") is not False
):
    raise SystemExit("handoff receipt does not authorize deploy")
PY
  then
    fail "reconciliation handoff" "marker is expired, mismatched, or not deploy-authorizing" 32
  fi
  if [[ ! -x "$WATCHDOG" ]] || ! "$WATCHDOG" arm "$$" "$OPERATION_MARKER"; then
    fail "reconciliation handoff" "continuous watchdog ownership could not transfer" 32
  fi
  RECONCILIATION_HANDOFF=1
  emit "reconciliation handoff" "PASS" \
    "published-release/backup/receipt-bound writer fence adopted without restarting the old runtime"
fi
if [[ ! -s "$SHARED_ENV" || ! -s "$GCP_RUNTIME_COMPOSE" || \
      ! -s "$BOOTSTRAP_STATE" || ! -s "$RUNTIME_PROVENANCE" ]]; then
  fail "shared runtime inputs" "host-owned env, overlay, or atomic runtime state is missing" 32
fi
"$SAFE_IO" env-validate --path "$SHARED_ENV" --forbid-prefix OMEGA_MIGRATION_ >/dev/null || \
  fail "shared runtime env" "env grammar/ownership/control boundary is invalid" 32
if ! CANONICAL_LAKEHOUSE_BUCKET="$(
  "$SAFE_IO" lakehouse-contract --path "$SHARED_ENV"
)"; then
  fail "lakehouse bucket" "live GCP runtime bucket contract is invalid" 32
fi
if ! CANONICAL_RELEASE_BACKUP_BUCKET="$(
  "$SAFE_IO" release-backup-contract --path "$SHARED_ENV"
)"; then
  fail "release backup bucket" "live GCP backup bucket contract is invalid" 32
fi
"$SAFE_IO" gcs-release-backup-permissions \
  --bucket "$CANONICAL_RELEASE_BACKUP_BUCKET" >/dev/null || \
  fail "release backup permissions" "VM has missing or destructive backup permissions" 32
if grep -Eq '^(GHCR_[A-Z0-9_]*(TOKEN|PASSWORD|SECRET|CREDENTIAL|AUTH|USER)|GITHUB_TOKEN|DOCKER_AUTH_CONFIG)=' "$SHARED_ENV"; then
  fail "server-owned registry credential boundary" "registry credential found in runtime env" 32
fi
emit "shared runtime inputs" "PASS" "host-owned env and generated GCP overlay are canonical"
if [[ ! -x /usr/local/sbin/omega-operation-gate ]] || \
    ! OMEGA_GCP_ALLOW_OPERATION_MARKER="$RECONCILIATION_HANDOFF" \
      /usr/local/sbin/omega-operation-gate; then
  fail "reboot operation guard" "installed Docker gate rejected the current atomic state" 32
fi

AIRFLOW_MIGRATION_MARKER="${SHARED_ROOT}/.airflow-runtime-migrated"
AIRFLOW_MIGRATION_NEEDED=0
if [[ ! -e "$AIRFLOW_MIGRATION_MARKER" ]]; then
  AIRFLOW_MIGRATION_NEEDED=1
fi
AIRFLOW_DAGS_MIGRATION_MARKER="${SHARED_ROOT}/.airflow-dags-migrated"
AIRFLOW_DAGS_MIGRATION_NEEDED=0
if [[ ! -e "$AIRFLOW_DAGS_MIGRATION_MARKER" ]]; then
  AIRFLOW_DAGS_MIGRATION_NEEDED=1
fi

WORKDIR="$(mktemp -d /tmp/omega-gcp-release.XXXXXX)"

BACKUP_MANIFEST="${WORKDIR}/backup-manifest.json"
"$SAFE_IO" gcs-download --uri "$BACKUP_MANIFEST_URI" \
  --generation "$BACKUP_MANIFEST_GENERATION" --size "$BACKUP_MANIFEST_SIZE_BYTES" \
  --sha256 "$BACKUP_MANIFEST_SHA256" --output "$BACKUP_MANIFEST"
BACKUP_ACTUAL_SHA="$BACKUP_MANIFEST_SHA256"
python3 - "$BACKUP_MANIFEST" "$BACKUP_MANIFEST_URI" "$OLD_REF" "$OLD_VERSION" \
  "$CANONICAL_LAKEHOUSE_BUCKET" "$CANONICAL_RELEASE_BACKUP_BUCKET" \
  "$BACKUP_POLICY_SHA256" "$PROJECT_ID" <<'PY'
import hashlib
import json
import re
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if manifest.get("schema_version") != 4 or manifest.get("complete") is not True:
    raise SystemExit("backup manifest is not complete schema v4")
backup_id = manifest.get("backup_id", "")
storage = manifest.get("object_storage", {})
bucket = storage.get("bucket", "")
backup_storage = manifest.get("backup_storage", {})
backup_bucket = backup_storage.get("bucket", "")
if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9.-]{0,80}", backup_id):
    raise SystemExit("backup manifest id is invalid")
if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", bucket):
    raise SystemExit("backup manifest bucket is invalid")
if bucket != sys.argv[5]:
    raise SystemExit("backup manifest bucket differs from the live GCP runtime")
if backup_bucket != sys.argv[6] or backup_bucket == bucket:
    raise SystemExit("backup storage is not the isolated live GCP backup bucket")
if sys.argv[2] != f"gs://{backup_bucket}/_omega_backups/{backup_id}/manifest.json":
    raise SystemExit("backup manifest URI differs from its canonical identity")
expected_backup_controls = {
    "bucket": backup_bucket,
    "location": backup_storage.get("location"),
    "uniform_bucket_level_access": True,
    "public_access_prevention": "enforced",
    "versioning_enabled": True,
    "soft_delete_seconds": 2592000,
    "retention_seconds": 604800,
    "retention_locked": False,
    "vm_role": f"projects/{sys.argv[8]}/roles/omegaReleaseBackupWriter",
    "vm_permissions": [
        "storage.buckets.get",
        "storage.objects.create",
        "storage.objects.get",
    ],
    "policy_sha256": sys.argv[7],
}
if (
    backup_storage != expected_backup_controls
    or re.fullmatch(r"[A-Z0-9-]{2,40}", str(backup_storage.get("location", ""))) is None
):
    raise SystemExit("backup storage policy differs from the controller attestation")
policy_payload = dict(backup_storage)
policy_sha256 = policy_payload.pop("policy_sha256")
if hashlib.sha256(
    json.dumps(policy_payload, separators=(",", ":"), sort_keys=True).encode()
).hexdigest() != policy_sha256:
    raise SystemExit("backup storage policy hash is not self-consistent")
consistency = manifest.get("consistency", {})
if (
    consistency.get("writers_fenced") is not True
    or consistency.get("scheduler_fenced") is not True
    or consistency.get("database_default_transaction_read_only") is not True
):
    raise SystemExit("backup was not captured under the canonical writer fence")
if storage.get("versioning_enabled") is not True:
    raise SystemExit("backup lacks a versioned object-storage restore point")
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
if manifest.get("source_release", {}).get("deploy_ref") != sys.argv[3]:
    raise SystemExit("backup source commit does not match current release")
if manifest.get("source_release", {}).get("version") != sys.argv[4]:
    raise SystemExit("backup source version does not match current release")
if manifest.get("plaintext_runtime_secrets_included") is not False:
    raise SystemExit("backup manifest does not exclude plaintext runtime secrets")
restore_settings = manifest.get("database_restore_settings", {})
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
if manifest.get("database_restore_settings_sha256") != restore_settings_sha:
    raise SystemExit("pre-fence database restore policy checksum differs")
artifacts = manifest.get("artifacts", {})
expected_artifacts = {
    "postgres": "postgres.sql.gz",
    "postgres_gold": "postgres_gold.sql.gz",
    "object_manifest": "lakehouse_objects.jsonl",
    "runtime_images": "runtime-images.json",
    "runtime_provenance": "runtime-provenance.json",
}
if set(artifacts) != set(expected_artifacts):
    raise SystemExit("backup artifact inventory is not exact")
for name, key in expected_artifacts.items():
    artifact = artifacts.get(name, {})
    if artifact.get("uri") != f"gs://{backup_bucket}/_omega_backups/{backup_id}/{key}":
        raise SystemExit(f"backup artifact {name} URI is invalid")
    if (
        re.fullmatch(r"[0-9a-f]{64}", str(artifact.get("sha256", ""))) is None
        or re.fullmatch(r"[1-9][0-9]*", str(artifact.get("generation", ""))) is None
        or not isinstance(artifact.get("size_bytes"), int)
        or artifact["size_bytes"] < 1
    ):
        raise SystemExit(f"backup artifact {name} is incomplete")
PY
read_backup_database_image_field() {
  python3 - "$BACKUP_MANIFEST" "$1" "$2" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(payload["database_images"][sys.argv[2]][sys.argv[3]])
PY
}
EXPECTED_OP_CONFIGURED="$(read_backup_database_image_field postgres configured_ref)"
EXPECTED_OP_REPO_DIGEST="$(read_backup_database_image_field postgres repo_digest)"
EXPECTED_OP_IMAGE_ID="$(read_backup_database_image_field postgres image_id)"
EXPECTED_GOLD_CONFIGURED="$(read_backup_database_image_field postgres_gold configured_ref)"
EXPECTED_GOLD_REPO_DIGEST="$(read_backup_database_image_field postgres_gold repo_digest)"
EXPECTED_GOLD_IMAGE_ID="$(read_backup_database_image_field postgres_gold image_id)"
if [[ "$EXPECTED_OP_CONFIGURED" != "pgvector/pgvector:pg15" || \
      ! "$EXPECTED_OP_REPO_DIGEST" =~ ^pgvector/pgvector@sha256:[0-9a-f]{64}$ || \
      ! "$EXPECTED_OP_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ || \
      "$EXPECTED_GOLD_CONFIGURED" != "postgres:15" || \
      ! "$EXPECTED_GOLD_REPO_DIGEST" =~ ^postgres@sha256:[0-9a-f]{64}$ || \
      ! "$EXPECTED_GOLD_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  fail "database image identity" "backup database image inventory is not canonical" 25
fi
python3 - "$PREDEPLOY_ATTESTATION" "$BACKUP_MANIFEST" "$DEPLOY_REF" \
  "$OLD_REF" "$BACKUP_MANIFEST_URI" "$BACKUP_MANIFEST_GENERATION" \
  "$BACKUP_MANIFEST_SIZE_BYTES" "$BACKUP_MANIFEST_SHA256" <<'PY'
import json
import pathlib
import re
import stat
import sys
from datetime import datetime, timedelta, timezone

path = pathlib.Path(sys.argv[1])
manifest = json.load(open(sys.argv[2], encoding="utf-8"))
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
    raise SystemExit("server-owned pre-deploy backup attestation is unsafe")
attestation = json.load(open(path, encoding="utf-8"))
expected_keys = {
    "schema_version",
    "state",
    "backup_id",
    "source_ref",
    "candidate_ref",
    "manifest_uri",
    "manifest_generation",
    "manifest_size_bytes",
    "manifest_sha256",
    "manifest_created_at",
    "attested_at",
    "lakehouse_bucket",
    "backup_bucket",
    "backup_policy_sha256",
    "database_restore_settings",
    "database_restore_settings_sha256",
}
if set(attestation) != expected_keys or attestation.get("schema_version") != 1:
    raise SystemExit("pre-deploy backup attestation shape is invalid")
if (
    attestation.get("state") != "ready"
    or attestation.get("backup_id") != manifest.get("backup_id")
    or attestation.get("source_ref") != sys.argv[4]
    or attestation.get("candidate_ref") != sys.argv[3]
    or attestation.get("manifest_uri") != sys.argv[5]
    or attestation.get("manifest_generation") != sys.argv[6]
    or attestation.get("manifest_size_bytes") != int(sys.argv[7])
    or attestation.get("manifest_sha256") != sys.argv[8]
    or attestation.get("manifest_created_at") != manifest.get("created_at")
    or attestation.get("lakehouse_bucket") != manifest.get("object_storage", {}).get("bucket")
    or attestation.get("backup_bucket") != manifest.get("backup_storage", {}).get("bucket")
    or attestation.get("backup_policy_sha256") != manifest.get("backup_storage", {}).get("policy_sha256")
    or attestation.get("database_restore_settings")
    != manifest.get("database_restore_settings")
    or attestation.get("database_restore_settings_sha256")
    != manifest.get("database_restore_settings_sha256")
):
    raise SystemExit("backup is not the fresh server-owned attestation for this deploy")


def parsed(value: object) -> datetime:
    if not isinstance(value, str):
        raise SystemExit("pre-deploy backup timestamp is missing")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise SystemExit("pre-deploy backup timestamp lacks timezone")
    return result.astimezone(timezone.utc)


backup_id = str(manifest.get("backup_id", ""))
if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9.-]{0,80}", backup_id) is None:
    raise SystemExit("pre-deploy backup ID is invalid")
started = datetime.strptime(backup_id[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
created = parsed(manifest.get("created_at"))
attested = parsed(attestation.get("attested_at"))
now = datetime.now(timezone.utc)
if not (
    started <= created <= attested <= now
    and now - started <= timedelta(hours=4)
    and now - created <= timedelta(minutes=30)
    and now - attested <= timedelta(minutes=30)
):
    raise SystemExit("pre-deploy backup is stale or temporally incoherent")
PY
emit "pre-deploy backup verified" "PASS" "manifest_sha256=${BACKUP_ACTUAL_SHA} source=${OLD_REF}"

ARTIFACT="${WORKDIR}/repo.tar.gz"
"$SAFE_IO" gcs-download --uri "$ARTIFACT_URI" --generation "$ARTIFACT_GENERATION" \
  --size "$ARTIFACT_SIZE_BYTES" --sha256 "$ARTIFACT_SHA256" --output "$ARTIFACT"
ARTIFACT_ACTUAL_SHA="$ARTIFACT_SHA256"
emit "release artifact checksum" "PASS" "sha256=${ARTIFACT_ACTUAL_SHA}"

RELEASE_MARKER="${RELEASE_DIR}/.omega-release.json"
if [[ -e "$RELEASE_DIR" || -L "$RELEASE_DIR" ]]; then
  if ! EXISTING_TREE_SHA256="$($SAFE_IO tree-sha256 --root "$RELEASE_DIR" \
      --exclude .omega-release.json --require-read-only)"; then
    fail "immutable release directory" "existing release ownership, mode, or path confinement is unsafe" 36
  fi
  if ! python3 - "$RELEASE_MARKER" "$RELEASE_DIR" "$RELEASE_ROOT" \
    "$DEPLOY_REF" "$TARGET_TAG" \
    "$ARTIFACT_GENERATION" "$ARTIFACT_SIZE_BYTES" "$ARTIFACT_SHA256" \
    "$EXPECTED_VERSION" "$EXISTING_TREE_SHA256" <<'PY'
from datetime import datetime
import json
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
release = pathlib.Path(sys.argv[2])
release_root = pathlib.Path(sys.argv[3])
info = path.lstat()
if (
    not stat.S_ISREG(info.st_mode)
    or info.st_uid != 0
    or info.st_gid != 0
    or stat.S_IMODE(info.st_mode) != 0o400
    or info.st_size < 1
    or info.st_size > 65536
    or path.parent != release
    or release.resolve(strict=True).parent != release_root.resolve(strict=True)
):
    raise SystemExit("existing immutable release marker is unsafe or escaped")
payload = json.loads(path.read_text(encoding="utf-8"))
expected_keys = {
    "schema_version",
    "deploy_ref",
    "tag",
    "artifact_generation",
    "artifact_size_bytes",
    "artifact_sha256",
    "version",
    "tree_sha256",
    "installed_at",
}
if set(payload) != expected_keys:
    raise SystemExit("existing immutable release marker shape differs")
expected = {
    "schema_version": 1,
    "deploy_ref": sys.argv[4],
    "tag": sys.argv[5],
    "artifact_generation": sys.argv[6],
    "artifact_size_bytes": int(sys.argv[7]),
    "artifact_sha256": sys.argv[8],
    "version": sys.argv[9],
    "tree_sha256": sys.argv[10],
}
for key, value in expected.items():
    if payload.get(key) != value:
        raise SystemExit(f"existing immutable release marker mismatch: {key}")
installed = payload.get("installed_at")
if not isinstance(installed, str):
    raise SystemExit("existing immutable release marker timestamp is invalid")
parsed = datetime.fromisoformat(installed.replace("Z", "+00:00"))
if parsed.tzinfo is None:
    raise SystemExit("existing immutable release marker timestamp lacks timezone")
PY
  then
    fail "immutable release directory" "existing marker or exact tree hash differs" 36
  fi
  emit "immutable release directory" "PASS" "existing verified release reused"
else
  RELEASE_TMP="${RELEASE_ROOT}/.${DEPLOY_REF}.tmp.$$"
  if [[ -e "$RELEASE_TMP" || -L "$RELEASE_TMP" ]]; then
    fail "immutable release directory" "unexpected temporary path already exists" 36
  fi
  install -d -m 0700 "$RELEASE_TMP"
  "$SAFE_IO" safe-extract --archive "$ARTIFACT" --destination "$RELEASE_TMP"
  chmod -R a-w "$RELEASE_TMP"
  TREE_SHA256="$($SAFE_IO tree-sha256 --root "$RELEASE_TMP" --require-read-only)"
  RELEASE_MARKER_STAGE="${WORKDIR}/.omega-release.${DEPLOY_REF}.json"
  python3 - "$RELEASE_MARKER_STAGE" "$DEPLOY_REF" "$TARGET_TAG" \
    "$ARTIFACT_GENERATION" "$ARTIFACT_SIZE_BYTES" "$ARTIFACT_SHA256" \
    "$EXPECTED_VERSION" "$TREE_SHA256" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "deploy_ref": sys.argv[2],
    "tag": sys.argv[3],
    "artifact_generation": sys.argv[4],
    "artifact_size_bytes": int(sys.argv[5]),
    "artifact_sha256": sys.argv[6],
    "version": sys.argv[7],
    "tree_sha256": sys.argv[8],
    "installed_at": datetime.now(timezone.utc).isoformat(),
}
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  chmod 0400 "$RELEASE_MARKER_STAGE"
  "$SAFE_IO" fsync-file "$RELEASE_MARKER_STAGE"
  chmod u+w "$RELEASE_TMP"
  install -m 0400 "$RELEASE_MARKER_STAGE" "$RELEASE_TMP/.omega-release.json"
  chmod a-w "$RELEASE_TMP"
  "$SAFE_IO" fsync-file "$RELEASE_TMP/.omega-release.json"
  "$SAFE_IO" fsync-dir "$RELEASE_TMP"
  if [[ "$TREE_SHA256" != "$($SAFE_IO tree-sha256 --root "$RELEASE_TMP" \
      --exclude .omega-release.json --require-read-only)" ]]; then
    fail "immutable release directory" "release tree changed while publishing its exact marker" 36
  fi
  mv "$RELEASE_TMP" "$RELEASE_DIR"
  "$SAFE_IO" fsync-dir "$RELEASE_ROOT"
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
CANDIDATE_SAFE_IO="${RELEASE_DIR}/scripts/gcp/safe_io.py"
CANDIDATE_WATCHDOG="${RELEASE_DIR}/scripts/gcp/operation-watchdog.sh"
CANDIDATE_FIREWALL="${RELEASE_DIR}/scripts/gcp/metadata-firewall.sh"
CANDIDATE_OPERATION_GUARD="${RELEASE_DIR}/infra/terraform-gcp/templates/omega-operation-gate"
if [[ ! -x "$CANDIDATE_AUTH_RUNNER" || ! -x "$CANDIDATE_PREFLIGHT" || \
      ! -x "$RUNTIME_CONTRACT" || ! -x "$CANDIDATE_REBOOT_HELPER" || \
      ! -x "$CANDIDATE_SAFE_IO" || ! -x "$CANDIDATE_WATCHDOG" || \
      ! -x "$CANDIDATE_FIREWALL" || ! -x "$CANDIDATE_OPERATION_GUARD" ]]; then
  fail "candidate release helpers" "release does not contain the audited auth, preflight, and runtime helpers" 38
fi
"$CANDIDATE_FIREWALL" install >/dev/null
emit "container metadata host policy" "PASS" \
  "IPv4/IPv6 forwarding deny installed before candidate containers start"
if [[ "$RECONCILIATION_HANDOFF" == "1" ]]; then
  if ! OMEGA_GCP_ALLOW_OPERATION_MARKER=1 "$CANDIDATE_OPERATION_GUARD"; then
    fail "reboot operation guard" "candidate guard rejected the exact reconciliation handoff" 38
  fi
else
  if ! "$CANDIDATE_OPERATION_GUARD"; then
    fail "reboot operation guard" "candidate guard is incompatible with the current atomic state" 38
  fi
fi
install_operation_guard "$CANDIDATE_OPERATION_GUARD"
install_operation_watchdog "$CANDIDATE_WATCHDOG"
if ! OMEGA_GCP_ALLOW_OPERATION_MARKER="$RECONCILIATION_HANDOFF" \
    /usr/local/sbin/omega-operation-gate; then
  fail "reboot operation guard" "candidate guard rejected the current atomic state" 38
fi
emit "reboot operation guard" "PASS" "exact candidate ExecStartPre installed and current state verified"
install -d -m 0700 "$(dirname "$AUTH_RUNNER")"
AUTH_RUNNER_TMP="$(dirname "$AUTH_RUNNER")/.ghcr-auth-run.${DEPLOY_REF}.$$"
install -m 0700 "$CANDIDATE_AUTH_RUNNER" "$AUTH_RUNNER_TMP"
mv -Tf "$AUTH_RUNNER_TMP" "$AUTH_RUNNER"
"$SAFE_IO" fsync-file "$AUTH_RUNNER"
"$SAFE_IO" fsync-dir "$(dirname "$AUTH_RUNNER")"
emit "server-owned GHCR auth runner" "PASS" "installed from exact release artifact"

RELEASE_SERVICES=(console workspace refinement vault mcp-infra airflow replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-successfactors sap-s4hana)
LOCK_TMP="${SHARED_ROOT}/image-locks/.${DEPLOY_REF}.tmp.$$"
install -d -m 0700 "$LOCK_TMP"
NEW_LOCK_ENV="${LOCK_TMP}/release-images.env"
NEW_LOCK_MANIFEST="${LOCK_TMP}/manifest.json"
NEW_IMAGE_AUTHORITY_TMP="${NEW_LOCK_ENV}.authority.json"
NEW_IMAGE_AUTHORITY="${LOCK_TMP}/image-authority.json"
if ! OMEGA_GCP_ENVIRONMENT="$GCP_ENVIRONMENT" \
  OMEGA_GHCR_PULL_SECRET_VERSION="$GHCR_SECRET_VERSION" \
  OMEGA_GCP_IMAGE_AUTHORITY_MODE=published \
  OMEGA_RELEASE_TAG_MANIFEST_SHA256="$PUBLISHED_MANIFEST_SHA256" \
  OMEGA_RELEASE_TAG_OBJECT_SHA="$PUBLISHED_TAG_OBJECT_SHA" \
  "$AUTH_RUNNER" "$CANDIDATE_PREFLIGHT" "$GHCR_OWNER" "$TARGET_TAG" \
    "$DEPLOY_REF" "$EXPECTED_VERSION" "$NEW_LOCK_ENV" >/dev/null 2>&1; then
  fail "private GHCR release pull" "less than 15/15 images pullable; credential output suppressed" 41
fi
if [[ ! -s "$NEW_IMAGE_AUTHORITY_TMP" ]]; then
  fail "published image authority" "fresh sealed-manifest receipt is missing" 41
fi
mv -T "$NEW_IMAGE_AUTHORITY_TMP" "$NEW_IMAGE_AUTHORITY"
emit "private GHCR release pull" "PASS" "15/15 tag=${TARGET_TAG}"

PUBLISHED_PREFLIGHT_ROOT="${SHARED_ROOT}/image-preflights/release-published-${TARGET_TAG}-${DEPLOY_REF}-by-${DEPLOY_REF}"
PUBLISHED_PREFLIGHT_LOCK="${PUBLISHED_PREFLIGHT_ROOT}/release-images.env"
PUBLISHED_PREFLIGHT_MANIFEST="${PUBLISHED_PREFLIGHT_ROOT}/manifest.json"
PUBLISHED_PREFLIGHT_AUTHORITY="${PUBLISHED_PREFLIGHT_ROOT}/image-authority.json"
if [[ ! -s "$PUBLISHED_PREFLIGHT_LOCK" || \
      ! -s "$PUBLISHED_PREFLIGHT_MANIFEST" || \
      ! -s "$PUBLISHED_PREFLIGHT_AUTHORITY" ]]; then
  fail "published preflight handoff" "immutable published preflight evidence is incomplete" 41
fi
if ! python3 - "$PUBLISHED_PREFLIGHT_MANIFEST" \
    "$PUBLISHED_PREFLIGHT_AUTHORITY" "$PUBLISHED_MANIFEST_SHA256" \
    "$PUBLISHED_TAG_OBJECT_SHA" "$DEPLOY_REF" "$TARGET_TAG" <<'PY'
import hashlib
import json
import pathlib
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
authority_raw = pathlib.Path(sys.argv[2]).read_bytes()
authority = json.loads(authority_raw)
if (
    manifest.get("schema_version") != 2
    or manifest.get("image_authority_sha256") != hashlib.sha256(authority_raw).hexdigest()
    or manifest.get("release_candidate_manifest_digest") != sys.argv[3]
    or manifest.get("release_tag_object_sha") != sys.argv[4]
    or manifest.get("target_ref") != sys.argv[5]
    or manifest.get("target_tag") != sys.argv[6]
    or authority.get("authority_mode") != "published"
    or authority.get("manifest_digest") != sys.argv[3]
    or authority.get("tag_object_sha") != sys.argv[4]
):
    raise SystemExit("published preflight authority binding differs")
PY
then
  fail "published preflight handoff" "annotated-tag/sealed-manifest binding differs" 41
fi
cmp "$PUBLISHED_PREFLIGHT_LOCK" "$NEW_LOCK_ENV" >/dev/null || \
  fail "published preflight handoff" "fresh digest lock differs byte-for-byte" 41
cmp "$PUBLISHED_PREFLIGHT_AUTHORITY" "$NEW_IMAGE_AUTHORITY" >/dev/null || \
  fail "published preflight handoff" "fresh authority receipt differs byte-for-byte" 41
emit "published preflight handoff" "PASS" \
  "same sealed payload and exact 15 digest lock consumed byte-for-byte"

python3 - "$NEW_LOCK_ENV" "$NEW_LOCK_MANIFEST" "$DEPLOY_REF" "$EXPECTED_VERSION" \
  "$ARTIFACT_URI" "$ARTIFACT_GENERATION" "$ARTIFACT_SIZE_BYTES" \
  "$ARTIFACT_SHA256" "$GHCR_OWNER" "$COMPOSE_PROJECT" "$TARGET_TAG" \
  "$NEW_IMAGE_AUTHORITY" "$PUBLISHED_MANIFEST_SHA256" \
  "$PUBLISHED_TAG_OBJECT_SHA" <<'PY'
import json
import pathlib
import re
import sys

(lock_path, manifest_path, deploy_ref, version, artifact_uri, artifact_generation,
 artifact_size, artifact_sha, owner, project, tag, authority_path,
 sealed_digest, tag_object_sha) = sys.argv[1:]
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
    "schema_version": 3,
    "deploy_ref": deploy_ref,
    "version": version,
    "artifact_uri": artifact_uri,
    "artifact_generation": artifact_generation,
    "artifact_size_bytes": int(artifact_size),
    "artifact_sha256": artifact_sha,
    "owner": owner,
    "compose_project": project,
    "tag": tag,
    "unique_image_count": len(images),
    "service_binding_count": len(service_images),
    "image_authority_sha256": __import__("hashlib").sha256(
        pathlib.Path(authority_path).read_bytes()
    ).hexdigest(),
    "release_candidate_manifest_digest": sealed_digest,
    "release_tag_object_sha": tag_object_sha,
    "images": images,
    "services": {service: images[image] for service, image in service_images.items()},
}
pathlib.Path(manifest_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
chmod 0400 "$NEW_LOCK_ENV" "$NEW_LOCK_MANIFEST" "$NEW_IMAGE_AUTHORITY"
"$SAFE_IO" fsync-file "$NEW_LOCK_ENV" "$NEW_LOCK_MANIFEST" "$NEW_IMAGE_AUTHORITY"
"$SAFE_IO" fsync-dir "$LOCK_TMP"
NEW_LOCK_TREE_SHA256="$($SAFE_IO tree-sha256 --root "$LOCK_TMP")"
if [[ -e "$LOCK_DIR" || -L "$LOCK_DIR" ]]; then
  if ! EXISTING_LOCK_TREE_SHA256="$($SAFE_IO tree-sha256 --root "$LOCK_DIR")"; then
    fail "immutable image lock" "existing lock ownership, mode, or path confinement is unsafe" 42
  fi
  if [[ ! -s "$LOCK_ENV" || ! -s "$LOCK_MANIFEST" ]]; then
    fail "immutable image lock" "existing lock is incomplete" 42
  fi
  if [[ "$EXISTING_LOCK_TREE_SHA256" != "$NEW_LOCK_TREE_SHA256" ]]; then
    fail "immutable image lock" "existing lock tree inventory, permissions, or bytes differ" 42
  fi
  cmp "$LOCK_ENV" "$NEW_LOCK_ENV" >/dev/null || fail "immutable image lock" "existing env lock differs from authenticated pulls" 42
  cmp "$LOCK_MANIFEST" "$NEW_LOCK_MANIFEST" >/dev/null || fail "immutable image lock" "existing manifest differs from candidate identity" 42
  rm -rf -- "$LOCK_TMP"
  LOCK_TMP=""
  emit "immutable image digest lock" "PASS" "existing 15/15 lock revalidated against fresh pulls"
else
  mv "$LOCK_TMP" "$LOCK_DIR"
  "$SAFE_IO" fsync-dir "${SHARED_ROOT}/image-locks"
  LOCK_TMP=""
fi
if ! LOCK_TREE_SHA256="$($SAFE_IO tree-sha256 --root "$LOCK_DIR")" || \
    [[ "$LOCK_TREE_SHA256" != "$NEW_LOCK_TREE_SHA256" ]]; then
  fail "immutable image lock" "published lock tree read-back differs" 42
fi
LOCK_SHA256="$(sha256sum "$LOCK_MANIFEST" | awk '{print $1}')"
LOCK_ENV_SHA256="$(sha256sum "$LOCK_ENV" | awk '{print $1}')"
emit "immutable image digest lock" "PASS" "15/15 manifest_sha256=${LOCK_SHA256} tree_sha256=${LOCK_TREE_SHA256}"

COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
  --env-file "$LOCK_ENV" -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" \
  -f "$RELEASE_COMPOSE" --profile sap)
COMPOSE_RUNTIME_READY=1
"${COMPOSE[@]}" config -q
compose_image_for() {
  "${COMPOSE[@]}" config --format json | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
value = payload.get("services", {}).get(sys.argv[1], {}).get("image")
if not isinstance(value, str) or not value:
    raise SystemExit(1)
print(value)
' "$1"
}
if [[ "$(compose_image_for postgres)" != "$EXPECTED_OP_CONFIGURED" || \
      "$(compose_image_for postgres_gold)" != "$EXPECTED_GOLD_CONFIGURED" ]]; then
  fail "database image identity" "candidate Compose DB images differ from the backup" 43
fi
for spec in \
  "mode_postgres|${EXPECTED_OP_CONFIGURED}|${EXPECTED_OP_REPO_DIGEST}|${EXPECTED_OP_IMAGE_ID}" \
  "mode_postgres_gold|${EXPECTED_GOLD_CONFIGURED}|${EXPECTED_GOLD_REPO_DIGEST}|${EXPECTED_GOLD_IMAGE_ID}"; do
  IFS='|' read -r container configured repo_digest image_id <<<"$spec"
  if [[ "$(docker image inspect "$configured" --format '{{.Id}}' 2>/dev/null || true)" != "$image_id" || \
        "$(docker image inspect "$repo_digest" --format '{{.Id}}' 2>/dev/null || true)" != "$image_id" || \
        "$(docker inspect "$container" --format '{{.Config.Image}}' 2>/dev/null || true)" != "$configured" || \
        "$(docker inspect "$container" --format '{{.Image}}' 2>/dev/null || true)" != "$image_id" ]]; then
    fail "database image identity" "pre-deploy DB image differs for ${container}" 43
  fi
done
emit "database image identity" "PASS" "candidate/current/local DB images match backup digests"
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

"$WATCHDOG" arm "$$" "$OPERATION_MARKER"
write_operation_state "fencing"
MUTATION_STARTED=1
emit "independent operation watchdog" "PASS" "systemd monitor armed before durable marker and writer mutation"
consume_predeploy_attestation
emit "pre-deploy backup attestation" "PASS" "fresh server-owned binding consumed exactly once"
"${COMPOSE[@]}" stop --timeout 60 "${MUTATING_SERVICES[@]}"
python3 "$RUNTIME_CONTRACT" writer-fence --compose-project "$COMPOSE_PROJECT" >/dev/null || \
  fail "writer and mutator fence" "host-local labeled or unlabeled proprietary writer remains running" 44
DB_FENCE_ACTIVE=1
database_fence on || fail "database write fence" "cannot persist read-only defaults" 44
write_operation_state "fenced"
emit "writer and scheduler fence" "PASS" "all GCP host-local mutators stopped; both databases persistently read-only"

# Legacy Airflow data is copied only after the backup, candidate, image, and
# volume identities are verified and the independent fail-closed watchdog plus
# durable operation fence are active. A crash can therefore never leave an
# unrecorded host mutation while application writers are still running.
if (( AIRFLOW_MIGRATION_NEEDED == 1 )); then
  for runtime_dir in logs plugins; do
    if [[ -d "${OLD_RELEASE}/airflow/${runtime_dir}" ]]; then
      cp -a "${OLD_RELEASE}/airflow/${runtime_dir}/." "${SHARED_ROOT}/airflow/${runtime_dir}/"
    fi
  done
  chown -R 50000:0 "${SHARED_ROOT}/airflow/logs" "${SHARED_ROOT}/airflow/plugins"
  touch "$AIRFLOW_MIGRATION_MARKER"
  emit "mutable Airflow mounts" "PASS" "existing logs/plugins preserved outside immutable releases"
fi
if (( AIRFLOW_DAGS_MIGRATION_NEEDED == 1 )); then
  if [[ -d "${OLD_RELEASE}/airflow/dags" ]]; then
    cp -a "${OLD_RELEASE}/airflow/dags/." "${SHARED_ROOT}/airflow/dags/"
  fi
  chown -R 50000:0 "${SHARED_ROOT}/airflow/dags"
  touch "$AIRFLOW_DAGS_MIGRATION_MARKER"
  emit "mutable Airflow DAG mount" "PASS" "existing generated DAGs preserved outside immutable releases"
fi

"${COMPOSE[@]}" up -d --no-build --pull never --no-deps --force-recreate postgres postgres_gold
for spec in \
  "mode_postgres|${EXPECTED_OP_CONFIGURED}|${EXPECTED_OP_IMAGE_ID}" \
  "mode_postgres_gold|${EXPECTED_GOLD_CONFIGURED}|${EXPECTED_GOLD_IMAGE_ID}"; do
  IFS='|' read -r container configured image_id <<<"$spec"
  if [[ "$(docker inspect "$container" --format '{{.Config.Image}}' 2>/dev/null || true)" != "$configured" || \
        "$(docker inspect "$container" --format '{{.Image}}' 2>/dev/null || true)" != "$image_id" ]]; then
    fail "database image identity" "post-recreation DB image differs for ${container}" 45
  fi
done
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

env -i \
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
HOME=/root \
LANG=C.UTF-8 \
OMEGA_GCP_SAFE_IO="${RELEASE_DIR}/scripts/gcp/safe_io.py" \
OMEGA_MIGRATION_COMPOSE_FILE="$BASE_COMPOSE" \
OMEGA_MIGRATION_ENV_FILE="$SHARED_ENV" \
OMEGA_MIGRATION_COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT" \
OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT=1 \
OMEGA_MIGRATION_BOOTSTRAP_MODE=0 \
OMEGA_MIGRATION_OLD_REF="$OLD_REF" \
OMEGA_MIGRATION_CANDIDATE_REF="$DEPLOY_REF" \
OMEGA_MIGRATION_RELEASE_VERSION="$EXPECTED_VERSION" \
OMEGA_MIGRATION_BASELINE_MANIFEST="${RELEASE_DIR}/infra/migrations/manifests/gcp-live-6b12883c5b5ea0537120279ccbee4947137998a2.json" \
OMEGA_MIGRATION_BASELINE_MANIFEST_SHA256="b6cb33c9b1a0f93e13fe2eb68f2e8fff1fdeedb2979bbfb22840a2a35d2e4a18" \
OMEGA_MIGRATION_RELEASE_MANIFEST="${RELEASE_DIR}/infra/migrations/manifests/v1.45.207-beta.json" \
OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256="fbc2db83f82eecf9733a60a23fae7c9982d0f9aba52e474f41ba909acbaedc93" \
  bash "${RELEASE_DIR}/scripts/apply_db_migrations.sh"
write_operation_state "migrated"
emit "canonical database migrations" "PASS" "operational and Gold runner completed"

write_operation_state "unfencing-databases"
database_fence off || fail "database write fence release" "cannot reset persistent read-only defaults" 48
DB_FENCE_ACTIVE=0
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

"$CANDIDATE_FIREWALL" verify-container >/dev/null || \
  fail "container metadata isolation" "candidate console could reach VM metadata or firewall verification failed" 48
emit "container metadata isolation" "PASS" \
  "candidate console denied IPv4/IPv6 VM metadata while host identity remains available"

RUNTIME_GREEN=0
for _ in $(seq 1 60); do
  if python3 "$RUNTIME_CONTRACT" lock --lock-env "$LOCK_ENV" \
      --compose-project "$COMPOSE_PROJECT" --deploy-ref "$DEPLOY_REF" \
      --version "$EXPECTED_VERSION" --scheduler stopped --one-shots \
      --runtime-input "shared_env=${SHARED_ENV}" \
      --runtime-input "base_compose=${BASE_COMPOSE}" \
      --runtime-input "gcp_compose=${GCP_RUNTIME_COMPOSE}" \
      --runtime-input "release_compose=${RELEASE_COMPOSE}" >/dev/null 2>&1; then
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
DEPLOYMENT_PROVENANCE_STAGE="${WORKDIR}/deployment-provenance.json"
for _ in $(seq 1 60); do
  if python3 "$RUNTIME_CONTRACT" lock --lock-env "$LOCK_ENV" \
      --compose-project "$COMPOSE_PROJECT" --deploy-ref "$DEPLOY_REF" \
      --version "$EXPECTED_VERSION" --scheduler required --one-shots \
      --runtime-input "shared_env=${SHARED_ENV}" \
      --runtime-input "base_compose=${BASE_COMPOSE}" \
      --runtime-input "gcp_compose=${GCP_RUNTIME_COMPOSE}" \
      --runtime-input "release_compose=${RELEASE_COMPOSE}" \
      --write-provenance "$DEPLOYMENT_PROVENANCE_STAGE" >/dev/null 2>&1; then
    FINAL_RUNTIME_GREEN=1
    break
  fi
  sleep 3
done
if [[ "$FINAL_RUNTIME_GREEN" != "1" ]]; then
  fail "exact final runtime" "16 running services, one healthy scheduler, and 15 exact image digests are required" 50
fi
if ! DEPLOYMENT_PROVENANCE_SHA256="$(python3 - \
    "$DEPLOYMENT_PROVENANCE_STAGE" "$DEPLOYMENT_PROVENANCE" \
    "$DEPLOYMENT_ROOT" "$DEPLOY_REF" "$EXPECTED_VERSION" \
    "$COMPOSE_PROJECT" "$LOCK_ENV_SHA256" <<'PY'
import hashlib
import json
import os
import pathlib
import stat
import sys
import tempfile

(staged_raw, destination_raw, root_raw, deploy_ref, version,
 compose_project, lock_sha256) = sys.argv[1:]
staged = pathlib.Path(staged_raw)
destination = pathlib.Path(destination_raw)
root = pathlib.Path(root_raw)


def require_file(path: pathlib.Path, mode: int) -> os.stat_result:
    if not path.is_absolute() or not os.path.lexists(path):
        raise SystemExit("deployment evidence file is missing or relative")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != mode
        or path.resolve(strict=True) != pathlib.Path(os.path.abspath(path))
    ):
        raise SystemExit("deployment evidence file is linked, escaped, unowned, or has wrong mode")
    return info


root_info = root.lstat()
if (
    not root.is_absolute()
    or not stat.S_ISDIR(root_info.st_mode)
    or root_info.st_uid != 0
    or root_info.st_gid != 0
    or stat.S_IMODE(root_info.st_mode) & 0o022
    or root.resolve(strict=True) != pathlib.Path(os.path.abspath(root))
    or destination.parent != root
    or destination.name != f"{deploy_ref}.json"
):
    raise SystemExit("deployment evidence root or target is unsafe")
stage_info = require_file(staged, 0o600)
if stage_info.st_size < 1 or stage_info.st_size > 2 * 1024 * 1024:
    raise SystemExit("deployment evidence size is invalid")
payload = json.loads(staged.read_text(encoding="utf-8"))
expected_keys = {
    "schema_version",
    "mode",
    "compose_project",
    "deploy_ref",
    "version",
    "image_lock_sha256",
    "services",
}
if (
    set(payload) != expected_keys
    or payload.get("schema_version") != 1
    or payload.get("mode") != "day2"
    or payload.get("compose_project") != compose_project
    or payload.get("deploy_ref") != deploy_ref
    or payload.get("version") != version
    or payload.get("image_lock_sha256") != lock_sha256
    or not isinstance(payload.get("services"), dict)
    or not payload["services"]
):
    raise SystemExit("deployment evidence identity is not exact")
staged_bytes = staged.read_bytes()
digest = hashlib.sha256(staged_bytes).hexdigest()
if os.path.lexists(destination):
    existing_info = require_file(destination, 0o600)
    if existing_info.st_size != len(staged_bytes) or destination.read_bytes() != staged_bytes:
        raise SystemExit("existing deployment evidence differs from the exact runtime")
else:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=root
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(staged_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
require_file(destination, 0o600)
if destination.read_bytes() != staged_bytes:
    raise SystemExit("published deployment evidence read-back differs")
print(digest)
PY
)"; then
  fail "immutable deployment evidence" "existing evidence is linked, escaped, unowned, mode-drifted, or non-exact" 50
fi
write_operation_state "validated"
emit "exact final runtime" "PASS" "images=15/15 running=22 healthy=22 scheduler=1 analytics=healthy exact_lock=true"
emit "immutable deployment evidence" "PASS" "sha256=${DEPLOYMENT_PROVENANCE_SHA256}"

# Stage both canonical state files in one directory. The runtime-state symlink
# is the sole commit point, so no observer can see a bootstrap state without
# its matching runtime provenance.
STATE_STAGE="$(mktemp -d "${STATE_BUNDLES_ROOT}/.day2.${DEPLOY_REF}.XXXXXX")"
install -m 0600 "$DEPLOYMENT_PROVENANCE" "${STATE_STAGE}/runtime-provenance.json"
REBOOT_SHA256="$(sha256sum "$CANDIDATE_REBOOT_HELPER" | awk '{print $1}')"
CONTRACT_SHA256="$(sha256sum "$RUNTIME_CONTRACT" | awk '{print $1}')"
SAFE_IO_SHA256="$(sha256sum "$CANDIDATE_SAFE_IO" | awk '{print $1}')"
python3 - "${STATE_STAGE}/bootstrap-state.json" \
  "${STATE_STAGE}/runtime-provenance.json" "$DEPLOY_REF" "$EXPECTED_VERSION" \
  "$ARTIFACT_URI" "$ARTIFACT_GENERATION" "$ARTIFACT_SIZE_BYTES" \
  "$ARTIFACT_SHA256" "$REBOOT_SHA256" "$CONTRACT_SHA256" \
  "$SAFE_IO_SHA256" <<'PY'
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
        "source_artifact_generation": sys.argv[6],
        "source_artifact_size_bytes": int(sys.argv[7]),
        "source_artifact_sha256": sys.argv[8],
        "reboot_runtime_sha256": sys.argv[9],
        "runtime_contract_sha256": sys.argv[10],
        "safe_io_sha256": sys.argv[11],
    },
    "completed_at": datetime.now(timezone.utc).isoformat(),
}
pathlib.Path(sys.argv[1]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
chmod 0600 "${STATE_STAGE}/bootstrap-state.json"
"$SAFE_IO" fsync-file "${STATE_STAGE}/bootstrap-state.json" \
  "${STATE_STAGE}/runtime-provenance.json"
"$SAFE_IO" fsync-dir "$STATE_STAGE"
STATE_FINAL="${STATE_BUNDLES_ROOT}/day2-${DEPLOY_REF}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mv "$STATE_STAGE" "$STATE_FINAL"
"$SAFE_IO" fsync-dir "$STATE_BUNDLES_ROOT"
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
"$SAFE_IO" fsync-dir "$APP_ROOT" "$SHARED_ROOT"
if [[ "$(readlink -f "$CURRENT_LINK")" != "$RELEASE_DIR" ]]; then
  fail "atomic current promotion" "read-back does not match candidate release" 51
fi
if ! OMEGA_GCP_ALLOW_OPERATION_MARKER=1 /usr/local/sbin/omega-operation-gate; then
  fail "atomic runtime state" "published state pair/current/helper read-back failed" 51
fi
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
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
date -u +%Y-%m-%dT%H:%M:%SZ > "${APP_ROOT}/DEPLOYED"
write_operation_state "promoted"
rm -f -- "$OPERATION_MARKER"
"$SAFE_IO" fsync-dir "$SHARED_ROOT"
"$WATCHDOG" disarm "$$" "$OPERATION_MARKER"
DB_FENCE_ACTIVE=0
MUTATION_STARTED=0
emit "atomic current promotion" "PASS" "current=${DEPLOY_REF} previous=${OLD_REF}"
printf 'OMEGA_GCP_RELEASE_JSON={"status":"PASS","tag":"%s","deploy_ref":"%s","version":"%s","previous_ref":"%s","image_lock_sha256":"%s"}\n' \
  "$TARGET_TAG" "$DEPLOY_REF" "$EXPECTED_VERSION" "$OLD_REF" "$LOCK_SHA256"
