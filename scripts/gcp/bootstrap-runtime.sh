#!/usr/bin/env bash
set -Eeuo pipefail

exec > >(tee -a /var/log/omega-startup.log) 2>&1

umask 077
STARTUP_CONFIG_BASE64="${OMEGA_GCP_STARTUP_CONFIG_BASE64:?startup config contract is required}"
APP_ROOT="/opt/modecissions"
CURRENT_DIR="${APP_ROOT}/current"
SHARED_ROOT="${APP_ROOT}/shared"
SHARED_ENV="${SHARED_ROOT}/infra.env"
GCP_RUNTIME_COMPOSE="${SHARED_ROOT}/docker-compose.gcp.yml"
OPERATION_MARKER="${SHARED_ROOT}/operation-state.json"
STATE_LINK="${SHARED_ROOT}/runtime-state"
STATE_BUNDLES_ROOT="${SHARED_ROOT}/state-bundles"
BOOTSTRAP_STATE="${STATE_LINK}/bootstrap-state.json"
RUNTIME_PROVENANCE="${STATE_LINK}/runtime-provenance.json"
DATA_DISK="/dev/disk/by-id/google-omega-docker-data"
SAFE_IO_SOURCE="${OMEGA_GCP_SAFE_IO_SOURCE:?safe I/O source is required}"
METADATA_FIREWALL_SOURCE="${OMEGA_GCP_METADATA_FIREWALL_SOURCE:?metadata firewall source is required}"
OPERATION_GUARD_SOURCE="${OMEGA_GCP_OPERATION_GUARD_SOURCE:?operation guard source is required}"
SAFE_IO="/usr/local/sbin/omega-safe-io"
WATCHDOG="/usr/local/sbin/omega-operation-watchdog"
STARTUP_CONFIG_DIR="$(mktemp -d /run/omega-startup.XXXXXX)"
STARTUP_CONFIG="${STARTUP_CONFIG_DIR}/contract.json"

echo "[omega-startup] start $(date -Iseconds)"

# Fence the daemon/socket before parsing configuration, package operations, or
# recovery. This runs even when Docker is absent and survives all later errors.
systemctl stop docker.service docker.socket containerd.service >/dev/null 2>&1 || true
systemctl mask --runtime docker.service docker.socket containerd.service >/dev/null 2>&1 || true

install -d -m 0755 /usr/local/sbin
SAFE_IO_TMP="$(mktemp /usr/local/sbin/.omega-safe-io.XXXXXX)"
install -m 0755 "${SAFE_IO_SOURCE}" "${SAFE_IO_TMP}"
chmod 0755 "${SAFE_IO_TMP}"
mv -f "${SAFE_IO_TMP}" "${SAFE_IO}"
"${SAFE_IO}" fsync-dir /usr/local/sbin
printf '%s' "${STARTUP_CONFIG_BASE64}" | "${SAFE_IO}" startup-config --output "${STARTUP_CONFIG}"

cfg() {
  python3 - "${STARTUP_CONFIG}" "$@" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
for key in sys.argv[2:]:
    value = value[key]
if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, (str, int)):
    print(value)
else:
    raise SystemExit("startup config scalar expected")
PY
}

PROJECT_ID="$(cfg project_id)"
SOURCE_BUCKET="$(cfg source bucket)"
SOURCE_OBJECT="$(cfg source object)"
SOURCE_SHA="$(cfg source ref)"
SOURCE_GENERATION="$(cfg source generation)"
SOURCE_SIZE_BYTES="$(cfg source size_bytes)"
SOURCE_ARCHIVE_SHA256="$(cfg source archive_sha256)"
PUBLIC_CONSOLE_URL="$(cfg urls public_console)"
PUBLIC_WORKSPACE_URL="$(cfg urls public_workspace)"
PUBLIC_AIRFLOW_URL="$(cfg urls public_airflow)"
TECHNICAL_CONSOLE_URL="$(cfg urls technical_console)"
TECHNICAL_WORKSPACE_URL="$(cfg urls technical_workspace)"
ADMIN_EMAIL="$(cfg admin_email)"
COOKIE_SECURE="$(cfg cookie_secure)"
EXPECTED_DATA_DISK_BYTES="$(cfg data_disk_size_bytes)"
LAKEHOUSE_BUCKET="$(cfg lakehouse_bucket)"
RELEASE_BACKUP_BUCKET="$(cfg release_backup_bucket)"
LAKEHOUSE_ENDPOINT="$(cfg lakehouse_endpoint)"
ENABLE_AIRFLOW_SCHEDULER="$(cfg enable_airflow_scheduler)"
SECRET_PREFIX="$(cfg secret_prefix)"
RELEASE_DIR="${APP_ROOT}/releases/${SOURCE_SHA}"

