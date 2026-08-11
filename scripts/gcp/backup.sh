#!/usr/bin/env bash
# Create a writer-fenced, retention-protected pre-deploy backup on GCP.
set -Eeuo pipefail
set +x
umask 077

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: backup.sh must run through sudo" >&2
  exit 10
fi

RELEASE_BACKUP_BUCKET="${1:-}"
BACKUP_ID="${2:-}"
COMPOSE_PROJECT="${3:-infra}"
CANDIDATE_REF="${4:-}"
CANDIDATE_ARTIFACT_URI="${5:-}"
CANDIDATE_ARTIFACT_GENERATION="${6:-}"
CANDIDATE_ARTIFACT_SIZE_BYTES="${7:-}"
CANDIDATE_ARTIFACT_SHA256="${8:-}"
EXPECTED_CURRENT_REF="${9:-}"
BACKUP_POLICY_SHA256="${10:-}"
BACKUP_LOCATION="${11:-}"
PROJECT_ID="${12:-}"
OPERATION_MODE="${13:-backup}"
APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
CURRENT_LINK="${APP_ROOT}/current"
SHARED_ROOT="${APP_ROOT}/shared"
SHARED_ENV="${SHARED_ROOT}/infra.env"
GCP_RUNTIME_COMPOSE="${SHARED_ROOT}/docker-compose.gcp.yml"
LEGACY_IMAGE_COMPOSE="${SHARED_ROOT}/docker-compose.legacy-images.gcp.yml"
ENV_BACKUPS_ROOT="${SHARED_ROOT}/env-backups"
OPERATION_MARKER="${SHARED_ROOT}/operation-state.json"
PREDEPLOY_ATTESTATION="${SHARED_ROOT}/predeploy-backup.json"
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
DB_FENCE_ACTIVE=0
DB_CONN_LIMIT_MODECISSIONS=""
DB_CONN_LIMIT_GOLD=""
DB_READONLY_MODECISSIONS=""
DB_READONLY_GOLD=""
DB_READONLY_CONFIG_MODECISSIONS=""
DB_READONLY_CONFIG_GOLD=""
RUNNING_BEFORE=()
SAFE_IO="${OMEGA_GCP_SAFE_IO:-}"
WATCHDOG="/usr/local/sbin/omega-operation-watchdog"

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
  install -d -m 0755 /usr/local/sbin /etc/systemd/system/docker.service.d
  guard_tmp="$(mktemp /usr/local/sbin/.omega-operation-gate.XXXXXX)"
  dropin_tmp="$(mktemp /etc/systemd/system/docker.service.d/.omega-operation-gate.conf.XXXXXX)"
  install -m 0755 "$source" "$guard_tmp"
  printf '%s\n' '[Service]' \
    'ExecStartPre=/usr/local/sbin/omega-operation-gate' > "$dropin_tmp"
  chmod 0644 "$dropin_tmp"
  "$SAFE_IO" fsync-file "$guard_tmp" "$dropin_tmp"
  # Publish the drop-in first. On a fresh host, a crash before the gate rename
  # leaves ExecStartPre pointing at a missing executable, which fails closed.
  mv -Tf "$dropin_tmp" /etc/systemd/system/docker.service.d/omega-operation-gate.conf
  "$SAFE_IO" fsync-dir /etc/systemd/system/docker.service.d
  mv -Tf "$guard_tmp" /usr/local/sbin/omega-operation-gate
  "$SAFE_IO" fsync-dir /usr/local/sbin
  systemctl daemon-reload
}