# Install the embedded, reviewed gate before any package operation can trigger
# Docker. Its permanent drop-in has no bootstrap bypass.
install -d -m 0755 /usr/local/sbin /etc/systemd/system/docker.service.d
GUARD_TMP="$(mktemp /usr/local/sbin/.omega-operation-gate.XXXXXX)"
DROPIN_TMP="$(mktemp /etc/systemd/system/docker.service.d/.omega-operation-gate.conf.XXXXXX)"
install -m 0755 "${OPERATION_GUARD_SOURCE}" "${GUARD_TMP}"
chmod 0755 "${GUARD_TMP}"
printf '%s\n' '[Service]' 'ExecStartPre=/usr/local/sbin/omega-operation-gate' \
  > "${DROPIN_TMP}"
chmod 0644 "${DROPIN_TMP}"
"${SAFE_IO}" fsync-file "${GUARD_TMP}" "${DROPIN_TMP}"
# Publish the drop-in first: if this is a fresh host and power fails before the
# executable rename, ExecStartPre references a missing gate and Docker fails
# closed. Existing hosts retain the prior executable until the second rename.
mv -Tf "${DROPIN_TMP}" /etc/systemd/system/docker.service.d/omega-operation-gate.conf
"${SAFE_IO}" fsync-dir /etc/systemd/system/docker.service.d
mv -Tf "${GUARD_TMP}" /usr/local/sbin/omega-operation-gate
"${SAFE_IO}" fsync-dir /usr/local/sbin

if command -v iptables >/dev/null && command -v ip6tables >/dev/null; then
  "${METADATA_FIREWALL_SOURCE}" install
fi

# A SIGKILL or reboot during backup/deploy leaves this durable marker. Stop the
# daemon/socket before any restart policy can reactivate a writer or scheduler.
if [[ -e "${OPERATION_MARKER}" ]]; then
  echo "[omega-startup] incomplete canonical operation; Docker remains fenced" >&2
    exit 75
fi

# Existing hosts recover only after the exact durable state pair is verified.
# No package operation or artifact download precedes this branch.
if [[ -e "${STATE_LINK}" || -L "${STATE_LINK}" ]]; then
  if [[ ! -x /usr/local/sbin/omega-operation-gate ]] || \
      ! /usr/local/sbin/omega-operation-gate; then
    echo "[omega-startup] canonical runtime-state verification failed; Docker remains fenced" >&2
    exit 76
  fi
  if [[ ! -L "${CURRENT_DIR}" || ! -s "${SHARED_ENV}" || \
        ! -s "${GCP_RUNTIME_COMPOSE}" || ! -s "${RUNTIME_PROVENANCE}" ]]; then
    echo "[omega-startup] canonical shared inputs are incomplete" >&2
    exit 76
  fi
  HELPER_PATHS="$(/usr/local/sbin/omega-operation-gate print-helpers)"
  IFS=$'\t' read -r REBOOT_HELPER RUNTIME_HELPER SAFE_IO_HELPER <<<"${HELPER_PATHS}"
  [[ -n "${REBOOT_HELPER}" && -n "${RUNTIME_HELPER}" && \
      -n "${SAFE_IO_HELPER}" ]] || exit 76
  systemctl unmask --runtime docker.service docker.socket containerd.service >/dev/null 2>&1 || true
  systemctl daemon-reload
  echo "[omega-startup] bootstrap already complete; recovering exact current without download/build/pull"
  OMEGA_GCP_APP_ROOT="${APP_ROOT}" OMEGA_GCP_COMPOSE_PROJECT=infra \
    OMEGA_GCP_RUNTIME_CONTRACT="${RUNTIME_HELPER}" \
    OMEGA_GCP_SAFE_IO="${SAFE_IO_HELPER}" bash "${REBOOT_HELPER}"
  echo "[omega-startup] exact reboot recovery complete $(date -Iseconds)"
  exit 0
fi

install -d -m 0755 "${APP_ROOT}/releases" "${SHARED_ROOT}"
install -d -m 0700 "${STATE_BUNDLES_ROOT}"
python3 - "${OPERATION_MARKER}" "${SOURCE_SHA}" <<'PY'
import json, os, pathlib, sys, tempfile
from datetime import datetime, timezone
path = pathlib.Path(sys.argv[1])
payload = {"schema_version": 1, "operation": "bootstrap", "state": "initializing",
           "deploy_ref": sys.argv[2], "updated_at": datetime.now(timezone.utc).isoformat()}
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PY

# This authorization lives only in /run. A crash or reboot removes it while the
# durable marker remains, so the permanent gate cannot restart Docker.
install -d -m 0755 /run/systemd/system/docker.service.d
printf '%s\n' '[Service]' \
  'Environment=OMEGA_GCP_INITIAL_BOOTSTRAP=1' \
  'Environment=OMEGA_GCP_ALLOW_OPERATION_MARKER=1' \
  > /run/systemd/system/docker.service.d/omega-initial-bootstrap.conf
chmod 0644 /run/systemd/system/docker.service.d/omega-initial-bootstrap.conf
"${SAFE_IO}" fsync-dir /run/systemd/system/docker.service.d
systemctl unmask --runtime docker.service docker.socket containerd.service >/dev/null 2>&1 || true
systemctl daemon-reload
OMEGA_GCP_INITIAL_BOOTSTRAP=1 OMEGA_GCP_ALLOW_OPERATION_MARKER=1 \
  /usr/local/sbin/omega-operation-gate

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl gnupg jq python3 openssl lsof iptables
"${METADATA_FIREWALL_SOURCE}" install

mkdir -p /var/lib/docker
if [[ ! -b "${DATA_DISK}" ]]; then
  echo "[omega-startup] canonical Docker data disk is missing" >&2
  exit 77
fi
set +e
DISK_TYPE="$(blkid -o value -s TYPE "${DATA_DISK}" 2>/dev/null)"
BLKID_RC=$?
DISK_BYTES="$(blockdev --getsize64 "${DATA_DISK}" 2>/dev/null)"
SIZE_RC=$?
set -e
if [[ "${SIZE_RC}" != "0" || ! "${DISK_BYTES}" =~ ^[1-9][0-9]*$ || \
      "${DISK_BYTES}" != "${EXPECTED_DATA_DISK_BYTES}" ]]; then
  echo "[omega-startup] data disk byte size differs from signed startup contract" >&2
  exit 77
fi
if [[ "${BLKID_RC}" == "2" ]]; then
  set +e
  LSBLK_TYPE="$(lsblk -nro FSTYPE "${DATA_DISK}" 2>/dev/null)"; LSBLK_RC=$?
  WIPEFS_OUTPUT="$(wipefs -n "${DATA_DISK}" 2>/dev/null)"; WIPEFS_RC=$?
  set -e
  if [[ "${LSBLK_RC}" != "0" || "${WIPEFS_RC}" != "0" || \
        -n "${LSBLK_TYPE}" || \
        -n "${WIPEFS_OUTPUT}" ]]; then
    echo "[omega-startup] blank-disk probes were inconclusive" >&2
    exit 77
  fi
  # A missing signature is not proof of an unused disk. Scan every byte before
  # the one destructive formatting operation; any difference or read error is
  # a hard stop.
  if ! cmp --silent --bytes="${DISK_BYTES}" "${DATA_DISK}" /dev/zero; then
    echo "[omega-startup] unformatted data disk is not byte-for-byte blank" >&2
    exit 77
  fi
  mkfs.ext4 -F "${DATA_DISK}"
elif [[ "${BLKID_RC}" != "0" || "${DISK_TYPE}" != "ext4" ]]; then
  echo "[omega-startup] data disk identity is unknown or not ext4" >&2
  exit 77