install_operation_watchdog() {
  local source="$1" temporary
  if [[ ! -x "$source" ]]; then
    fail "operation watchdog" "exact candidate watchdog is missing" 26
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
  python3 - "$OPERATION_MARKER" "$state" "$BACKUP_ID" "$CURRENT_REF" \
    "$CANDIDATE_REF" "$OPERATION_MODE" <<'PY'
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
mode = sys.argv[6]
if mode == "adopt-runtime":
    payload = {
        "schema_version": 1,
        "operation": "startup-adoption",
        "state": sys.argv[2],
        "adoption_id": sys.argv[3],
        "deploy_ref": sys.argv[4],
        "helper_ref": sys.argv[5],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
else:
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

if [[ "$OPERATION_MODE" != "backup" && "$OPERATION_MODE" != "adopt-runtime" ]]; then
  fail "operation mode" "expected backup or adopt-runtime" 20
fi
if [[ ! "$RELEASE_BACKUP_BUCKET" =~ ^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$ ]]; then
  fail "release backup bucket" "invalid bucket name" 20
fi
if [[ ! "$BACKUP_POLICY_SHA256" =~ ^[0-9a-f]{64}$ || \
      ! "$BACKUP_LOCATION" =~ ^[A-Z0-9-]{2,40}$ || \
      ! "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  fail "release backup policy" "controller policy attestation is invalid" 20
fi
if [[ ! "$BACKUP_ID" =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9.-]{0,80}$ ]]; then
  fail "immutable backup id" "expected UTC timestamp and release label" 21
fi
if [[ ! "$COMPOSE_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  fail "Compose project" "invalid project name" 22
fi
if [[ ! "$CANDIDATE_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$CANDIDATE_ARTIFACT_GENERATION" =~ ^[1-9][0-9]*$ || \
      ! "$CANDIDATE_ARTIFACT_SIZE_BYTES" =~ ^[1-9][0-9]*$ || \
      ! "$CANDIDATE_ARTIFACT_SHA256" =~ ^[0-9a-f]{64}$ || \
      ! "$CANDIDATE_ARTIFACT_URI" =~ ^gs://[^/]+/deploy-artifacts/${CANDIDATE_REF}/repo\.tar\.gz$ ]]; then
  fail "candidate helper artifact" "exact ref, URI, and sha256 are required" 22
fi
if [[ "$SAFE_IO" != /* || ! -x "$SAFE_IO" ]]; then
  fail "safe I/O helper" "controller-owned helper is unavailable" 22
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
      ! -s "$BASE_COMPOSE" || ! -s "$SHARED_ENV" ]]; then
  fail "current runtime inputs" "exact release or shared host state missing" 25
fi
if [[ "$OPERATION_MODE" == "backup" && ! -s "$GCP_RUNTIME_COMPOSE" ]]; then
  fail "current runtime inputs" "canonical shared GCP overlay is missing" 25
fi
LIVE_GCP_RUNTIME_COMPOSE="${CURRENT_RELEASE}/infra/docker-compose.gcp.yml"
LIVE_LEGACY_IMAGE_COMPOSE="${CURRENT_RELEASE}/infra/docker-compose.aws-images.gcp.yml"
if [[ "$OPERATION_MODE" == "adopt-runtime" && \
      ( ! -s "$LIVE_GCP_RUNTIME_COMPOSE" || ! -s "$LIVE_LEGACY_IMAGE_COMPOSE" ) ]]; then
  fail "legacy runtime inputs" "reviewed live GCP/image overlays are missing" 25
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
if [[ "$OPERATION_MODE" == "backup" ]]; then
  if [[ -L "$PREDEPLOY_ATTESTATION" || \
        ( -e "$PREDEPLOY_ATTESTATION" && ! -f "$PREDEPLOY_ATTESTATION" ) ]]; then
    fail "pre-deploy backup attestation" "existing attestation path is unsafe" 26
  fi
  rm -f -- "$PREDEPLOY_ATTESTATION"
  "$SAFE_IO" fsync-dir "$SHARED_ROOT"
fi
if grep -Eq '^(GHCR_[A-Z0-9_]*(TOKEN|PASSWORD|SECRET|CREDENTIAL|AUTH|USER)|GITHUB_TOKEN|DOCKER_AUTH_CONFIG)=' "$SHARED_ENV"; then
  fail "server-owned registry credential boundary" "registry credential found in runtime env" 26
fi
"$SAFE_IO" env-validate --path "$SHARED_ENV" --forbid-prefix OMEGA_MIGRATION_ >/dev/null || \
  fail "shared runtime env" "env grammar/ownership/control boundary is invalid" 26
if ! LAKEHOUSE_BUCKET="$(
  "$SAFE_IO" lakehouse-contract --path "$SHARED_ENV"
)"; then
  fail "lakehouse bucket" "live GCP lakehouse contract is invalid" 26
fi
if [[ "$OPERATION_MODE" == "backup" ]]; then
  if ! CANONICAL_RELEASE_BACKUP_BUCKET="$(
    "$SAFE_IO" release-backup-contract --path "$SHARED_ENV" \
      --expected-bucket "$RELEASE_BACKUP_BUCKET"
  )"; then
    fail "release backup bucket" "requested backup bucket differs from live GCP runtime" 26
  fi
  emit "storage isolation" "PASS" "lakehouse=${LAKEHOUSE_BUCKET} backup=${CANONICAL_RELEASE_BACKUP_BUCKET}"
  "$SAFE_IO" gcs-release-backup-permissions \
    --bucket "$RELEASE_BACKUP_BUCKET" >/dev/null || \
    fail "release backup permissions" "VM has missing or destructive backup permissions" 26
  emit "release backup permissions" "PASS" "bucket-get/object-create/object-get allowed; list/delete/update/restore denied"
fi

WORKDIR="$(mktemp -d /tmp/omega-gcp-backup.XXXXXX)"
cleanup_early() {
  local rc=$?
  trap - EXIT
  set +e
  if [[ -n "$STATE_PREVIEW" && "$STATE_PREVIEW" == "${SHARED_ROOT}/.runtime-state."* ]]; then
    rm -f -- "$STATE_PREVIEW"
  fi
  if [[ -n "$STATE_STAGE" && "$STATE_STAGE" == "${STATE_BUNDLES_ROOT}/."* ]]; then
    rm -rf -- "$STATE_STAGE"
  fi
  if [[ -n "$STATE_FINAL" && "$STATE_FINAL" == "${STATE_BUNDLES_ROOT}/legacy-"* && \
        "$(readlink -f "$STATE_LINK" 2>/dev/null)" != "$(readlink -f "$STATE_FINAL" 2>/dev/null)" ]]; then
    rm -rf -- "$STATE_FINAL"
  fi
  if [[ -n "$WORKDIR" && "$WORKDIR" == /tmp/omega-gcp-backup.* ]]; then
    rm -rf -- "$WORKDIR"
  fi
  exit "$rc"
}
trap cleanup_early EXIT

"$SAFE_IO" gcs-download --uri "$CANDIDATE_ARTIFACT_URI" \
  --generation "$CANDIDATE_ARTIFACT_GENERATION" \
  --size "$CANDIDATE_ARTIFACT_SIZE_BYTES" \
  --sha256 "$CANDIDATE_ARTIFACT_SHA256" \
  --output "${WORKDIR}/candidate.tar.gz"
install -d -m 0700 "${WORKDIR}/candidate"
"$SAFE_IO" safe-extract --archive "${WORKDIR}/candidate.tar.gz" \
  --destination "${WORKDIR}/candidate"
RUNTIME_CONTRACT="${WORKDIR}/candidate/scripts/gcp/runtime_contract.py"
REBOOT_HELPER="${WORKDIR}/candidate/scripts/gcp/reboot-runtime.sh"
CANDIDATE_SAFE_IO="${WORKDIR}/candidate/scripts/gcp/safe_io.py"
CANDIDATE_WATCHDOG="${WORKDIR}/candidate/scripts/gcp/operation-watchdog.sh"
CANDIDATE_FIREWALL="${WORKDIR}/candidate/scripts/gcp/metadata-firewall.sh"
CANDIDATE_GUARD="${WORKDIR}/candidate/infra/terraform-gcp/templates/omega-operation-gate"
if [[ ! -x "$RUNTIME_CONTRACT" || ! -x "$REBOOT_HELPER" || \
      ! -x "$CANDIDATE_SAFE_IO" || ! -x "$CANDIDATE_WATCHDOG" || \
      ! -x "$CANDIDATE_FIREWALL" || ! -x "$CANDIDATE_GUARD" ]]; then
  fail "candidate helper artifact" "exact candidate runtime/reboot/operation helpers are missing" 26
fi
emit "candidate helper artifact" "PASS" "ref=${CANDIDATE_REF} sha256=${CANDIDATE_ARTIFACT_SHA256}"

# The candidate guard is installed before any legacy state is made visible.
# Docker therefore cannot auto-restart writers against a partial adoption.
if [[ -e "$STATE_LINK" || -L "$STATE_LINK" ]]; then
  if ! "$CANDIDATE_GUARD" && \
      ! OMEGA_GCP_LEGACY_ARTIFACT_UPGRADE=1 "$CANDIDATE_GUARD"; then
    fail "reboot operation guard" "candidate guard is incompatible with existing atomic state" 26
  fi
fi
"$CANDIDATE_FIREWALL" install
install_operation_guard "$CANDIDATE_GUARD"
install_operation_watchdog "$CANDIDATE_WATCHDOG"
emit "reboot operation guard" "PASS" "exact candidate ExecStartPre installed before state publication"

publish_private_file() {
  local source="$1" destination="$2" label="$3" temporary source_sha
  if ! python3 - "$source" "$CURRENT_RELEASE" <<'PY'
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
release = pathlib.Path(sys.argv[2]).resolve(strict=True)
try:
    info = path.lstat()
    path.resolve(strict=True).relative_to(release)
except (FileNotFoundError, RuntimeError, ValueError):
    raise SystemExit(1)
if (
    not stat.S_ISREG(info.st_mode)
    or info.st_uid != 0
    or info.st_gid != 0
    or stat.S_IMODE(info.st_mode) & 0o022
):
    raise SystemExit(1)
PY
  then
    fail "$label" "live source is linked, unowned, writable, or unconfined" 26
  fi
  source_sha="$(sha256sum "$source" | awk '{print $1}')"
  if [[ -e "$destination" || -L "$destination" ]]; then
    if [[ -L "$destination" || ! -f "$destination" || \
          "$(stat -c '%u:%g:%a' "$destination")" != "0:0:600" ]] || \
        ! cmp -s "$source" "$destination"; then
      fail "$label" "existing shared copy differs from the exact live input" 26
    fi
  else
    temporary="$(mktemp "${SHARED_ROOT}/.$(basename "$destination").XXXXXX")"
    install -m 0600 "$source" "$temporary"
    "$SAFE_IO" fsync-file "$temporary"
    mv "$temporary" "$destination"
    "$SAFE_IO" fsync-dir "$SHARED_ROOT"
  fi
  emit "$label" "PASS" "sha256=${source_sha}"
}

if [[ "$OPERATION_MODE" == "adopt-runtime" ]]; then
  # The process watchdog owns failures during preparation.  At the successful
  # controller hand-off it is replaced with a bounded marker monitor, keeping
  # startup fenced until the metadata CAS is read back and finalized remotely.
  "$WATCHDOG" arm "$$" "$OPERATION_MARKER"
  write_operation_state "metadata-cas-preparing"
  emit "startup adoption fence" "PASS" "durable marker and watchdog armed before host input publication"

  install -d -m 0700 "$ENV_BACKUPS_ROOT"
  ENV_BEFORE_SHA256="$(sha256sum "$SHARED_ENV" | awk '{print $1}')"
  ENV_BACKUP="${ENV_BACKUPS_ROOT}/${ENV_BEFORE_SHA256}.env"
  if [[ -e "$ENV_BACKUP" || -L "$ENV_BACKUP" ]]; then
    if [[ -L "$ENV_BACKUP" || ! -f "$ENV_BACKUP" || \
          "$(stat -c '%u:%g:%a' "$ENV_BACKUP")" != "0:0:600" ]] || \
        ! cmp -s "$SHARED_ENV" "$ENV_BACKUP"; then
      fail "runtime env preservation" "existing hash-addressed backup differs" 26
    fi
  else
    ENV_BACKUP_TMP="$(mktemp "${ENV_BACKUPS_ROOT}/.${ENV_BEFORE_SHA256}.XXXXXX")"
    install -m 0600 "$SHARED_ENV" "$ENV_BACKUP_TMP"
    "$SAFE_IO" fsync-file "$ENV_BACKUP_TMP"
    mv "$ENV_BACKUP_TMP" "$ENV_BACKUP"
    "$SAFE_IO" fsync-dir "$ENV_BACKUPS_ROOT"
  fi
  printf '%s' "$RELEASE_BACKUP_BUCKET" | \
    "$SAFE_IO" env-set --path "$SHARED_ENV" --key RELEASE_BACKUP_BUCKET
  "$SAFE_IO" env-validate --path "$SHARED_ENV" --forbid-prefix OMEGA_MIGRATION_ >/dev/null || \
    fail "shared runtime env" "adopted env failed exact grammar/ownership validation" 26
  CANONICAL_RELEASE_BACKUP_BUCKET="$($SAFE_IO release-backup-contract \
    --path "$SHARED_ENV" --expected-bucket "$RELEASE_BACKUP_BUCKET")" || \
    fail "release backup bucket" "adopted env storage contract differs" 26
  ENV_AFTER_SHA256="$(sha256sum "$SHARED_ENV" | awk '{print $1}')"
  emit "runtime env preservation" "PASS" "before_sha256=${ENV_BEFORE_SHA256} after_sha256=${ENV_AFTER_SHA256} backup=hash-addressed-private"

  publish_private_file "$LIVE_GCP_RUNTIME_COMPOSE" "$GCP_RUNTIME_COMPOSE" \
    "canonical GCP runtime overlay"
  publish_private_file "$LIVE_LEGACY_IMAGE_COMPOSE" "$LEGACY_IMAGE_COMPOSE" \
    "legacy immutable image overlay"
  "$SAFE_IO" gcs-release-backup-permissions --bucket "$RELEASE_BACKUP_BUCKET" \
    >/dev/null || fail "release backup permissions" \
      "VM has missing or destructive backup permissions" 26
  emit "release backup permissions" "PASS" "bucket-get/object-create/object-get allowed; destructive permissions denied"
fi

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
  if /usr/local/sbin/omega-operation-gate; then
    : # Current state already records generation, size, and SHA-256.
  elif OMEGA_GCP_LEGACY_ARTIFACT_UPGRADE=1 /usr/local/sbin/omega-operation-gate; then
    ADOPTION_NEEDED=1
    emit "legacy artifact provenance" "PASS" "reviewed pre-generation state accepted only for one fail-closed upgrade"
  else
    fail "canonical runtime state" "operation gate rejected existing state/helper provenance" 26
  fi
else
  ADOPTION_NEEDED=1
fi

if [[ "$OPERATION_MODE" == "backup" && "$ADOPTION_NEEDED" == "1" ]]; then
  fail "legacy runtime adoption" "run the coordinated startup/runtime adoption before backup" 26
fi

if [[ "$ADOPTION_NEEDED" == "1" ]]; then
  install -d -m 0700 "$STATE_BUNDLES_ROOT" "${SHARED_ROOT}/bin"

  # Stage immutable, ref-addressed helpers. The directory is published before
  # state, but remains inert until the atomic state link references its hashes.
  HELPER_ROOT="${SHARED_ROOT}/bin/${CANDIDATE_REF}"
  HELPER_TMP="${SHARED_ROOT}/bin/.${CANDIDATE_REF}.$$"
  REBOOT_SHA256="$(sha256sum "$REBOOT_HELPER" | awk '{print $1}')"
  CONTRACT_SHA256="$(sha256sum "$RUNTIME_CONTRACT" | awk '{print $1}')"
  SAFE_IO_SHA256="$(sha256sum "$CANDIDATE_SAFE_IO" | awk '{print $1}')"
  if [[ -e "$HELPER_ROOT" ]]; then
    if [[ ! -x "${HELPER_ROOT}/reboot-runtime.sh" || \
          ! -x "${HELPER_ROOT}/runtime_contract.py" || \
          ! -x "${HELPER_ROOT}/safe_io.py" || \
          "$(sha256sum "${HELPER_ROOT}/reboot-runtime.sh" | awk '{print $1}')" != "$REBOOT_SHA256" || \
          "$(sha256sum "${HELPER_ROOT}/runtime_contract.py" | awk '{print $1}')" != "$CONTRACT_SHA256" || \
          "$(sha256sum "${HELPER_ROOT}/safe_io.py" | awk '{print $1}')" != "$SAFE_IO_SHA256" ]]; then
      fail "shared reboot helper bundle" "existing candidate-ref helper bundle differs" 26
    fi
  else
    install -d -m 0700 "$HELPER_TMP"
    install -m 0755 "$REBOOT_HELPER" "${HELPER_TMP}/reboot-runtime.sh"
    install -m 0755 "$RUNTIME_CONTRACT" "${HELPER_TMP}/runtime_contract.py"
    install -m 0755 "$CANDIDATE_SAFE_IO" "${HELPER_TMP}/safe_io.py"
    python3 - "${HELPER_TMP}/source.json" "$CANDIDATE_REF" \
      "$CANDIDATE_ARTIFACT_URI" "$CANDIDATE_ARTIFACT_GENERATION" \
      "$CANDIDATE_ARTIFACT_SIZE_BYTES" "$CANDIDATE_ARTIFACT_SHA256" \
      "$REBOOT_SHA256" "$CONTRACT_SHA256" "$SAFE_IO_SHA256" <<'PY'
import json
import pathlib
import sys

payload = {
    "schema_version": 1,
    "source_ref": sys.argv[2],
    "source_artifact_uri": sys.argv[3],
    "source_artifact_generation": sys.argv[4],
    "source_artifact_size_bytes": int(sys.argv[5]),
    "source_artifact_sha256": sys.argv[6],
    "reboot_runtime_sha256": sys.argv[7],
    "runtime_contract_sha256": sys.argv[8],
    "safe_io_sha256": sys.argv[9],
}
pathlib.Path(sys.argv[1]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
    chmod 0400 "${HELPER_TMP}/source.json"
    "$SAFE_IO" fsync-file "${HELPER_TMP}/reboot-runtime.sh" \
      "${HELPER_TMP}/runtime_contract.py" "${HELPER_TMP}/safe_io.py" \
      "${HELPER_TMP}/source.json"
    "$SAFE_IO" fsync-dir "$HELPER_TMP"
    mv "$HELPER_TMP" "$HELPER_ROOT"
    "$SAFE_IO" fsync-dir "${SHARED_ROOT}/bin"
  fi
  emit "shared reboot helper bundle" "PASS" "candidate_ref=${CANDIDATE_REF} exact hashes recorded"

  STATE_STAGE="$(mktemp -d "${STATE_BUNDLES_ROOT}/.legacy.${CURRENT_REF}.XXXXXX")"
  ACTIVE_PROVENANCE="${STATE_STAGE}/runtime-provenance.json"
  python3 "$RUNTIME_CONTRACT" bootstrap-record --compose-project "$COMPOSE_PROJECT" \
    --deploy-ref "$CURRENT_REF" --version "$CURRENT_VERSION" \
    --runtime-input "shared_env=${SHARED_ENV}" \
    --runtime-input "base_compose=${BASE_COMPOSE}" \
    --runtime-input "gcp_compose=${GCP_RUNTIME_COMPOSE}" \
    --runtime-input "legacy_image_compose=${LEGACY_IMAGE_COMPOSE}" \
    --output "$ACTIVE_PROVENANCE"
  python3 - "$ACTIVE_PROVENANCE" "${STATE_STAGE}/bootstrap-state.json" \
    "$CURRENT_REF" "$CURRENT_VERSION" "$CANDIDATE_REF" \
    "$CANDIDATE_ARTIFACT_URI" "$CANDIDATE_ARTIFACT_GENERATION" \
    "$CANDIDATE_ARTIFACT_SIZE_BYTES" "$CANDIDATE_ARTIFACT_SHA256" \
    "$REBOOT_SHA256" "$CONTRACT_SHA256" "$SAFE_IO_SHA256" <<'PY'
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
    "candidate_artifact_generation": sys.argv[7],
    "candidate_artifact_size_bytes": int(sys.argv[8]),
    "candidate_artifact_sha256": sys.argv[9],
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
        "source_artifact_generation": sys.argv[7],
        "source_artifact_size_bytes": int(sys.argv[8]),
        "source_artifact_sha256": sys.argv[9],
        "reboot_runtime_sha256": sys.argv[10],
        "runtime_contract_sha256": sys.argv[11],
        "safe_io_sha256": sys.argv[12],
    },
    "completed_at": datetime.now(timezone.utc).isoformat(),
}
bootstrap_path.write_text(
    json.dumps(bootstrap, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
  chmod 0600 "$ACTIVE_PROVENANCE" "${STATE_STAGE}/bootstrap-state.json"
  "$SAFE_IO" fsync-file "$ACTIVE_PROVENANCE" "${STATE_STAGE}/bootstrap-state.json"
  "$SAFE_IO" fsync-dir "$STATE_STAGE"
fi

PROVENANCE_MODE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["mode"])' "$ACTIVE_PROVENANCE")"
PROVENANCE_ARGS=()
RUNTIME_INPUT_ARGS=(
  --runtime-input "shared_env=${SHARED_ENV}"
  --runtime-input "base_compose=${BASE_COMPOSE}"
  --runtime-input "gcp_compose=${GCP_RUNTIME_COMPOSE}"
)
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
  RUNTIME_INPUT_ARGS+=(--runtime-input "release_compose=${RELEASE_COMPOSE}")
elif [[ "$PROVENANCE_MODE" == "bootstrap" ]]; then
  if python3 - "$ACTIVE_PROVENANCE" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(
    0 if "legacy_image_compose" in payload.get("runtime_input_sha256", {}) else 1
)
PY
  then
    if [[ ! -s "$LEGACY_IMAGE_COMPOSE" ]]; then
      fail "bootstrap runtime inputs" "recorded legacy image overlay is missing" 25
    fi
    COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
      -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" -f "$LEGACY_IMAGE_COMPOSE" --profile sap)
    RUNTIME_INPUT_ARGS+=(--runtime-input "legacy_image_compose=${LEGACY_IMAGE_COMPOSE}")
  else
    COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
      -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" --profile sap)
  fi
else
  fail "runtime provenance" "unsupported provenance mode" 25
fi
"${COMPOSE[@]}" config -q
emit "shared runtime inputs" "PASS" "host-owned env and generated GCP overlay are canonical"

if ! READINESS_MODE="$(python3 - "$CURRENT_REF" "$CURRENT_VERSION" <<'PY'
import json
import sys
import urllib.error
import urllib.request

current_ref, current_version = sys.argv[1:]
payloads = {}
for path in ("/healthz", "/readyz"):
    with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=10) as response:
        if response.status != 200:
            raise SystemExit(1)
        payloads[path] = json.load(response)
health = payloads["/healthz"]
if health.get("version") != current_version or health.get("app_env") != "production":
    raise SystemExit(1)
if health.get("ok") is not True and health.get("status") not in {"ok", "healthy"}:
    raise SystemExit(1)
ready = payloads["/readyz"]
if ready.get("ok") is not True and ready.get("status") not in {"ok", "ready"}:
    raise SystemExit(1)

try:
    with urllib.request.urlopen(
        "http://127.0.0.1:8000/readyz?require_data=1", timeout=10
    ) as response:
        if response.status != 200:
            raise SystemExit(1)
        data_ready = json.load(response)
    if data_ready.get("ok") is not True:
        raise SystemExit(1)
    print("green")
except urllib.error.HTTPError as exc:
    try:
        body = json.load(exc)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SystemExit(1)
    legacy_identity = (
        current_ref == "6b12883c5b5ea0537120279ccbee4947137998a2"
        and current_version == "1.45.205-beta"
    )
    if (
        not legacy_identity
        or exc.code != 503
        or body != {"ok": False, "service": "console"}
    ):
        raise SystemExit(1)
    print("legacy-gold-permission-exception")
PY
)"; then
  fail "live release coherence" "health/version or readyz/data gate differs from current release" 27
fi
if ! python3 "$RUNTIME_CONTRACT" provenance --provenance "$ACTIVE_PROVENANCE" \
    --compose-project "$COMPOSE_PROJECT" --deploy-ref "$CURRENT_REF" \
    --version "$CURRENT_VERSION" --one-shots "${RUNTIME_INPUT_ARGS[@]}" \
    "${PROVENANCE_ARGS[@]}" >/dev/null; then
  fail "live runtime provenance" "containers, images, project, health, or scheduler drifted" 27
fi
emit "live release coherence" "PASS" "healthz.version=${CURRENT_VERSION} readyz=200 data_readiness_mode=${READINESS_MODE} runtime provenance exact scheduler=1"
"$CANDIDATE_FIREWALL" verify-container >/dev/null
emit "container metadata isolation" "PASS" "IPv4/IPv6 metadata endpoints denied from running proprietary container"

if [[ "$ADOPTION_NEEDED" == "1" ]]; then
  STATE_FINAL="${STATE_BUNDLES_ROOT}/legacy-${CURRENT_REF}-by-${CANDIDATE_REF}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  mv "$STATE_STAGE" "$STATE_FINAL"
  "$SAFE_IO" fsync-dir "$STATE_BUNDLES_ROOT"
  STATE_STAGE=""
  STATE_PREVIEW="${SHARED_ROOT}/.runtime-state.${CURRENT_REF}.$$"
  ln -s "$STATE_FINAL" "$STATE_PREVIEW"
  if ! OMEGA_GCP_ALLOW_OPERATION_MARKER=1 \
      OMEGA_GCP_STATE_LINK_OVERRIDE="$STATE_PREVIEW" \
      /usr/local/sbin/omega-operation-gate; then
    fail "legacy runtime adoption" "staged atomic state/helper verification failed" 27
  fi
  mv -Tf "$STATE_PREVIEW" "$STATE_LINK"
  STATE_PREVIEW=""
  STATE_FINAL=""
  "$SAFE_IO" fsync-dir "$SHARED_ROOT"
  ACTIVE_PROVENANCE="$RUNTIME_PROVENANCE"
  emit "legacy runtime adoption" "PASS" "current=${CURRENT_REF} candidate_helper=${CANDIDATE_REF} state_pair=atomic"
fi

if [[ "$OPERATION_MODE" == "adopt-runtime" ]]; then
  if ! OMEGA_GCP_ALLOW_OPERATION_MARKER=1 /usr/local/sbin/omega-operation-gate; then
    fail "legacy runtime adoption" "published runtime state failed exact guard read-back" 27
  fi
  write_operation_state "metadata-cas-pending"
  "$WATCHDOG" arm-hold 3600 "$OPERATION_MARKER"
  emit "coordinated runtime adoption" "PASS" "runtime-state, host inputs, reboot guard, durable CAS marker, and bounded watchdog are active"
  printf 'OMEGA_GCP_BACKUP_JSON={"status":"PASS","operation":"adopt-runtime","adoption_id":"%s","current_ref":"%s","helper_ref":"%s","metadata_cas_pending":true,"secrets_included":false}\n' \
    "$BACKUP_ID" "$CURRENT_REF" "$CANDIDATE_REF"
  exit 0
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

if ! OPERATIONAL_DATA_ATTESTATION="$(docker exec mode_postgres \
    psql -v ON_ERROR_STOP=1 -At -F $'\t' -U postgres -d modecissions -p 5432 \
    -c "SELECT (NOT pg_is_in_recovery())::int, current_setting('default_transaction_read_only'), (to_regclass('public.control_room_items') IS NOT NULL)::int, (to_regclass('public.workspaces') IS NOT NULL)::int, (SELECT count(*) FROM control_room_items WHERE COALESCE(item_kind, '') <> 'source_state'), COALESCE((SELECT tenant_id::text FROM workspaces ORDER BY created_at ASC NULLS LAST,id ASC LIMIT 1),''), COALESCE((SELECT id::text FROM workspaces ORDER BY created_at ASC NULLS LAST,id ASC LIMIT 1),'');" | tr -d '\r')"; then
  fail "direct operational DB attestation" "canonical SQL query failed" 27
fi
IFS=$'\t' read -r OP_PRIMARY OP_READONLY OP_ITEMS_TABLE OP_WORKSPACES_TABLE \
  OPERATIONAL_ITEMS ATTEST_TENANT ATTEST_WORKSPACE <<<"$OPERATIONAL_DATA_ATTESTATION"
if [[ "$OP_PRIMARY" != "1" || "$OP_READONLY" != "off" || \
      "$OP_ITEMS_TABLE" != "1" || "$OP_WORKSPACES_TABLE" != "1" || \
      ! "$OPERATIONAL_ITEMS" =~ ^[0-9]+$ || \
      ! "$ATTEST_TENANT" =~ ^[0-9a-fA-F-]{36}$ || \
      ! "$ATTEST_WORKSPACE" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  fail "direct operational DB attestation" "identity, writer state, schema, or scope differs" 27
fi
if ! GOLD_DATA_ATTESTATION="$(docker exec mode_postgres_gold \
    psql -v ON_ERROR_STOP=1 -At -F $'\t' -U postgres -d modecissions_gold -p 5433 \
    -c "WITH scope AS (SELECT set_config('app.tenant_id','${ATTEST_TENANT}',true), set_config('app.workspace_id','${ATTEST_WORKSPACE}',true)), counts AS (SELECT count(*) FILTER (WHERE h.layer='gold') AS gold_tables, COALESCE(sum(r.row_count) FILTER (WHERE h.layer='gold'),0) AS gold_rows, COALESCE(sum(r.row_count) FILTER (WHERE h.layer='silver'),0) AS silver_rows FROM omega_publication.dataset_publication_heads h JOIN omega_publication.materialization_runs r ON r.materialization_run_id=h.materialization_run_id, scope WHERE h.tenant_id='${ATTEST_TENANT}'::uuid AND h.workspace_id='${ATTEST_WORKSPACE}'::uuid AND r.status IN ('published','legacy_unverified')), head_relations AS (SELECT DISTINCT m.relation_name FROM omega_publication.dataset_publication_heads h JOIN omega_publication.dataset_gold_relations m ON m.tenant_id=h.tenant_id AND m.workspace_id=h.workspace_id AND m.dataset=h.dataset WHERE h.layer='gold'), head_security AS (SELECT count(*) AS referenced_relations, count(*) FILTER (WHERE c.oid IS NOT NULL AND c.relkind IN ('r','p') AND c.relrowsecurity AND c.relforcerowsecurity AND has_table_privilege('omega_refinement_gold',c.oid,'SELECT')) AS secured_relations, count(*) FILTER (WHERE c.oid IS NULL) AS missing_relations FROM head_relations hr LEFT JOIN pg_class c ON c.oid=to_regclass(format('%I.%I','public',hr.relation_name))), stale_exposure AS (SELECT count(*) AS exposed_relations FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname LIKE 'gold\\_%' ESCAPE '\\' AND c.relkind IN ('r','p') AND NOT EXISTS (SELECT 1 FROM head_relations hr WHERE hr.relation_name=c.relname) AND has_table_privilege('omega_refinement_gold',c.oid,'SELECT')) SELECT (NOT pg_is_in_recovery())::int, current_setting('default_transaction_read_only'), (to_regclass('omega_publication.dataset_publication_heads') IS NOT NULL)::int, (to_regclass('omega_publication.materialization_runs') IS NOT NULL)::int, gold_tables, gold_rows, silver_rows, referenced_relations, secured_relations, missing_relations, exposed_relations FROM counts CROSS JOIN head_security CROSS JOIN stale_exposure;" | tr -d '\r')"; then
  fail "direct Gold DB attestation" "canonical scoped SQL query failed" 27
fi
IFS=$'\t' read -r GOLD_PRIMARY GOLD_READONLY GOLD_HEADS_TABLE GOLD_RUNS_TABLE \
  GOLD_TABLES GOLD_ROWS SILVER_ROWS GOLD_REFERENCED_RELATIONS \
  GOLD_SECURED_RELATIONS GOLD_MISSING_RELATIONS GOLD_STALE_EXPOSED_RELATIONS \
  <<<"$GOLD_DATA_ATTESTATION"
if [[ "$GOLD_PRIMARY" != "1" || "$GOLD_READONLY" != "off" || \
      "$GOLD_HEADS_TABLE" != "1" || "$GOLD_RUNS_TABLE" != "1" || \
      ! "$GOLD_TABLES" =~ ^[0-9]+$ || ! "$GOLD_ROWS" =~ ^[0-9]+$ || \
      ! "$SILVER_ROWS" =~ ^[0-9]+$ || \
      ! "$GOLD_REFERENCED_RELATIONS" =~ ^[1-9][0-9]*$ || \
      "$GOLD_SECURED_RELATIONS" != "$GOLD_REFERENCED_RELATIONS" || \
      "$GOLD_MISSING_RELATIONS" != "0" || \
      "$GOLD_STALE_EXPOSED_RELATIONS" != "0" || \
      ( "$READINESS_MODE" == "legacy-gold-permission-exception" && \
        "$GOLD_REFERENCED_RELATIONS" != "57" ) || \
      $((OPERATIONAL_ITEMS + GOLD_ROWS + SILVER_ROWS)) -le 0 ]]; then
  fail "direct Gold DB attestation" "primary state, publication schema, head relation security, or canonical data evidence differs" 27
fi
emit "direct DB and Gold attestation" "PASS" \
  "operational_items=${OPERATIONAL_ITEMS} gold_tables=${GOLD_TABLES} gold_rows=${GOLD_ROWS} silver_rows=${SILVER_ROWS} head_relations=${GOLD_SECURED_RELATIONS}/${GOLD_REFERENCED_RELATIONS} stale_reader_exposure=${GOLD_STALE_EXPOSED_RELATIONS} primary_writer=true"

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

ensure_fail_closed_runtime() {
  local safe=1
  if ! write_operation_state "restore-failed"; then
    safe=0
  fi
  if ! "${COMPOSE[@]}" stop --timeout 30 "${WRITER_SERVICES[@]}" >/dev/null 2>&1; then
    safe=0
  fi
  if ! python3 "$RUNTIME_CONTRACT" writer-fence --compose-project "$COMPOSE_PROJECT" >/dev/null 2>&1; then
    safe=0
  fi
  DB_FENCE_ACTIVE=1
  RUNTIME_FENCED=1
  if ! database_fence on; then
    safe=0
  fi
  if [[ "$safe" == "1" ]]; then
    emit "fail-closed backup recovery" "PASS" "all writers stopped; both databases persistently read-only; durable marker retained"
    return 0
  fi
  emit "fail-closed backup recovery" "FAIL" "manual recovery required; one or more stop/fence/marker checks failed"
  return 1
}

restore_runtime() {
  local rc=$? restored=1
  trap - EXIT
  if [[ "$RUNTIME_FENCED" == "1" ]]; then
    if [[ "$DB_FENCE_ACTIVE" == "1" ]] && ! database_fence off; then
      emit "database write fence release" "FAIL" "persistent database fence could not be safely reset; runtime remains stopped"
      rc=72
      restored=0
    elif [[ "${#RUNNING_BEFORE[@]}" -gt 0 ]] && \
        ! "${COMPOSE[@]}" start "${RUNNING_BEFORE[@]}" >/dev/null; then
      emit "runtime restored after backup" "FAIL" "one or more previously-running services did not restart"
      rc=70
      restored=0
    elif ! wait_for_runtime_restore; then
      emit "runtime restored after backup" "FAIL" "not every previously-active service returned running/healthy"
      rc=71
      restored=0
    fi
    if [[ "$restored" == "1" ]]; then
      if ! write_operation_state "restored" || ! rm -f -- "$OPERATION_MARKER" || \
          ! "$SAFE_IO" fsync-dir "$SHARED_ROOT"; then
        emit "runtime restored after backup" "FAIL" "durable restored-state publication failed"
        rc=73
        restored=0
      fi
    fi
    if [[ "$restored" == "1" ]]; then
      "$WATCHDOG" disarm "$$" "$OPERATION_MARKER"
      DB_FENCE_ACTIVE=0
      RUNTIME_FENCED=0
      emit "runtime restored after backup" "PASS" "all previously-active services running/healthy; scheduler=1"
    else
      if ! ensure_fail_closed_runtime; then
        rc=74
      fi
    fi
  fi
  if [[ -n "$WORKDIR" && "$WORKDIR" == /tmp/omega-gcp-backup.* ]]; then
    rm -rf -- "$WORKDIR"
  fi
  exit "$rc"
}
trap restore_runtime EXIT

"$WATCHDOG" arm "$$" "$OPERATION_MARKER"
write_operation_state "fencing"
RUNTIME_FENCED=1
emit "independent operation watchdog" "PASS" "systemd monitor armed before durable marker and writer mutation"
"${COMPOSE[@]}" stop --timeout 60 "${WRITER_SERVICES[@]}"
python3 "$RUNTIME_CONTRACT" writer-fence --compose-project "$COMPOSE_PROJECT" >/dev/null || \
  fail "writer fence" "a global labeled or unlabeled proprietary writer remains running" 28
DB_FENCE_ACTIVE=1
database_fence on || fail "database write fence" "cannot persist read-only defaults" 29
write_operation_state "fenced"
emit "writer and scheduler fence" "PASS" "all global application writers stopped; databases persistently read-only"

# pg_dumpall necessarily serializes the temporary catalog fence. Preserve the
# exact pre-fence database policy independently so a restore can remain fenced
# through verification and reapply the original policy only at cutover.
python3 - "${WORKDIR}/database-restore-settings.json" \
  "$DB_CONN_LIMIT_MODECISSIONS" "$DB_READONLY_MODECISSIONS" \
  "$DB_READONLY_CONFIG_MODECISSIONS" "$DB_CONN_LIMIT_GOLD" \
  "$DB_READONLY_GOLD" "$DB_READONLY_CONFIG_GOLD" <<'PY'
import json
import pathlib
import re
import sys

output = pathlib.Path(sys.argv[1])
values = sys.argv[2:]
if len(values) != 6:
    raise SystemExit("pre-fence database setting inventory is incomplete")


def setting(offset: int) -> dict[str, object]:
    connection_limit, effective_read_only, database_config = values[offset : offset + 3]
    if re.fullmatch(r"-1|[0-9]+", connection_limit) is None:
        raise SystemExit("pre-fence connection limit is invalid")
    if effective_read_only not in {"on", "off"}:
        raise SystemExit("pre-fence effective read-only state is invalid")
    if database_config not in {"absent", "on", "off"}:
        raise SystemExit("pre-fence database read-only config is invalid")
    expected_effective = "on" if database_config == "on" else "off"
    if effective_read_only != expected_effective:
        raise SystemExit(
            "effective pre-fence read-only state has an unrecorded override"
        )
    return {
        "connection_limit": int(connection_limit),
        "database_default_transaction_read_only": database_config,
        "effective_default_transaction_read_only": effective_read_only,
    }


payload = {
    "schema_version": 1,
    "captured_before_fence": True,
    "restore_policy": "keep-fenced-until-explicit-cutover",
    "databases": {
        "modecissions": setting(0),
        "modecissions_gold": setting(3),
    },
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
DATABASE_RESTORE_SETTINGS_SHA256="$(sha256sum "${WORKDIR}/database-restore-settings.json" | awk '{print $1}')"
emit "pre-fence database restore policy" "PASS" "sha256=${DATABASE_RESTORE_SETTINGS_SHA256}; sealed for cutover"

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

python3 - "$LAKEHOUSE_BUCKET" "$BACKUP_PREFIX" "${WORKDIR}/lakehouse_objects.jsonl" \
  "${WORKDIR}/bucket.json" "$BACKUP_LOCATION" <<'PY'
import concurrent.futures
import hashlib
import json
import os
import pathlib
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

bucket, excluded_prefix, output_name, bucket_output, expected_location = sys.argv[1:]
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
    bucket_payload = json.load(response)
uniform = bucket_payload.get("iamConfiguration", {}).get(
    "uniformBucketLevelAccess", {}
)
soft_delete = bucket_payload.get("softDeletePolicy", {})
expected_lifecycle = [
    {"action": {"storageClass": "NEARLINE", "type": "SetStorageClass"}, "condition": {"age": 30}},
    {"action": {"type": "Delete"}, "condition": {"isLive": False, "numNewerVersions": 5}},
]
actual_lifecycle = bucket_payload.get("lifecycle", {}).get("rule", [])
if (
    bucket_payload.get("name") != bucket
    or bucket_payload.get("location") != expected_location
    or re.fullmatch(r"[1-9][0-9]*", str(bucket_payload.get("metageneration", "")))
    is None
    or bucket_payload.get("versioning", {}).get("enabled") is not True
    or uniform.get("enabled") is not True
    or bucket_payload.get("iamConfiguration", {}).get("publicAccessPrevention")
    != "enforced"
    or int(soft_delete.get("retentionDurationSeconds", 0)) != 604800
    or bucket_payload.get("retentionPolicy") not in (None, {})
    or sorted(actual_lifecycle, key=lambda value: json.dumps(value, sort_keys=True))
    != sorted(expected_lifecycle, key=lambda value: json.dumps(value, sort_keys=True))
):
    raise SystemExit("effective live GCS bucket policy differs from the reviewed contract")
bucket_policy = {
    "bucket": bucket,
    "location": expected_location,
    "metageneration": str(bucket_payload["metageneration"]),
    "versioning_enabled": True,
    "uniform_bucket_level_access": True,
    "public_access_prevention": "enforced",
    "soft_delete_seconds": 604800,
    "retention_policy": "absent",
    "lifecycle_rules": expected_lifecycle,
}
bucket_policy["policy_sha256"] = hashlib.sha256(
    json.dumps(bucket_policy, separators=(",", ":"), sort_keys=True).encode()
).hexdigest()
pathlib.Path(bucket_output).write_text(
    json.dumps(bucket_policy, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)

fields = "nextPageToken,items(name,size,generation,metageneration,md5Hash,crc32c,etag,updated,storageClass,temporaryHold,eventBasedHold)"
object_fields = "name,size,generation,metageneration,md5Hash,crc32c,etag,updated,storageClass,temporaryHold,eventBasedHold"

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
                "temporaryHold": item.get("temporaryHold") is True,
                "eventBasedHold": item.get("eventBasedHold") is True,
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

# Pin every exact live generation before any writer is allowed to resume. The
# bounded pool avoids turning a large lakehouse into hours of serial outage;
# executor.map preserves the stable key order in the resulting manifest. CAS
# preconditions and exact readback remain per generation. A partial failure
# intentionally leaves the runtime/DB fence and completed holds in place.
rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
if len(rows) != count:
    raise SystemExit("recorded GCS generation inventory changed on local readback")

token_lock = threading.Lock()
access_token_value = token


def refresh_access_token(force=False):
    global access_token_value
    with token_lock:
        if not force and access_token_value:
            return access_token_value
        request = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/"
            "service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            value = json.load(response).get("access_token")
        if not isinstance(value, str) or not value:
            raise RuntimeError("metadata token refresh failed")
        access_token_value = value
        return value


def api_json(url, *, method="GET", data=None, attempts=4):
    delay = 0.25
    for attempt in range(attempts):
        request_headers = {"Authorization": f"Bearer {refresh_access_token()}"}
        if data is not None:
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(
            url, data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt + 1 < attempts:
                refresh_access_token(force=True)
            elif exc.code in {408, 429, 500, 502, 503, 504} and attempt + 1 < attempts:
                pass
            else:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt + 1 >= attempts:
                raise
        time.sleep(delay)
        delay = min(delay * 2, 2.0)
    raise RuntimeError("bounded GCS request retries exhausted")


def exact_readback(row, metageneration=None):
    params = {"generation": row["generation"], "fields": object_fields}
    if metageneration is not None:
        params["ifMetagenerationMatch"] = metageneration
    key = urllib.parse.quote(row["key"], safe="")
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{quoted_bucket}/o/{key}?"
        f"{urllib.parse.urlencode(params)}"
    )
    return api_json(url)


def validate_held(row, actual):
    actual_meta = str(actual.get("metageneration", ""))
    if (
        str(actual.get("name", "")) != row["key"]
        or str(actual.get("generation", "")) != row["generation"]
        or re.fullmatch(r"[1-9][0-9]*", actual_meta) is None
        or int(actual.get("size", -1)) != row["size_bytes"]
        or actual.get("crc32c") != row.get("crc32c")
        or actual.get("md5Hash") != row.get("md5")
        or actual.get("temporaryHold") is not True
    ):
        raise RuntimeError("GCS temporary hold exact-generation readback differs")
    result = dict(row)
    result.update(
        {
            "metageneration": actual_meta,
            "temporaryHold": True,
            "eventBasedHold": actual.get("eventBasedHold") is True,
            "etag": actual.get("etag"),
            "updated": actual.get("updated"),
            "storage_class": actual.get("storageClass"),
        }
    )
    return result


def hold_generation(row):
    generation = str(row.get("generation", ""))
    original_meta = str(row.get("metageneration", ""))
    if (
        re.fullmatch(r"[1-9][0-9]*", generation) is None
        or re.fullmatch(r"[1-9][0-9]*", original_meta) is None
    ):
        raise RuntimeError("recorded GCS generation metadata is invalid")
    if row.get("temporaryHold") is True:
        return validate_held(row, exact_readback(row, original_meta))

    key = urllib.parse.quote(row["key"], safe="")
    params = urllib.parse.urlencode(
        {
            "generation": generation,
            "ifMetagenerationMatch": original_meta,
            "fields": object_fields,
        }
    )
    url = f"https://storage.googleapis.com/storage/v1/b/{quoted_bucket}/o/{key}?{params}"
    body = json.dumps({"temporaryHold": True}).encode()
    try:
        patched = api_json(url, method="PATCH", data=body)
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
        # A lost PATCH response is ambiguous.  Accept only an exact generation
        # that is now held; otherwise fail rather than retrying against drift.
        actual = exact_readback(row)
        if actual.get("temporaryHold") is not True:
            raise
        return validate_held(row, actual)
    patched_meta = str(patched.get("metageneration", ""))
    if (
        str(patched.get("name", "")) != row["key"]
        or str(patched.get("generation", "")) != generation
        or re.fullmatch(r"[1-9][0-9]*", patched_meta) is None
        or patched.get("temporaryHold") is not True
    ):
        raise RuntimeError("GCS temporary hold patch response is incomplete")
    return validate_held(row, exact_readback(row, patched_meta))


held_output = output.with_name(f".{output.name}.held")
held_count = 0
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as executor:
        held_rows = executor.map(hold_generation, rows)
        with held_output.open("w", encoding="utf-8") as destination:
            for held_row in held_rows:
                destination.write(
                    json.dumps(held_row, separators=(",", ":"), sort_keys=True)
                    + "\n"
                )
                held_count += 1
            destination.flush()
            os.fsync(destination.fileno())
    if held_count != count:
        raise SystemExit("not every recorded GCS generation received a temporary hold")
    os.replace(held_output, output)
finally:
    if held_output.exists():
        held_output.unlink()

held_hash = hashlib.sha256(output.read_bytes()).hexdigest()
print(json.dumps({"object_count": count, "total_bytes": total_bytes, "sha256": held_hash, "temporarily_held": held_count}))
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
emit "versioned object manifest" "PASS" "objects=${OBJECT_COUNT} bytes=${OBJECT_BYTES} sha256=${OBJECT_SHA} stable_passes=2 temporarily_held=${OBJECT_COUNT}"

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

upload_backup_generation() {
  local source="$1" key="$2" metadata_output="$3"
  python3 - "$source" "$RELEASE_BACKUP_BUCKET" "$key" "$metadata_output" <<'PY'
import http.client
import hashlib
import json
import pathlib
import re
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
generation = str(payload.get("generation", ""))
if (
    re.fullmatch(r"[1-9][0-9]*", generation) is None
    or not payload.get("crc32c")
    or int(payload.get("size", -1)) != path.stat().st_size
):
    raise SystemExit("uploaded object metadata is incomplete")
media_url = (
    "https://storage.googleapis.com/download/storage/v1/b/"
    f"{urllib.parse.quote(bucket, safe='')}/o/{urllib.parse.quote(key, safe='')}?"
    + urllib.parse.urlencode({"alt": "media", "generation": generation})
)
request = urllib.request.Request(media_url, headers={"Authorization": f"Bearer {token}"})
digest = hashlib.sha256()
readback_size = 0
with urllib.request.urlopen(request, timeout=180) as response:
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        readback_size += len(chunk)
        digest.update(chunk)
local_hash = hashlib.sha256()
with path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        local_hash.update(chunk)
local_digest = local_hash.hexdigest()
if readback_size != path.stat().st_size or digest.hexdigest() != local_digest:
    raise SystemExit("uploaded object exact-generation readback differs")
pathlib.Path(metadata_output).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
}

upload_backup_generation "${WORKDIR}/postgres.sql.gz" "${BACKUP_PREFIX}/postgres.sql.gz" "${WORKDIR}/postgres.meta.json"
upload_backup_generation "${WORKDIR}/postgres_gold.sql.gz" "${BACKUP_PREFIX}/postgres_gold.sql.gz" "${WORKDIR}/postgres_gold.meta.json"
upload_backup_generation "${WORKDIR}/lakehouse_objects.jsonl" "${BACKUP_PREFIX}/lakehouse_objects.jsonl" "${WORKDIR}/objects.meta.json"
upload_backup_generation "${WORKDIR}/runtime-images.json" "${BACKUP_PREFIX}/runtime-images.json" "${WORKDIR}/images.meta.json"
upload_backup_generation "${WORKDIR}/runtime-provenance.json" "${BACKUP_PREFIX}/runtime-provenance.json" "${WORKDIR}/provenance.meta.json"

# Re-read every exact live generation immediately before sealing the backup
# manifest. The manifest cannot report success if a hold was removed or any
# metadata generation changed after the CAS above.
if ! HOLD_RESULT="$(python3 - "$LAKEHOUSE_BUCKET" "${WORKDIR}/lakehouse_objects.jsonl" <<'PY'
import concurrent.futures
import json
import re
import sys
import urllib.parse
import urllib.request

bucket, manifest_path = sys.argv[1:]
rows = []
last_key = ""
with open(manifest_path, encoding="utf-8") as stream:
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
            raise SystemExit("held GCS generation manifest is invalid")
        rows.append(row)
        last_key = key
if not rows:
    raise SystemExit("held GCS generation manifest is empty")

token_request = urllib.request.Request(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
)
with urllib.request.urlopen(token_request, timeout=10) as response:
    token = json.load(response)["access_token"]
headers = {"Authorization": f"Bearer {token}"}
quoted_bucket = urllib.parse.quote(bucket, safe="")


def verify_hold(row):
    key = urllib.parse.quote(row["key"], safe="")
    params = urllib.parse.urlencode(
        {
            "generation": row["generation"],
            "ifMetagenerationMatch": row["metageneration"],
            "fields": "name,size,generation,metageneration,md5Hash,crc32c,temporaryHold",
        }
    )
    url = f"https://storage.googleapis.com/storage/v1/b/{quoted_bucket}/o/{key}?{params}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        actual = json.load(response)
    if (
        str(actual.get("name", "")) != row["key"]
        or str(actual.get("generation", "")) != row["generation"]
        or str(actual.get("metageneration", "")) != row["metageneration"]
        or int(actual.get("size", -1)) != row["size_bytes"]
        or actual.get("crc32c") != row.get("crc32c")
        or actual.get("md5Hash") != row.get("md5")
        or actual.get("temporaryHold") is not True
    ):
        raise RuntimeError("held GCS exact-generation readback differs")


with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
    futures = [executor.submit(verify_hold, row) for row in rows]
    for future in concurrent.futures.as_completed(futures):
        future.result()
print(json.dumps({"temporarily_held": len(rows), "verified": len(rows)}))
PY
)"; then
  fail "GCS generation holds" "one or more exact live generations is not temporarily held" 29
fi
emit "GCS generation holds" "PASS" "$HOLD_RESULT"

SOURCE_REF="$CURRENT_REF"
SOURCE_VERSION="$CURRENT_VERSION"
python3 - "${WORKDIR}/manifest.json" "$BACKUP_ID" "$LAKEHOUSE_BUCKET" \
  "$RELEASE_BACKUP_BUCKET" "$BACKUP_PREFIX" "$SOURCE_REF" "$SOURCE_VERSION" \
  "$OBJECT_COUNT" "$OBJECT_BYTES" "$BACKUP_POLICY_SHA256" "$BACKUP_LOCATION" \
  "$PROJECT_ID" \
  "${WORKDIR}/postgres.meta.json" "${WORKDIR}/postgres_gold.meta.json" \
  "${WORKDIR}/objects.meta.json" "${WORKDIR}/images.meta.json" "${WORKDIR}/provenance.meta.json" \
  "${WORKDIR}/postgres.sql.gz" "${WORKDIR}/postgres_gold.sql.gz" \
  "${WORKDIR}/lakehouse_objects.jsonl" "${WORKDIR}/runtime-images.json" \
  "${WORKDIR}/runtime-provenance.json" "${WORKDIR}/database-images.json" \
  "${WORKDIR}/database-restore-settings.json" "${WORKDIR}/bucket.json" <<'PY'
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

(output, backup_id, lakehouse_bucket, backup_bucket, prefix, source_ref,
 source_version, object_count, object_bytes, policy_sha256, backup_location,
 project_id,
 *paths) = sys.argv[1:]
meta_paths = paths[:5]
local_paths = paths[5:10]
database_images_path = paths[10]
database_restore_settings_path = paths[11]
lakehouse_policy_path = paths[12]
names = ["postgres", "postgres_gold", "object_manifest", "runtime_images", "runtime_provenance"]
keys = ["postgres.sql.gz", "postgres_gold.sql.gz", "lakehouse_objects.jsonl", "runtime-images.json", "runtime-provenance.json"]
artifacts = {}
for name, key, meta_path, local_path in zip(names, keys, meta_paths, local_paths):
    metadata = json.load(open(meta_path, encoding="utf-8"))
    digest = hashlib.sha256(pathlib.Path(local_path).read_bytes()).hexdigest()
    artifacts[name] = {
        "uri": f"gs://{backup_bucket}/{prefix}/{key}",
        "generation": str(metadata["generation"]),
        "size_bytes": int(metadata["size"]),
        "crc32c": metadata.get("crc32c"),
        "md5": metadata.get("md5Hash"),
        "sha256": digest,
    }
database_restore_settings_bytes = pathlib.Path(database_restore_settings_path).read_bytes()
database_restore_settings = json.loads(database_restore_settings_bytes)
lakehouse_policy = json.load(open(lakehouse_policy_path, encoding="utf-8"))
lakehouse_policy_payload = dict(lakehouse_policy)
lakehouse_policy_sha256 = lakehouse_policy_payload.pop("policy_sha256", "")
if hashlib.sha256(
    json.dumps(lakehouse_policy_payload, separators=(",", ":"), sort_keys=True).encode()
).hexdigest() != lakehouse_policy_sha256:
    raise SystemExit("live lakehouse policy hash is not self-consistent")
payload = {
    "schema_version": 4,
    "complete": True,
    "backup_id": backup_id,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "source_release": {"deploy_ref": source_ref, "version": source_version},
    "consistency": {
        "writers_fenced": True,
        "scheduler_fenced": True,
        "database_default_transaction_read_only": True,
        "database_mode": "logical dumps while all known application writers were stopped",
        "object_manifest_passes": 2,
    },
    "object_storage": {
        "bucket": lakehouse_bucket,
        "versioning_enabled": True,
        "object_count": int(object_count),
        "total_bytes": int(object_bytes),
        "bucket_policy": lakehouse_policy,
        "restore_point": "exact live object generations recorded in object_manifest",
        "generation_hold": {
            "type": "temporaryHold",
            "all_recorded_generations_held": True,
            "release_policy": "explicit-approved-release-only",
            "implicit_release": False,
        },
    },
    "backup_storage": {
        "bucket": backup_bucket,
        "location": backup_location,
        "uniform_bucket_level_access": True,
        "public_access_prevention": "enforced",
        "versioning_enabled": True,
        "soft_delete_seconds": 2592000,
        "retention_seconds": 604800,
        "retention_locked": False,
        "vm_role": f"projects/{project_id}/roles/omegaReleaseBackupWriter",
        "vm_permissions": [
            "storage.buckets.get",
            "storage.objects.create",
            "storage.objects.get",
        ],
        "policy_sha256": policy_sha256,
    },
    "database_images": json.load(open(database_images_path, encoding="utf-8")),
    "database_restore_settings": database_restore_settings,
    "database_restore_settings_sha256": hashlib.sha256(
        database_restore_settings_bytes
    ).hexdigest(),
    "runtime_provenance_sha256": artifacts["runtime_provenance"]["sha256"],
    "artifacts": artifacts,
    "plaintext_runtime_secrets_included": False,
    "database_contents_sensitive": True,
}
pathlib.Path(output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
MANIFEST_SHA="$(sha256sum "${WORKDIR}/manifest.json" | awk '{print $1}')"
upload_backup_generation "${WORKDIR}/manifest.json" "${BACKUP_PREFIX}/manifest.json" "${WORKDIR}/manifest.meta.json"
MANIFEST_GENERATION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["generation"])' "${WORKDIR}/manifest.meta.json")"
MANIFEST_SIZE_BYTES="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["size"])' "${WORKDIR}/manifest.meta.json")"
emit "retention-protected backup manifest" "PASS" "sha256=${MANIFEST_SHA} generation=${MANIFEST_GENERATION} size=${MANIFEST_SIZE_BYTES}"

write_operation_state "captured"
database_fence off || fail "database write fence release" "cannot reset persistent read-only defaults" 30
"${COMPOSE[@]}" start "${RUNNING_BEFORE[@]}" >/dev/null
if ! wait_for_runtime_restore; then
  fail "runtime restored after backup" "not every previously-active service returned running/healthy" 30
fi
write_operation_state "restored"
rm -f -- "$OPERATION_MARKER"
"$SAFE_IO" fsync-dir "$SHARED_ROOT"
"$WATCHDOG" disarm "$$" "$OPERATION_MARKER"
RUNTIME_FENCED=0
DB_FENCE_ACTIVE=0
python3 - "$PREDEPLOY_ATTESTATION" "$BACKUP_ID" "$CURRENT_REF" \
  "$CANDIDATE_REF" "gs://${RELEASE_BACKUP_BUCKET}/${BACKUP_PREFIX}/manifest.json" \
  "$MANIFEST_GENERATION" "$MANIFEST_SIZE_BYTES" "$MANIFEST_SHA" \
  "${WORKDIR}/manifest.json" "$LAKEHOUSE_BUCKET" "$RELEASE_BACKUP_BUCKET" \
  "$BACKUP_POLICY_SHA256" <<'PY'
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
manifest = json.load(open(sys.argv[9], encoding="utf-8"))
payload = {
    "schema_version": 1,
    "state": "ready",
    "backup_id": sys.argv[2],
    "source_ref": sys.argv[3],
    "candidate_ref": sys.argv[4],
    "manifest_uri": sys.argv[5],
    "manifest_generation": sys.argv[6],
    "manifest_size_bytes": int(sys.argv[7]),
    "manifest_sha256": sys.argv[8],
    "manifest_created_at": manifest["created_at"],
    "attested_at": datetime.now(timezone.utc).isoformat(),
    "lakehouse_bucket": sys.argv[10],
    "backup_bucket": sys.argv[11],
    "backup_policy_sha256": sys.argv[12],
    "database_restore_settings": manifest["database_restore_settings"],
    "database_restore_settings_sha256": manifest[
        "database_restore_settings_sha256"
    ],
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
emit "pre-deploy backup attestation" "PASS" "fresh server-owned one-time deploy binding created"
emit "runtime restored after backup" "PASS" "all previously-active services running/healthy; scheduler=1"
printf 'OMEGA_GCP_BACKUP_JSON={"status":"PASS","backup_id":"%s","manifest_uri":"gs://%s/%s/manifest.json","manifest_sha256":"%s","manifest_generation":"%s","manifest_size_bytes":%s,"object_count":%s,"lakehouse_bucket":"%s","backup_bucket":"%s"}\n' \
  "$BACKUP_ID" "$RELEASE_BACKUP_BUCKET" "$BACKUP_PREFIX" "$MANIFEST_SHA" "$MANIFEST_GENERATION" "$MANIFEST_SIZE_BYTES" "$OBJECT_COUNT" "$LAKEHOUSE_BUCKET" "$RELEASE_BACKUP_BUCKET"