else
  # On a state-less first boot, accepting an arbitrary ext4 disk could revive
  # stale Docker containers. A clean superblock and a read-only fsck are
  # required before inspecting without journal replay; otherwise the later
  # normal mount could materialize journaled state that was never reviewed.
  EXT4_STATE="$(LC_ALL=C tune2fs -l "${DATA_DISK}" 2>/dev/null | \
    awk -F: '$1 == "Filesystem state" {gsub(/^[[:space:]]+/, "", $2); print $2; exit}')"
  EXT4_FEATURES="$(LC_ALL=C tune2fs -l "${DATA_DISK}" 2>/dev/null | \
    awk -F: '$1 == "Filesystem features" {gsub(/^[[:space:]]+/, "", $2); print $2; exit}')"
  if [[ "${EXT4_STATE}" != "clean" || " ${EXT4_FEATURES} " == *" needs_recovery "* ]] || \
      ! LC_ALL=C e2fsck -fn "${DATA_DISK}" >/dev/null 2>&1; then
    echo "[omega-startup] existing ext4 data disk is dirty or requires recovery" >&2
    exit 77
  fi
  # With no pending journal recovery, the noload view is the view the normal
  # mount will expose. Accept only an empty filesystem (apart from lost+found).
  DISK_CHECK="$(mktemp -d /run/omega-disk-check.XXXXXX)"
  if ! mount -o ro,noload "${DATA_DISK}" "${DISK_CHECK}"; then
    rmdir "${DISK_CHECK}"
    echo "[omega-startup] existing ext4 data disk cannot be inspected" >&2
    exit 77
  fi
  DISK_CONTENT="$(find "${DISK_CHECK}" -mindepth 1 -maxdepth 1 \
    ! -name lost+found -print -quit)"
  LOST_FOUND_CONTENT=""
  if [[ -d "${DISK_CHECK}/lost+found" ]]; then
    LOST_FOUND_CONTENT="$(find "${DISK_CHECK}/lost+found" -mindepth 1 -print -quit)"
  fi
  if ! umount "${DISK_CHECK}"; then
    echo "[omega-startup] existing ext4 probe could not be unmounted" >&2
    exit 77
  fi
  rmdir "${DISK_CHECK}"
  if [[ -n "${DISK_CONTENT}" || -n "${LOST_FOUND_CONTENT}" ]]; then
    echo "[omega-startup] existing ext4 data disk is not a blank filesystem" >&2
    exit 77
  fi
fi
if findmnt --fstab --noheadings --output SOURCE,TARGET | \
    awk '$2 == "/var/lib/docker" && $1 != "/dev/disk/by-id/google-omega-docker-data" { bad=1 } END { exit bad ? 0 : 1 }'; then
  echo "[omega-startup] fstab already maps Docker to a different source" >&2
  exit 77
fi
if ! grep -Fqx "${DATA_DISK} /var/lib/docker ext4 discard,defaults,nofail 0 2" /etc/fstab; then
  echo "${DATA_DISK} /var/lib/docker ext4 discard,defaults,nofail 0 2" >> /etc/fstab
fi
mount /var/lib/docker
MOUNT_SOURCE="$(findmnt --noheadings --output SOURCE --target /var/lib/docker)"
if [[ ! -b "${MOUNT_SOURCE}" || \
      "$(readlink -f "${MOUNT_SOURCE}")" != "$(readlink -f "${DATA_DISK}")" ]]; then
  echo "[omega-startup] Docker data mount source differs after mount" >&2
  exit 77
fi

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl daemon-reload
systemctl enable docker
systemctl start docker

download_source() {
  "${SAFE_IO}" gcs-download \
    --uri "gs://${SOURCE_BUCKET}/${SOURCE_OBJECT}" \
    --generation "${SOURCE_GENERATION}" \
    --size "${SOURCE_SIZE_BYTES}" \
    --sha256 "${SOURCE_ARCHIVE_SHA256}" \
    --output "${STARTUP_CONFIG_DIR}/source.tar.gz"
}

secret_value() {
  local name
  name="$1"
  "${SAFE_IO}" secret-access --project "${PROJECT_ID}" \
    --secret "${SECRET_PREFIX}${name}" --version latest
}

set_env() {
  local key="$1" value="$2"
  printf '%s' "${value}" | "${SAFE_IO}" env-set --path "$PWD/infra/.env" --key "${key}"
}

load_required_secret() {
  local secret_name="$1"
  local env_name="$2"
  local value
  if ! value="$(secret_value "${secret_name}")"; then
    echo "[omega-startup] required Secret Manager value unavailable: ${secret_name}" >&2
    exit 1
  fi
  printf -v "${env_name}" '%s' "${value}"
  export "${env_name}"
}

bootstrap_fail_closed() {
  local rc=$? safe=1
  trap - EXIT
  set +e
  if [[ "${BOOTSTRAP_MUTATION_STARTED:-0}" == "1" ]]; then
    echo "[omega-startup] bootstrap incomplete; stopping Docker with durable marker retained" >&2
    systemctl stop docker.service docker.socket containerd.service >/dev/null 2>&1
    systemctl mask --runtime docker.service docker.socket containerd.service >/dev/null 2>&1
    for service in docker.service docker.socket containerd.service; do
      if systemctl is-active --quiet "$service"; then
        safe=0
      fi
    done
    if [[ "$safe" != "1" ]]; then
      echo "[omega-startup] Docker stop verification failed; watchdog recovery required" >&2
      rc=90
    fi
  fi
  exit "${rc}"
}

BOOTSTRAP_MUTATION_STARTED=1
trap bootstrap_fail_closed EXIT

download_source
RELEASE_STAGE="$(mktemp -d "${APP_ROOT}/releases/.${SOURCE_SHA}.XXXXXX")"
"${SAFE_IO}" safe-extract --archive "${STARTUP_CONFIG_DIR}/source.tar.gz" \
  --destination "${RELEASE_STAGE}"
mv "${RELEASE_STAGE}" "${RELEASE_DIR}"
"${SAFE_IO}" fsync-dir "${APP_ROOT}/releases"
CURRENT_TMP="${APP_ROOT}/.current.${SOURCE_SHA}.$$"
ln -s "${RELEASE_DIR}" "${CURRENT_TMP}"
mv -Tf "${CURRENT_TMP}" "${CURRENT_DIR}"
"${SAFE_IO}" fsync-dir "${APP_ROOT}"
cd "${CURRENT_DIR}"

RELEASE_WATCHDOG="${CURRENT_DIR}/scripts/gcp/operation-watchdog.sh"
if [[ ! -x "${RELEASE_WATCHDOG}" ]]; then
  echo "[omega-startup] independent operation watchdog is missing" >&2
  exit 78
fi
WATCHDOG_TMP="$(mktemp /usr/local/sbin/.omega-operation-watchdog.XXXXXX)"
install -m 0755 "${RELEASE_WATCHDOG}" "${WATCHDOG_TMP}"
"${SAFE_IO}" fsync-file "${WATCHDOG_TMP}"
mv -Tf "${WATCHDOG_TMP}" "${WATCHDOG}"
"${SAFE_IO}" fsync-dir /usr/local/sbin

mkdir -p "$(dirname "${SHARED_ENV}")"

load_required_secret control_room_evidence_signing_key_id CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID
load_required_secret control_room_evidence_signing_key CONTROL_ROOM_EVIDENCE_SIGNING_KEY
load_required_secret control_room_evidence_signing_previous_keys CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS

if [[ ! -f infra/.env ]]; then
  bash infra/bootstrap.sh
fi
chmod 600 infra/.env
"${SAFE_IO}" env-validate --path "$PWD/infra/.env" --forbid-prefix OMEGA_MIGRATION_ >/dev/null
set_env CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID "${CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID}"
set_env CONTROL_ROOM_EVIDENCE_SIGNING_KEY "${CONTROL_ROOM_EVIDENCE_SIGNING_KEY}"
set_env CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS "${CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS}"
bash infra/bootstrap-keys.sh infra/.env
chmod 600 infra/.env
mkdir -p data/lakehouse
mkdir -p airflow/dags airflow/logs/scheduler airflow/plugins
chown -R 50000:0 airflow/dags airflow/logs airflow/plugins
chmod -R 775 airflow/dags airflow/logs airflow/plugins

set_env APP_ENV production
set_env COOKIE_SECURE "${COOKIE_SECURE}"
set_env CONSOLE_URL "${PUBLIC_CONSOLE_URL}"
set_env WORKSPACE_PUBLIC_URL "${PUBLIC_WORKSPACE_URL}"
set_env ALLOWED_ORIGINS "${PUBLIC_CONSOLE_URL},${PUBLIC_WORKSPACE_URL},${TECHNICAL_CONSOLE_URL},${TECHNICAL_WORKSPACE_URL}"
set_env AIRFLOW_PUBLIC_URL "${PUBLIC_AIRFLOW_URL}"
set_env AIRFLOW_URL "http://airflow:8080/airflow"
set_env AIRFLOW_HEALTH_PATH "/airflow/health"
set_env SUPERSET_PUBLIC_URL ""
set_env SUPERSET_SESSION_COOKIE_SECURE "${COOKIE_SECURE}"
set_env SUPERSET_FORCE_HTTPS "${COOKIE_SECURE}"
set_env SUPERSET_ENABLE_PROXY_FIX true
set_env LAKEHOUSE_PROVIDER gcs
set_env LAKEHOUSE_BUCKET "${LAKEHOUSE_BUCKET}"
set_env RELEASE_BACKUP_BUCKET "${RELEASE_BACKUP_BUCKET}"
set_env GCS_BUCKET "${LAKEHOUSE_BUCKET}"
set_env LAKEHOUSE_ENDPOINT "${LAKEHOUSE_ENDPOINT}"
set_env S3_BUCKET_NAME "${LAKEHOUSE_BUCKET}"
set_env MINIO_BUCKET "${LAKEHOUSE_BUCKET}"
set_env MINIO_ENDPOINT "${LAKEHOUSE_ENDPOINT}"
set_env MINIO_SECURE true
set_env AWS_ACCESS_KEY_ID ""
set_env AWS_SECRET_ACCESS_KEY ""
set_env AWS_DEFAULT_REGION auto
set_env AWS_REGION auto
set_env S3_ENDPOINT_URL "https://${LAKEHOUSE_ENDPOINT}"

if hmac_access_key="$(secret_value gcs_hmac_access_key_id)"; then
  set_env AWS_ACCESS_KEY_ID "${hmac_access_key}"
  set_env MINIO_ACCESS_KEY "${hmac_access_key}"
  set_env GCS_ACCESS_KEY_ID "${hmac_access_key}"
  set_env LAKEHOUSE_ACCESS_KEY "${hmac_access_key}"
fi
if hmac_secret_key="$(secret_value gcs_hmac_secret_access_key)"; then
  set_env AWS_SECRET_ACCESS_KEY "${hmac_secret_key}"
  set_env MINIO_SECRET_KEY "${hmac_secret_key}"
  set_env GCS_SECRET_ACCESS_KEY "${hmac_secret_key}"
  set_env LAKEHOUSE_SECRET_KEY "${hmac_secret_key}"
fi

# Publish the fully reconciled GCP runtime env only after every production,
# lakehouse, and optional HMAC value has been applied.  Copying it earlier
# leaves day-2 releases with the bootstrap defaults instead of the live GCP
# contract.
SHARED_ENV_TMP="${SHARED_ROOT}/.infra.env.${SOURCE_SHA}.$$"
install -m 0600 infra/.env "${SHARED_ENV_TMP}"
"${SAFE_IO}" fsync-file "${SHARED_ENV_TMP}"
mv -Tf "${SHARED_ENV_TMP}" "${SHARED_ENV}"
"${SAFE_IO}" env-validate --path "${SHARED_ENV}" --forbid-prefix OMEGA_MIGRATION_ >/dev/null
"${SAFE_IO}" fsync-dir "${SHARED_ROOT}"

python3 - "${STARTUP_CONFIG}" "$PWD/infra/docker-compose.gcp.yml" <<'PY'
import json, os, pathlib, sys, tempfile
config, output = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
value = json.loads(config.read_text(encoding="utf-8"))["compose_override"]
fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value); stream.write("\n" if not value.endswith("\n") else "")
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, output)
    directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PY
GCP_RUNTIME_COMPOSE_TMP="${SHARED_ROOT}/.docker-compose.gcp.yml.${SOURCE_SHA}.$$"
install -m 0600 infra/docker-compose.gcp.yml "${GCP_RUNTIME_COMPOSE_TMP}"
"${SAFE_IO}" fsync-file "${GCP_RUNTIME_COMPOSE_TMP}"
mv -Tf "${GCP_RUNTIME_COMPOSE_TMP}" "${GCP_RUNTIME_COMPOSE}"
"${SAFE_IO}" fsync-dir "${SHARED_ROOT}"

COMPOSE=(docker compose --env-file infra/.env -f infra/docker-compose.yml -f infra/docker-compose.gcp.yml --profile sap)
"${COMPOSE[@]}" config -q
"${WATCHDOG}" arm "$$" "${OPERATION_MARKER}"
echo "[omega-startup] independent operation watchdog armed"
"${COMPOSE[@]}" up --build -d
if [[ "${ENABLE_AIRFLOW_SCHEDULER}" != "true" ]]; then
  "${COMPOSE[@]}" stop airflow-scheduler || true
  echo "[omega-startup] GCP canonical requires exactly one scheduler" >&2
  exit 1
fi

echo "[omega-startup] waiting for console readiness"
ready=0
for _ in $(seq 1 180); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/readyz >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 10
done
if [[ "${ready}" != "1" ]]; then
  echo "[omega-startup] console did not become ready"
  "${COMPOSE[@]}" ps || true
  "${COMPOSE[@]}" logs --tail=200 console || true
  exit 1
fi

CREDENTIALS_FILE="${APP_ROOT}/admin_credentials.txt"
if [[ ! -f "${CREDENTIALS_FILE}" ]]; then
  ADMIN_PASSWORD="$(openssl rand -hex 24)"
  CREDENTIALS_TMP="${APP_ROOT}/.admin_credentials.${SOURCE_SHA}.$$"
  {
    echo "url=${PUBLIC_CONSOLE_URL}"
    echo "email=${ADMIN_EMAIL}"
    echo "password=${ADMIN_PASSWORD}"
    echo "source_sha=${SOURCE_SHA}"
  } > "${CREDENTIALS_TMP}"
  chmod 600 "${CREDENTIALS_TMP}"
  "${SAFE_IO}" fsync-file "${CREDENTIALS_TMP}"
  mv -Tf "${CREDENTIALS_TMP}" "${CREDENTIALS_FILE}"
  "${SAFE_IO}" fsync-dir "${APP_ROOT}"
else
  ADMIN_PASSWORD="$(awk -F= '$1 == "password" {print $2}' "${CREDENTIALS_FILE}")"
fi

export BOOTSTRAP_ADMIN_PASSWORD="${ADMIN_PASSWORD}"
export BOOTSTRAP_ADMIN_FULL_NAME="OMEGA Admin"
"${COMPOSE[@]}" exec -T \
  -e BOOTSTRAP_ADMIN_PASSWORD \
  -e BOOTSTRAP_ADMIN_FULL_NAME \
  console python -m app.bootstrap_admin "${ADMIN_EMAIL}"
unset BOOTSTRAP_ADMIN_PASSWORD BOOTSTRAP_ADMIN_FULL_NAME ADMIN_PASSWORD

RUNTIME_CONTRACT="${CURRENT_DIR}/scripts/gcp/runtime_contract.py"
REBOOT_HELPER="${CURRENT_DIR}/scripts/gcp/reboot-runtime.sh"
RELEASE_SAFE_IO="${CURRENT_DIR}/scripts/gcp/safe_io.py"
if [[ ! -x "${RUNTIME_CONTRACT}" || ! -x "${REBOOT_HELPER}" || \
      ! -x "${RELEASE_SAFE_IO}" ]]; then
  echo "[omega-startup] runtime provenance helper missing" >&2
  exit 1
fi
VERSION="$(tr -d '\r\n' < "${CURRENT_DIR}/VERSION")"
install -d -m 0700 "${STATE_BUNDLES_ROOT}"
STATE_STAGE="$(mktemp -d "${STATE_BUNDLES_ROOT}/.bootstrap.${SOURCE_SHA}.XXXXXX")"
STATE_FINAL="${STATE_BUNDLES_ROOT}/bootstrap-${SOURCE_SHA}"
runtime_green=0
for _ in $(seq 1 60); do
  if python3 "${RUNTIME_CONTRACT}" bootstrap-record \
      --compose-project infra --deploy-ref "${SOURCE_SHA}" --version "${VERSION}" \
      --runtime-input "shared_env=${SHARED_ENV}" \
      --runtime-input "base_compose=${CURRENT_DIR}/infra/docker-compose.yml" \
      --runtime-input "gcp_compose=${GCP_RUNTIME_COMPOSE}" \
      --output "${STATE_STAGE}/runtime-provenance.json" >/dev/null 2>&1; then
    runtime_green=1
    break
  fi
  sleep 3
done
if [[ "${runtime_green}" != "1" ]]; then
  echo "[omega-startup] bootstrap runtime is not exact/running/healthy" >&2
  exit 1
fi

python3 - "${STATE_STAGE}/bootstrap-state.json" \
  "${STATE_STAGE}/runtime-provenance.json" "${SOURCE_SHA}" "${VERSION}" \
  "${REBOOT_HELPER}" "${RUNTIME_CONTRACT}" "${RELEASE_SAFE_IO}" \
  "gs://${SOURCE_BUCKET}/${SOURCE_OBJECT}" "${SOURCE_GENERATION}" \
  "${SOURCE_SIZE_BYTES}" "${SOURCE_ARCHIVE_SHA256}" <<'PY'
import hashlib
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])


def sha256(name: str) -> str:
    digest = hashlib.sha256()
    with open(name, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


payload = {
    "schema_version": 2,
    "state": "complete",
    "deploy_ref": sys.argv[3],
    "version": sys.argv[4],
    "runtime_provenance_sha256": sha256(sys.argv[2]),
    "reboot_helper": {
        "mode": "current",
        "source_ref": sys.argv[3],
        "source_artifact_uri": sys.argv[8],
        "source_artifact_generation": sys.argv[9],
        "source_artifact_size_bytes": int(sys.argv[10]),
        "source_artifact_sha256": sys.argv[11],
        "reboot_runtime_sha256": sha256(sys.argv[5]),
        "runtime_contract_sha256": sha256(sys.argv[6]),
        "safe_io_sha256": sha256(sys.argv[7]),
    },
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

chmod 0600 "${STATE_STAGE}/bootstrap-state.json" "${STATE_STAGE}/runtime-provenance.json"
if [[ -e "${STATE_FINAL}" ]]; then
  echo "[omega-startup] uncommitted bootstrap state bundle already exists" >&2
  rm -rf -- "${STATE_STAGE}"
  exit 1
fi
mv "${STATE_STAGE}" "${STATE_FINAL}"
"${SAFE_IO}" fsync-dir "${STATE_BUNDLES_ROOT}"
STATE_LINK_TMP="${SHARED_ROOT}/.runtime-state.${SOURCE_SHA}.$$"
ln -s "${STATE_FINAL}" "${STATE_LINK_TMP}"
mv -Tf "${STATE_LINK_TMP}" "${STATE_LINK}"
"${SAFE_IO}" fsync-dir "${SHARED_ROOT}"
if ! OMEGA_GCP_ALLOW_OPERATION_MARKER=1 /usr/local/sbin/omega-operation-gate; then
  echo "[omega-startup] published bootstrap state failed operation-gate verification" >&2
  systemctl stop docker.service docker.socket containerd.service >/dev/null 2>&1 || true
  exit 76
fi

date -Iseconds > "${APP_ROOT}/DEPLOYED"
"${COMPOSE[@]}" ps > "${APP_ROOT}/compose-status.txt" || true

rm -f -- /run/systemd/system/docker.service.d/omega-initial-bootstrap.conf
"${SAFE_IO}" fsync-dir /run/systemd/system/docker.service.d
systemctl daemon-reload
rm -f -- "${OPERATION_MARKER}"
"${SAFE_IO}" fsync-dir "${SHARED_ROOT}"
"${WATCHDOG}" disarm "$$" "${OPERATION_MARKER}"
BOOTSTRAP_MUTATION_STARTED=0
trap - EXIT

echo "[omega-startup] done $(date -Iseconds)"
