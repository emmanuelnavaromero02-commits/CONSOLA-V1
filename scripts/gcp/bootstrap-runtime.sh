#!/bin/bash -p
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
STARTUP_CONFIG_BASE64="${OMEGA_GCP_STARTUP_CONFIG_BASE64:-}"
if [[ -z "$STARTUP_CONFIG_BASE64" ]]; then
  STARTUP_CONFIG_FILE="${OMEGA_GCP_STARTUP_CONFIG_FILE:-}"
  if [[ "$STARTUP_CONFIG_FILE" != /run/omega-gcp-bootstrap.*/startup-config.base64 || \
        -L "$STARTUP_CONFIG_FILE" || ! -f "$STARTUP_CONFIG_FILE" || \
        "$(stat -c '%u:%g:%a' -- "$STARTUP_CONFIG_FILE" 2>/dev/null)" != "0:0:600" ]]; then
    echo "[omega-startup] private startup config contract is required" >&2
    exit 10
  fi
  STARTUP_CONFIG_BASE64="$(<"$STARTUP_CONFIG_FILE")"
fi
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
OPERATION_WATCHDOG_SOURCE="${OMEGA_GCP_OPERATION_WATCHDOG_SOURCE:?operation watchdog source is required}"
REBOOT_RUNTIME_SOURCE="${OMEGA_GCP_REBOOT_RUNTIME_SOURCE:?reboot runtime source is required}"
RUNTIME_CONTRACT_SOURCE="${OMEGA_GCP_RUNTIME_CONTRACT_SOURCE:?runtime contract source is required}"
TERMINAL_RECEIPT="${OMEGA_GCP_STARTUP_TERMINAL_RECEIPT:?startup terminal receipt is required}"
STARTUP_CONTRACT_SHA256="${OMEGA_GCP_STARTUP_CONTRACT_SHA256:?startup contract checksum is required}"
EXPECTED_CONTROLLER_REF="${OMEGA_GCP_CONTROLLER_REF:?reviewed controller ref is required}"
EXPECTED_FOUNDATION_MARKER_SHA256="${OMEGA_GCP_EXPECTED_FOUNDATION_MARKER_SHA256:-}"
EXPECTED_FOUNDATION_WATCHDOG_STATE_SHA256="${OMEGA_GCP_EXPECTED_FOUNDATION_WATCHDOG_STATE_SHA256:-}"
EXPECTED_FOUNDATION_DEPLOY_REF="${OMEGA_GCP_EXPECTED_FOUNDATION_DEPLOY_REF:-}"
EXPECTED_FOUNDATION_HELPER_REF="${OMEGA_GCP_EXPECTED_FOUNDATION_HELPER_REF:-}"
EXPECTED_FOUNDATION_STARTUP_SHA256="${OMEGA_GCP_EXPECTED_FOUNDATION_STARTUP_SHA256:-}"
SAFE_IO="/usr/local/sbin/omega-safe-io"
WATCHDOG="/usr/local/sbin/omega-operation-watchdog"
CANONICAL_RUNTIME_CONTRACT="/usr/local/sbin/omega-runtime-contract"
RESTART_POLICY_STATE="${SHARED_ROOT}/restart-policy-fence.json"
RESTART_POLICY_DROPIN="/etc/systemd/system/docker.service.d/omega-restart-policy-fence.conf"
STARTUP_CONFIG_DIR="$(mktemp -d /run/omega-startup.XXXXXX)"
STARTUP_CONFIG="${STARTUP_CONFIG_DIR}/contract.json"

case "${TERMINAL_RECEIPT}" in
  /run/omega-gcp-bootstrap.*/terminal-state.json) ;;
  *) echo "[omega-startup] terminal receipt path is unconfined" >&2; exit 10 ;;
esac
if [[ ! "${STARTUP_CONTRACT_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "[omega-startup] startup contract checksum is invalid" >&2
  exit 10
fi

write_terminal_receipt() {
  local terminal_state="$1" deploy_ref="$2" version="$3"
  local marker_sha256="${4:-}" state_sha256="${5:-}"
  /usr/bin/python3 -I - "${TERMINAL_RECEIPT}" "${terminal_state}" \
    "${deploy_ref}" "${EXPECTED_CONTROLLER_REF}" "${version}" \
    "${STARTUP_CONTRACT_SHA256}" "${marker_sha256}" "${state_sha256}" <<'PY'
import json
import os
import pathlib
import re
import stat
import sys
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
(
    state, deploy_ref, helper_ref, version, contract_sha256,
    marker_sha256, watchdog_state_sha256,
) = sys.argv[2:]
parent = path.parent.lstat()
if (
    stat.S_ISLNK(parent.st_mode)
    or not stat.S_ISDIR(parent.st_mode)
    or parent.st_uid != 0
    or parent.st_gid != 0
    or stat.S_IMODE(parent.st_mode) != 0o700
    or state not in {"foundation-fenced", "live-recovered", "live-bootstrapped"}
    or re.fullmatch(r"[0-9a-f]{40}", deploy_ref) is None
    or re.fullmatch(r"[0-9a-f]{40}", helper_ref) is None
    or re.fullmatch(r"[0-9a-f]{64}", contract_sha256) is None
    or (
        state == "foundation-fenced"
        and (
            version != ""
            or re.fullmatch(r"[0-9a-f]{64}", marker_sha256) is None
            or re.fullmatch(r"[0-9a-f]{64}", watchdog_state_sha256) is None
        )
    )
    or (
        state != "foundation-fenced"
        and (
            marker_sha256 != ""
            or watchdog_state_sha256 != ""
            or re.fullmatch(
                r"[0-9]+[.][0-9]+[.][0-9]+(?:-[0-9A-Za-z.-]+)?", version
            ) is None
        )
    )
):
    raise SystemExit(1)
payload = {
    "schema_version": 1,
    "terminal_state": state,
    "deploy_ref": deploy_ref,
    "helper_ref": helper_ref,
    "version": version,
    "startup_contract_sha256": contract_sha256,
    "foundation_marker_sha256": marker_sha256,
    "foundation_watchdog_state_sha256": watchdog_state_sha256,
    "completed_at": datetime.now(timezone.utc).isoformat(),
}
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(path, flags, 0o600)
try:
    os.fchmod(descriptor, 0o600)
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

write_foundation_receipt() {
  local evidence marker_sha state_sha marker_identity
  evidence="$("${WATCHDOG}" foundation-evidence 0 "${OPERATION_MARKER}")"
  IFS=$'\t' read -r marker_sha state_sha marker_identity <<<"${evidence}"
  if [[ ! "${marker_sha}" =~ ^[0-9a-f]{64}$ || \
        ! "${state_sha}" =~ ^[0-9a-f]{64}$ || \
        "${marker_identity}" != "${marker_sha}:"*":foundation-ready:${SOURCE_SHA}" ]]; then
    echo "[omega-startup] foundation evidence identity differs" >&2
    return 1
  fi
  write_terminal_receipt foundation-fenced "${SOURCE_SHA}" "" \
    "${marker_sha}" "${state_sha}"
  # Close the publication/readback race: the receipt is valid only while the
  # exact marker and exact durable watchdog-state bytes still match it.
  [[ "$("${WATCHDOG}" foundation-evidence 0 "${OPERATION_MARKER}")" == \
     "${evidence}" ]]
}

echo "[omega-startup] start $(date -Iseconds)"

case "${REBOOT_RUNTIME_SOURCE}" in
  /run/omega-gcp-bootstrap.*/reboot-runtime.sh) ;;
  *) echo "[omega-startup] candidate reboot helper path is unconfined" >&2; exit 10 ;;
esac
case "${RUNTIME_CONTRACT_SOURCE}" in
  /run/omega-gcp-bootstrap.*/runtime_contract.py) ;;
  *) echo "[omega-startup] candidate runtime contract path is unconfined" >&2; exit 10 ;;
esac
for candidate_helper in "${REBOOT_RUNTIME_SOURCE}" "${RUNTIME_CONTRACT_SOURCE}"; do
  candidate_parent="$(dirname "${candidate_helper}")"
  if [[ -L "${candidate_parent}" || ! -d "${candidate_parent}" || \
        "$(stat -c '%u:%g:%a' -- "${candidate_parent}" 2>/dev/null)" != "0:0:700" ]]; then
    echo "[omega-startup] candidate helper parent contract differs" >&2
    exit 10
  fi
  if [[ -L "${candidate_helper}" || ! -f "${candidate_helper}" || \
        "$(stat -c '%u:%g:%a' -- "${candidate_helper}" 2>/dev/null)" != "0:0:700" ]]; then
    echo "[omega-startup] candidate helper ownership contract differs" >&2
    exit 10
  fi
done

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
  /usr/bin/python3 -I - "${STARTUP_CONFIG}" "$@" <<'PY'
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
CONTROLLER_REF="$(cfg controller_ref)"
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
CANONICAL_WRITER="$(cfg canonical_writer)"
ENABLE_AIRFLOW_SCHEDULER="$(cfg enable_airflow_scheduler)"
EXACT_RUNTIME_CONTRACT_READY="$(cfg exact_runtime_contract_ready)"
SECRET_PREFIX="$(cfg secret_prefix)"
CONTROL_ROOM_KEY_ID_VERSION="$(cfg secret_versions control_room_evidence_signing_key_id)"
CONTROL_ROOM_KEY_VERSION="$(cfg secret_versions control_room_evidence_signing_key)"
CONTROL_ROOM_PREVIOUS_KEYS_VERSION="$(cfg secret_versions control_room_evidence_signing_previous_keys)"
GCS_HMAC_ACCESS_KEY_VERSION="$(cfg secret_versions gcs_hmac_access_key_id)"
GCS_HMAC_SECRET_KEY_VERSION="$(cfg secret_versions gcs_hmac_secret_access_key)"
RELEASE_DIR="${APP_ROOT}/releases/${SOURCE_SHA}"
if [[ ! "${CONTROLLER_REF}" =~ ^[0-9a-f]{40}$ || \
   "${CONTROLLER_REF}" != "${EXPECTED_CONTROLLER_REF}" ]]; then
  echo "[omega-startup] reviewed controller ref differs from startup owner" >&2
  exit 1
fi
if [[ "${CANONICAL_WRITER}" != "true" && "${CANONICAL_WRITER}" != "false" ]] || \
   [[ "${ENABLE_AIRFLOW_SCHEDULER}" != "${CANONICAL_WRITER}" ]]; then
  echo "[omega-startup] scheduler role differs from canonical-writer role" >&2
  exit 1
fi
BASE_PACKAGES=(
  "ca-certificates=$(cfg host_package_versions ca_certificates)"
  "curl=$(cfg host_package_versions curl)"
  "gnupg=$(cfg host_package_versions gnupg)"
  "iptables=$(cfg host_package_versions iptables)"
  "jq=$(cfg host_package_versions jq)"
  "lsof=$(cfg host_package_versions lsof)"
  "openssl=$(cfg host_package_versions openssl)"
  "python3=$(cfg host_package_versions python3)"
)
DOCKER_PACKAGES=(
  "docker-ce=$(cfg host_package_versions docker_ce)"
  "docker-ce-cli=$(cfg host_package_versions docker_ce_cli)"
  "containerd.io=$(cfg host_package_versions containerd_io)"
  "docker-buildx-plugin=$(cfg host_package_versions docker_buildx_plugin)"
  "docker-compose-plugin=$(cfg host_package_versions docker_compose_plugin)"
)

verify_installed_packages() {
  local specification package expected actual
  for specification in "$@"; do
    package="${specification%%=*}"
    expected="${specification#*=}"
    actual="$(dpkg-query -W -f='${Version}' -- "${package}" 2>/dev/null)" || {
      echo "[omega-startup] exact host package is not installed: ${package}" >&2
      return 1
    }
    if [[ "${actual}" != "${expected}" ]]; then
      echo "[omega-startup] installed host package version differs: ${package}" >&2
      return 1
    fi
  done
}

# Install the embedded, reviewed gate before any package operation can trigger
# Docker. Its permanent drop-in has no bootstrap bypass.
install -d -m 0755 /usr/local/sbin /etc/systemd/system/docker.service.d
GUARD_TMP="$(mktemp /usr/local/sbin/.omega-operation-gate.XXXXXX)"
DROPIN_TMP="$(mktemp /etc/systemd/system/docker.service.d/.omega-operation-gate.conf.XXXXXX)"
install -m 0755 "${OPERATION_GUARD_SOURCE}" "${GUARD_TMP}"
chmod 0755 "${GUARD_TMP}"
printf '%s\n' '[Service]' \
  'ExecStartPre=/usr/local/sbin/omega-operation-gate authorize-start' \
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

# Install the independent watchdog from the metadata-embedded candidate before
# Docker can be unmasked or started. A downloaded runtime is never trusted to
# establish its own kill-window fence.
WATCHDOG_TMP="$(mktemp /usr/local/sbin/.omega-operation-watchdog.XXXXXX)"
install -m 0755 "${OPERATION_WATCHDOG_SOURCE}" "${WATCHDOG_TMP}"
"${SAFE_IO}" fsync-file "${WATCHDOG_TMP}"
mv -Tf "${WATCHDOG_TMP}" "${WATCHDOG}"
"${SAFE_IO}" fsync-dir /usr/local/sbin

# Docker's ExecStop hook is the clean-shutdown producer for the durable policy
# fence required by the next boot. The candidate runtime contract was delivered
# out of band in metadata and is compared byte-for-byte with the release below.
RUNTIME_CONTRACT_TMP="$(mktemp /usr/local/sbin/.omega-runtime-contract.XXXXXX)"
install -m 0755 "${RUNTIME_CONTRACT_SOURCE}" "${RUNTIME_CONTRACT_TMP}"
"${SAFE_IO}" fsync-file "${RUNTIME_CONTRACT_TMP}"
mv -Tf "${RUNTIME_CONTRACT_TMP}" "${CANONICAL_RUNTIME_CONTRACT}"
"${SAFE_IO}" fsync-dir /usr/local/sbin
RESTART_POLICY_DROPIN_TMP="$(mktemp /etc/systemd/system/docker.service.d/.omega-restart-policy-fence.conf.XXXXXX)"
printf '%s\n' '[Service]' \
  'TimeoutStopSec=480s' \
  'ExecStop=/usr/local/sbin/omega-runtime-contract restart-policy prepare --app-root /opt/modecissions --compose-project infra --state /opt/modecissions/shared/restart-policy-fence.json' \
  > "${RESTART_POLICY_DROPIN_TMP}"
chmod 0644 "${RESTART_POLICY_DROPIN_TMP}"
"${SAFE_IO}" fsync-file "${RESTART_POLICY_DROPIN_TMP}"
mv -Tf "${RESTART_POLICY_DROPIN_TMP}" "${RESTART_POLICY_DROPIN}"
"${SAFE_IO}" fsync-dir /etc/systemd/system/docker.service.d
systemctl daemon-reload

# Seal the metadata-delivered runtime verifier to the reviewed helper ref. The
# startup-adoption finalizer executes this canonical candidate, never the older
# helper path retained in the release's bootstrap provenance.
install -d -m 0700 /var/lib/omega-gcp
/usr/bin/python3 -I - /var/lib/omega-gcp/candidate-runtime-contract.json \
  "${SOURCE_SHA}" "${CONTROLLER_REF}" "${STARTUP_CONTRACT_SHA256}" \
  "$0" "${SAFE_IO}" "${METADATA_FIREWALL_SOURCE}" \
  /usr/local/sbin/omega-operation-gate "${WATCHDOG}" \
  "${REBOOT_RUNTIME_SOURCE}" "${CANONICAL_RUNTIME_CONTRACT}" <<'PY'
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
import tempfile

path = pathlib.Path(sys.argv[1])
parent = path.parent.lstat()
if (
    not stat.S_ISDIR(parent.st_mode)
    or parent.st_uid != 0
    or parent.st_gid != 0
    or stat.S_IMODE(parent.st_mode) != 0o700
    or re.fullmatch(r"[0-9a-f]{40}", sys.argv[2]) is None
    or re.fullmatch(r"[0-9a-f]{40}", sys.argv[3]) is None
    or re.fullmatch(r"[0-9a-f]{64}", sys.argv[4]) is None
):
    raise SystemExit(1)


def read_helper(raw_path: str) -> bytes:
    helper = pathlib.Path(raw_path)
    descriptor = os.open(helper, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or stat.S_IMODE(before.st_mode) not in {0o700, 0o755}
            or before.st_nlink != 1
            or not 1 <= before.st_size <= 2 * 1024 * 1024
        ):
            raise SystemExit(1)
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise SystemExit(1)
        return raw
    finally:
        os.close(descriptor)


helper_names = (
    "bootstrap_runtime", "safe_io", "metadata_firewall", "operation_gate",
    "operation_watchdog", "reboot_runtime", "runtime_contract",
)
helper_paths = sys.argv[5:]
if len(helper_paths) != len(helper_names):
    raise SystemExit(1)
payload = {
    "schema_version": 2,
    "deploy_ref": sys.argv[2],
    "helper_ref": sys.argv[3],
    "startup_contract_sha256": sys.argv[4],
    "helper_sha256": {
        name: hashlib.sha256(read_helper(helper_path)).hexdigest()
        for name, helper_path in zip(helper_names, helper_paths, strict=True)
    },
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

# A SIGKILL or reboot during backup/deploy leaves this durable marker. Stop the
# daemon/socket before any restart policy can reactivate a writer or scheduler.
if [[ -e "${OPERATION_MARKER}" || -L "${OPERATION_MARKER}" ]]; then
  HANDOFF_STATE="$(/usr/bin/python3 -I - "${OPERATION_MARKER}" "${SOURCE_SHA}" \
    "${CONTROLLER_REF}" "${STARTUP_CONTRACT_SHA256}" \
    /var/lib/omega-gcp/candidate-runtime-contract.json \
    "$0" "${SAFE_IO}" /usr/local/sbin/omega-metadata-firewall \
    /usr/local/sbin/omega-operation-gate "${WATCHDOG}" \
    "${REBOOT_RUNTIME_SOURCE}" "${CANONICAL_RUNTIME_CONTRACT}" <<'PY'
import hashlib
import json
import os
import pathlib
import re
import stat
import sys

path = pathlib.Path(sys.argv[1])


def read_owned(path: pathlib.Path, mode: int, maximum: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or stat.S_IMODE(before.st_mode) != mode
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
        ):
            raise SystemExit(1)
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise SystemExit(1)
        return raw
    finally:
        os.close(descriptor)


descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
try:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_gid != 0
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
        or not 1 <= before.st_size <= 32768
    ):
        raise SystemExit(1)
    raw = os.read(descriptor, before.st_size + 1)
    after = os.fstat(descriptor)
finally:
    os.close(descriptor)
if len(raw) != before.st_size or (
    before.st_dev, before.st_ino, before.st_size,
    before.st_mtime_ns, before.st_ctime_ns,
) != (
    after.st_dev, after.st_ino, after.st_size,
    after.st_mtime_ns, after.st_ctime_ns,
):
    raise SystemExit(1)
payload = json.loads(raw.decode("utf-8", errors="strict"))
helper_names = (
    "bootstrap_runtime", "safe_io", "metadata_firewall", "operation_gate",
    "operation_watchdog", "reboot_runtime", "runtime_contract",
)
expected_keys = {
    "schema_version", "operation", "state", "deploy_ref", "helper_ref",
    "helper_sha256", "startup_contract_sha256", "updated_at",
}
helper_hashes = payload.get("helper_sha256") if isinstance(payload, dict) else None
expected_hashes = {
    name: hashlib.sha256(read_owned(pathlib.Path(raw_path), 0o700 if name in {"bootstrap_runtime", "reboot_runtime"} else 0o755, 2 * 1024 * 1024)).hexdigest()
    for name, raw_path in zip(helper_names, sys.argv[6:], strict=True)
}
candidate = json.loads(
    read_owned(pathlib.Path(sys.argv[5]), 0o600, 32768).decode("utf-8", errors="strict")
)
if (
    not isinstance(payload, dict)
    or set(payload) != expected_keys
    or payload.get("schema_version") != 2
    or payload.get("operation") != "foundation-ready"
    or payload.get("state") != "awaiting-runtime-authority"
    or payload.get("deploy_ref") != sys.argv[2]
    or payload.get("helper_ref") != sys.argv[3]
    or payload.get("startup_contract_sha256") != sys.argv[4]
    or not isinstance(helper_hashes, dict)
    or helper_hashes != expected_hashes
    or candidate != {
        "schema_version": 2,
        "deploy_ref": sys.argv[2],
        "helper_ref": sys.argv[3],
        "startup_contract_sha256": sys.argv[4],
        "helper_sha256": expected_hashes,
    }
):
    raise SystemExit(1)
print("foundation-ready/awaiting-runtime-authority")
PY
)" || HANDOFF_STATE=""
  if [[ "${HANDOFF_STATE}" == "foundation-ready/awaiting-runtime-authority" ]]; then
    write_foundation_receipt
    echo "[omega-startup] foundation-ready/awaiting-runtime-authority; all 26 service images remain fenced" >&2
    exit 0
  fi
  # A reviewed controller/release transition consumes only the exact durable
  # foundation state under the watchdog lock. It never unmasks Docker. The
  # fresh controller then creates a new marker and publishes its own handoff.
  # The old identity is an input signed into the next controller's startup
  # contract by its reviewed plan. Never derive expected-old from the mutable
  # marker being consumed: that would turn the CAS into a tautology.
  if [[ ! "${EXPECTED_FOUNDATION_MARKER_SHA256}" =~ ^[0-9a-f]{64}$ || \
        ! "${EXPECTED_FOUNDATION_WATCHDOG_STATE_SHA256}" =~ ^[0-9a-f]{64}$ || \
        ! "${EXPECTED_FOUNDATION_DEPLOY_REF}" =~ ^[0-9a-f]{40}$ || \
        ! "${EXPECTED_FOUNDATION_HELPER_REF}" =~ ^[0-9a-f]{40}$ || \
        ! "${EXPECTED_FOUNDATION_STARTUP_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "[omega-startup] reviewed expected-old foundation identity is absent" >&2
    exit 75
  fi
  # Recreate only the exact marker-bound foundation state when a prior
  # consumer crashed after deleting state but before deleting the marker.
  if "${WATCHDOG}" foundation-handoff 0 "${OPERATION_MARKER}" && \
      "${WATCHDOG}" foundation-consume 0 "${OPERATION_MARKER}" 0 \
      unused "${SOURCE_SHA}" "${CONTROLLER_REF}" \
      "${EXPECTED_FOUNDATION_MARKER_SHA256}" \
      "${EXPECTED_FOUNDATION_DEPLOY_REF}" \
      "${EXPECTED_FOUNDATION_HELPER_REF}" \
      "${EXPECTED_FOUNDATION_STARTUP_SHA256}" \
      "${EXPECTED_FOUNDATION_WATCHDOG_STATE_SHA256}"; then
    echo "[omega-startup] consumed prior fenced foundation authority by CAS"
  else
    echo "[omega-startup] incomplete canonical operation; Docker remains fenced" >&2
    exit 75
  fi
fi

# Existing hosts recover only after the exact durable state pair is verified.
# No package operation or artifact download precedes this branch.
if [[ -e "${STATE_LINK}" || -L "${STATE_LINK}" ]]; then
  if ! command -v iptables >/dev/null || ! command -v ip6tables >/dev/null; then
    echo "[omega-startup] metadata firewall tools are missing; Docker remains fenced" >&2
    exit 76
  fi
  "${METADATA_FIREWALL_SOURCE}" install || {
    echo "[omega-startup] metadata firewall install/read-back failed; Docker remains fenced" >&2
    exit 76
  }
  "${SAFE_IO}" docker-runtime install-contract
  systemctl daemon-reload
  "${SAFE_IO}" docker-runtime verify-prestart
  if [[ ! -x /usr/local/sbin/omega-operation-gate ]] || \
      ! OMEGA_GCP_LEGACY_PROVENANCE_UPGRADE=1 \
        /usr/local/sbin/omega-operation-gate; then
    echo "[omega-startup] canonical runtime-state verification failed; Docker remains fenced" >&2
    exit 76
  fi
  if [[ ! -L "${CURRENT_DIR}" || ! -s "${SHARED_ENV}" || \
        ! -s "${GCP_RUNTIME_COMPOSE}" || ! -s "${RUNTIME_PROVENANCE}" ]]; then
    echo "[omega-startup] canonical shared inputs are incomplete" >&2
    exit 76
  fi
  echo "[omega-startup] bootstrap already complete; recovering exact current without download/build/pull"
  exec env OMEGA_GCP_APP_ROOT="${APP_ROOT}" OMEGA_GCP_COMPOSE_PROJECT=infra \
    OMEGA_GCP_RUNTIME_CONTRACT="${RUNTIME_CONTRACT_SOURCE}" \
    OMEGA_GCP_SAFE_IO="${SAFE_IO}" OMEGA_GCP_WATCHDOG="${WATCHDOG}" \
    OMEGA_GCP_STARTUP_TERMINAL_RECEIPT="${TERMINAL_RECEIPT}" \
    OMEGA_GCP_STARTUP_CONTRACT_SHA256="${STARTUP_CONTRACT_SHA256}" \
    OMEGA_GCP_CONTROLLER_REF="${CONTROLLER_REF}" \
    /bin/bash -p "${REBOOT_RUNTIME_SOURCE}"
fi

install -d -m 0755 "${APP_ROOT}/releases" "${SHARED_ROOT}"
install -d -m 0700 "${STATE_BUNDLES_ROOT}"
/usr/bin/python3 -I - "${OPERATION_MARKER}" "${SOURCE_SHA}" <<'PY'
import json, os, pathlib, stat, sys, tempfile
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
payload = {"schema_version": 1, "operation": "bootstrap", "state": "initializing",
           "deploy_ref": sys.argv[2], "updated_at": datetime.now(timezone.utc).isoformat()}
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
    # link(2) is an atomic no-overwrite publish. It fails for every existing
    # destination, including a dangling symlink created after the lstat above.
    os.link(temporary, path, follow_symlinks=False)
    os.unlink(temporary)
    temporary = ""
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
finally:
    if temporary and os.path.lexists(temporary): os.unlink(temporary)
PY

# Exercise the permanent gate directly without publishing a daemon-wide bypass.
# Package hooks and presets remain unable to start Docker until the watchdog is
# armed below.
OMEGA_GCP_INITIAL_BOOTSTRAP=1 OMEGA_GCP_ALLOW_OPERATION_MARKER=1 \
  /usr/local/sbin/omega-operation-gate

bootstrap_fail_closed() {
  local rc=$?
  trap - EXIT
  set +e
  if [[ "${BOOTSTRAP_MUTATION_STARTED:-0}" == "1" ]]; then
    echo "[omega-startup] bootstrap incomplete; hard-fencing Docker with durable evidence" >&2
    if ! OMEGA_GCP_SAFE_IO="${SAFE_IO}" \
        "${WATCHDOG}" fence-failure "$$" "${OPERATION_MARKER}"; then
      echo "[omega-startup] hard fence did not complete" >&2
      rc=90
    fi
  fi
  exit "${rc}"
}

BOOTSTRAP_MUTATION_STARTED=1
trap bootstrap_fail_closed EXIT

export DEBIAN_FRONTEND=noninteractive
APT_AUTOMATION_UNITS=(
  apt-daily.timer
  apt-daily-upgrade.timer
  apt-daily.service
  apt-daily-upgrade.service
  unattended-upgrades.service
)
/usr/bin/timeout --signal=TERM --kill-after=10s 120s \
  systemctl stop "${APT_AUTOMATION_UNITS[@]}" >/dev/null 2>&1 || true
/usr/bin/timeout --signal=TERM --kill-after=10s 120s \
  systemctl disable "${APT_AUTOMATION_UNITS[@]}" >/dev/null 2>&1 || true
/usr/bin/timeout --signal=TERM --kill-after=10s 120s \
  systemctl mask "${APT_AUTOMATION_UNITS[@]}"
for apt_unit in "${APT_AUTOMATION_UNITS[@]}"; do
  apt_active="$(/usr/bin/timeout --signal=TERM --kill-after=2s 10s \
    systemctl is-active "${apt_unit}" 2>/dev/null || true)"
  apt_enabled="$(/usr/bin/timeout --signal=TERM --kill-after=2s 10s \
    systemctl is-enabled "${apt_unit}" 2>/dev/null || true)"
  if [[ "${apt_active}" != "inactive" && "${apt_active}" != "failed" ]] || \
      [[ "${apt_enabled}" != "masked" ]]; then
    echo "[omega-startup] unattended APT automation remains available: ${apt_unit}" >&2
    exit 78
  fi
done
/usr/bin/timeout --signal=TERM --kill-after=30s 1800s \
  /usr/bin/apt-get update
/usr/bin/timeout --signal=TERM --kill-after=30s 1800s \
  /usr/bin/apt-get install -y --no-install-recommends -- "${BASE_PACKAGES[@]}"
verify_installed_packages "${BASE_PACKAGES[@]}"

"${SAFE_IO}" docker-storage paths
if [[ ! -b "${DATA_DISK}" ]]; then
  echo "[omega-startup] canonical Docker data disk is missing" >&2
  exit 77
fi
"${SAFE_IO}" docker-storage verify-unused
set +e
DISK_TYPE="$(/usr/bin/timeout --signal=TERM --kill-after=5s 30s \
  /usr/sbin/blkid -o value -s TYPE "${DATA_DISK}" 2>/dev/null)"
BLKID_RC=$?
DISK_BYTES="$(/usr/bin/timeout --signal=TERM --kill-after=5s 30s \
  /usr/sbin/blockdev --getsize64 "${DATA_DISK}" 2>/dev/null)"
SIZE_RC=$?
set -e
if [[ "${SIZE_RC}" != "0" || ! "${DISK_BYTES}" =~ ^[1-9][0-9]*$ || \
      "${DISK_BYTES}" != "${EXPECTED_DATA_DISK_BYTES}" ]]; then
  echo "[omega-startup] data disk byte size differs from signed startup contract" >&2
  exit 77
fi
if [[ "${BLKID_RC}" == "2" ]]; then
  set +e
  LSBLK_TYPE="$(/usr/bin/timeout --signal=TERM --kill-after=5s 30s \
    /usr/bin/lsblk -nro FSTYPE "${DATA_DISK}" 2>/dev/null)"; LSBLK_RC=$?
  WIPEFS_OUTPUT="$(/usr/bin/timeout --signal=TERM --kill-after=5s 30s \
    /usr/sbin/wipefs -n "${DATA_DISK}" 2>/dev/null)"; WIPEFS_RC=$?
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
  "${SAFE_IO}" docker-storage verify-unused
  if ! /usr/bin/timeout --signal=TERM --kill-after=30s 7200s \
      /usr/bin/cmp --silent --bytes="${DISK_BYTES}" "${DATA_DISK}" /dev/zero; then
    echo "[omega-startup] unformatted data disk is not byte-for-byte blank" >&2
    exit 77
  fi
  "${SAFE_IO}" docker-storage verify-unused
  /usr/bin/timeout --signal=TERM --kill-after=30s 1800s \
    /usr/sbin/mkfs.ext4 -F "${DATA_DISK}"
elif [[ "${BLKID_RC}" != "0" || "${DISK_TYPE}" != "ext4" ]]; then
  echo "[omega-startup] data disk identity is unknown or not ext4" >&2
  exit 77
else
  # On a state-less first boot, accepting an arbitrary ext4 disk could revive
  # stale Docker containers. A clean superblock and a read-only fsck are
  # required before inspecting without journal replay; otherwise the later
  # normal mount could materialize journaled state that was never reviewed.
  EXT4_STATE="$(LC_ALL=C /usr/bin/timeout --signal=TERM --kill-after=5s 60s \
    /usr/sbin/tune2fs -l "${DATA_DISK}" 2>/dev/null | \
    awk -F: '$1 == "Filesystem state" {gsub(/^[[:space:]]+/, "", $2); print $2; exit}')"
  EXT4_FEATURES="$(LC_ALL=C /usr/bin/timeout --signal=TERM --kill-after=5s 60s \
    /usr/sbin/tune2fs -l "${DATA_DISK}" 2>/dev/null | \
    awk -F: '$1 == "Filesystem features" {gsub(/^[[:space:]]+/, "", $2); print $2; exit}')"
  if [[ "${EXT4_STATE}" != "clean" || " ${EXT4_FEATURES} " == *" needs_recovery "* ]] || \
      ! LC_ALL=C /usr/bin/timeout --signal=TERM --kill-after=30s 1800s \
        /usr/sbin/e2fsck -fn "${DATA_DISK}" >/dev/null 2>&1; then
    echo "[omega-startup] existing ext4 data disk is dirty or requires recovery" >&2
    exit 77
  fi
  # With no pending journal recovery, the noload view is the view the normal
  # mount will expose. Accept only an empty filesystem (apart from lost+found).
  "${SAFE_IO}" docker-storage verify-unused
  DISK_CHECK="$(mktemp -d /run/omega-disk-check.XXXXXX)"
  if ! /usr/bin/timeout --signal=TERM --kill-after=5s 60s \
      /usr/bin/mount -o ro,noload "${DATA_DISK}" "${DISK_CHECK}"; then
    rmdir "${DISK_CHECK}"
    echo "[omega-startup] existing ext4 data disk cannot be inspected" >&2
    exit 77
  fi
  set +e
  DISK_CONTENT="$(/usr/bin/timeout --signal=TERM --kill-after=5s 60s \
    /usr/bin/find -P "${DISK_CHECK}" -mindepth 1 -maxdepth 1 \
    ! -name lost+found -print -quit)"
  DISK_CONTENT_RC=$?
  LOST_FOUND_CONTENT="unsafe"
  LOST_FOUND_IDENTITY="$(stat -c '%u:%g:%a' -- \
    "${DISK_CHECK}/lost+found" 2>/dev/null || true)"
  LOST_FOUND_RC=1
  if [[ ! -L "${DISK_CHECK}/lost+found" && -d "${DISK_CHECK}/lost+found" && \
        "${LOST_FOUND_IDENTITY}" == "0:0:700" ]]; then
    LOST_FOUND_CONTENT="$(/usr/bin/timeout --signal=TERM --kill-after=5s 60s \
      /usr/bin/find -P "${DISK_CHECK}/lost+found" -mindepth 1 -print -quit)"
    LOST_FOUND_RC=$?
  fi
  set -e
  if ! /usr/bin/timeout --signal=TERM --kill-after=5s 60s \
      /usr/bin/umount "${DISK_CHECK}"; then
    echo "[omega-startup] existing ext4 probe could not be unmounted" >&2
    exit 77
  fi
  rmdir "${DISK_CHECK}"
  if [[ "${DISK_CONTENT_RC}" != "0" || "${LOST_FOUND_RC}" != "0" || \
        -n "${DISK_CONTENT}" || -n "${LOST_FOUND_CONTENT}" ]]; then
    echo "[omega-startup] existing ext4 data disk is not a blank filesystem" >&2
    exit 77
  fi
fi
"${SAFE_IO}" docker-storage prepare
"${SAFE_IO}" docker-storage verify-unused
/usr/bin/timeout --signal=TERM --kill-after=5s 60s \
  /usr/bin/mount -t ext4 -o discard "${DATA_DISK}" /var/lib/docker
"${SAFE_IO}" docker-storage verify-mounted

"${SAFE_IO}" docker-apt-paths
DOCKER_GPG_TMP="$(mktemp /etc/apt/keyrings/.docker.asc.XXXXXX)"
curl -q -fsSL --proto '=https' --proto-redir '=https' \
  --connect-timeout 10 --max-time 60 \
  --cacert /etc/ssl/certs/ca-certificates.crt \
  https://download.docker.com/linux/ubuntu/gpg -o "${DOCKER_GPG_TMP}"
if [[ ! -s "${DOCKER_GPG_TMP}" || \
      "$(stat -c '%s' -- "${DOCKER_GPG_TMP}")" -gt 1048576 ]]; then
  echo "[omega-startup] Docker signing key has invalid bounded size" >&2
  exit 78
fi
DOCKER_GPG_INVENTORY="$(/usr/bin/timeout --signal=TERM --kill-after=5s 60s \
  /usr/bin/gpg --batch --no-options --no-default-keyring \
  --show-keys --with-colons "${DOCKER_GPG_TMP}")"
mapfile -t DOCKER_GPG_RECORD_TYPES < <(printf '%s\n' "${DOCKER_GPG_INVENTORY}" | \
  awk -F: '{print $1}')
mapfile -t DOCKER_GPG_FINGERPRINTS < <(printf '%s\n' "${DOCKER_GPG_INVENTORY}" | \
  awk -F: '$1 == "fpr" {print $10}')
mapfile -t DOCKER_GPG_UIDS < <(printf '%s\n' "${DOCKER_GPG_INVENTORY}" | \
  awk -F: '$1 == "uid" {print $10}')
if [[ "${DOCKER_GPG_RECORD_TYPES[*]}" != "pub fpr uid sub fpr" || \
      "${#DOCKER_GPG_FINGERPRINTS[@]}" != "2" || \
      "${DOCKER_GPG_FINGERPRINTS[0]}" != \
        "9DC858229FC7DD38854AE2D88D81803C0EBFCD88" || \
      "${DOCKER_GPG_FINGERPRINTS[1]}" != \
        "D3306D018370199E527AE7997EA0A9C3F273FCD8" || \
      "${#DOCKER_GPG_UIDS[@]}" != "1" || \
      "${DOCKER_GPG_UIDS[0]}" != \
        "Docker Release (CE deb) <docker@docker.com>" ]]; then
  echo "[omega-startup] Docker signing key inventory differs" >&2
  exit 78
fi
unset DOCKER_GPG_INVENTORY DOCKER_GPG_RECORD_TYPES \
  DOCKER_GPG_FINGERPRINTS DOCKER_GPG_UIDS
chmod 0644 "${DOCKER_GPG_TMP}"
"${SAFE_IO}" fsync-file "${DOCKER_GPG_TMP}"
mv -Tf "${DOCKER_GPG_TMP}" /etc/apt/keyrings/docker.asc
"${SAFE_IO}" fsync-dir /etc/apt/keyrings
VERSION_CODENAME="$("${SAFE_IO}" ubuntu-codename)"
DOCKER_ARCH="$(dpkg --print-architecture)"
if [[ ! "${DOCKER_ARCH}" =~ ^[a-z0-9][a-z0-9-]*$ || \
      ! "${VERSION_CODENAME:-}" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "[omega-startup] Docker apt platform identity is invalid" >&2
  exit 78
fi
DOCKER_LIST_TMP="$(mktemp /etc/apt/sources.list.d/.docker.list.XXXXXX)"
printf '%s\n' \
  "deb [arch=${DOCKER_ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > "${DOCKER_LIST_TMP}"
chmod 0644 "${DOCKER_LIST_TMP}"
"${SAFE_IO}" fsync-file "${DOCKER_LIST_TMP}"
mv -Tf "${DOCKER_LIST_TMP}" /etc/apt/sources.list.d/docker.list
"${SAFE_IO}" fsync-dir /etc/apt/sources.list.d
DOCKER_PREFERENCES_TMP="$(mktemp /etc/apt/preferences.d/.omega-docker.XXXXXX)"
printf '%s\n' \
  'Package: docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin' \
  'Pin: origin "download.docker.com"' \
  'Pin-Priority: 1001' > "${DOCKER_PREFERENCES_TMP}"
chmod 0644 "${DOCKER_PREFERENCES_TMP}"
"${SAFE_IO}" fsync-file "${DOCKER_PREFERENCES_TMP}"
mv -Tf "${DOCKER_PREFERENCES_TMP}" /etc/apt/preferences.d/omega-docker
"${SAFE_IO}" fsync-dir /etc/apt/preferences.d
/usr/bin/python3 -I - <<'PY' || {
import os
import pathlib
import stat

paths = [pathlib.Path("/etc/apt/sources.list")]
directory = pathlib.Path("/etc/apt/sources.list.d")
try:
    entries = sorted(directory.iterdir(), key=lambda path: os.fsencode(path.name))
except OSError:
    raise SystemExit(1) from None
paths.extend(entries)
for path in paths:
    try:
        info = path.lstat()
    except FileNotFoundError:
        continue
    except OSError:
        raise SystemExit(1) from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SystemExit(1)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if before.st_size > 1024 * 1024:
            raise SystemExit(1)
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise SystemExit(1)
    finally:
        os.close(descriptor)
    if path != pathlib.Path("/etc/apt/sources.list.d/docker.list") and b"download.docker.com" in raw:
        raise SystemExit(1)
PY
  echo "[omega-startup] Docker apt source inventory is unsafe or duplicated" >&2
  exit 78
}
/usr/bin/timeout --signal=TERM --kill-after=30s 1800s \
  /usr/bin/apt-get update
for specification in "${DOCKER_PACKAGES[@]}"; do
  package="${specification%%=*}"
  expected="${specification#*=}"
  matching_origin="$(/usr/bin/apt-cache madison "${package}" | /usr/bin/awk -v version="${expected}" '
    $2 == version && $0 ~ /https:\/\/download[.]docker[.]com\/linux\/ubuntu/ { count++ }
    END { print count + 0 }
  ')"
  if [[ "${matching_origin}" != "1" ]]; then
    echo "[omega-startup] exact Docker package origin/version differs: ${package}" >&2
    exit 78
  fi
done
unset package expected matching_origin specification
/usr/bin/timeout --signal=TERM --kill-after=30s 1800s \
  /usr/bin/apt-get install -y --no-install-recommends -- "${DOCKER_PACKAGES[@]}"
verify_installed_packages "${DOCKER_PACKAGES[@]}"
systemctl disable docker.service docker.socket containerd.service >/dev/null
"${SAFE_IO}" docker-runtime install-contract
"${METADATA_FIREWALL_SOURCE}" install

# PR1 is host foundation only. It must never build/pull mutable application
# inputs. A later foundation PR may make this signed flag true only together
# with exact digest/pre-pull/local-ID authority for all 26 services.
if [[ "${EXACT_RUNTIME_CONTRACT_READY}" != "true" ]]; then
  /usr/bin/python3 -I - "${OPERATION_MARKER}" "${SOURCE_SHA}" \
    "${CONTROLLER_REF}" "${STARTUP_CONFIG_FILE}" "$0" "${SAFE_IO}" \
    /usr/local/sbin/omega-metadata-firewall \
    /usr/local/sbin/omega-operation-gate "${WATCHDOG}" \
    "${REBOOT_RUNTIME_SOURCE}" "${CANONICAL_RUNTIME_CONTRACT}" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
import os
import pathlib
import stat
import sys
import tempfile

marker = pathlib.Path(sys.argv[1])


def read_owned(path: pathlib.Path, mode: int, maximum: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or stat.S_IMODE(before.st_mode) != mode
            or before.st_nlink != 1
            or not 1 <= before.st_size <= maximum
        ):
            raise SystemExit(1)
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise SystemExit(1)
        return raw
    finally:
        os.close(descriptor)


old = json.loads(read_owned(marker, 0o600, 32768).decode("utf-8", errors="strict"))
if (
    set(old) != {"schema_version", "operation", "state", "deploy_ref", "updated_at"}
    or old.get("schema_version") != 1
    or old.get("operation") != "bootstrap"
    or old.get("state") != "initializing"
    or old.get("deploy_ref") != sys.argv[2]
):
    raise SystemExit(1)
paths = {
    "startup_contract_sha256": (pathlib.Path(sys.argv[4]), 0o600),
    "bootstrap_runtime_sha256": (pathlib.Path(sys.argv[5]), 0o700),
    "safe_io_sha256": (pathlib.Path(sys.argv[6]), 0o755),
    "metadata_firewall_sha256": (pathlib.Path(sys.argv[7]), 0o755),
    "operation_gate_sha256": (pathlib.Path(sys.argv[8]), 0o755),
    "operation_watchdog_sha256": (pathlib.Path(sys.argv[9]), 0o755),
    "reboot_runtime_sha256": (pathlib.Path(sys.argv[10]), 0o700),
    "runtime_contract_sha256": (pathlib.Path(sys.argv[11]), 0o755),
}
payload = {
    "schema_version": 2,
    "operation": "foundation-ready",
    "state": "awaiting-runtime-authority",
    "deploy_ref": sys.argv[2],
    "helper_ref": sys.argv[3],
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "startup_contract_sha256": hashlib.sha256(
        read_owned(paths.pop("startup_contract_sha256")[0], 0o600, 2 * 1024 * 1024)
    ).hexdigest(),
    "helper_sha256": {
        key.removesuffix("_sha256"): hashlib.sha256(
            read_owned(path, mode, 2 * 1024 * 1024)
        ).hexdigest()
        for key, (path, mode) in paths.items()
    },
}
descriptor, temporary = tempfile.mkstemp(prefix=f".{marker.name}.", dir=marker.parent)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, marker)
    temporary = ""
    directory = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if temporary and os.path.exists(temporary):
        os.unlink(temporary)
PY
  "${WATCHDOG}" foundation-handoff 0 "${OPERATION_MARKER}"
  "${WATCHDOG}" foundation-check 0 "${OPERATION_MARKER}" >/dev/null
  BOOTSTRAP_MUTATION_STARTED=0
  trap - EXIT
  write_foundation_receipt
  echo "[omega-startup] exact 26-service runtime authority is absent; foundation handoff remains fenced" >&2
  exit 0
fi

# Package installation and persistent disable complete while the daemon has no
# start authorization. Publish the volatile drop-in only immediately before
# arming the independent owner monitor and starting the runtime.
"${SAFE_IO}" runtime-auth-paths
INITIAL_AUTH_TMP="$(mktemp \
  /run/systemd/system/docker.service.d/.omega-initial-bootstrap.conf.XXXXXX)"
printf '%s\n' '[Service]' \
  'Environment=OMEGA_GCP_INITIAL_BOOTSTRAP=1' \
  'Environment=OMEGA_GCP_ALLOW_OPERATION_MARKER=1' \
  'Environment=OMEGA_GCP_RUNTIME_START_AUTHORIZED=1' \
  > "${INITIAL_AUTH_TMP}"
chmod 0644 "${INITIAL_AUTH_TMP}"
"${SAFE_IO}" fsync-file "${INITIAL_AUTH_TMP}"
mv -Tf "${INITIAL_AUTH_TMP}" \
  /run/systemd/system/docker.service.d/omega-initial-bootstrap.conf
"${SAFE_IO}" fsync-dir /run/systemd/system/docker.service.d
systemctl daemon-reload
"${SAFE_IO}" docker-runtime verify-prestart
"${WATCHDOG}" arm "$$" "${OPERATION_MARKER}" 7200
echo "[omega-startup] independent operation watchdog armed before Docker start"
systemctl unmask --runtime docker.service docker.socket containerd.service
systemctl daemon-reload
for service in docker.service docker.socket containerd.service; do
  unit_state="$(systemctl is-enabled "$service" 2>/dev/null || true)"
  case "$unit_state" in
    disabled|static|indirect) ;;
    *)
      echo "[omega-startup] persistent runtime autostart remains enabled: ${service}=${unit_state:-unknown}" >&2
      exit 79
      ;;
  esac
done
systemctl start containerd.service docker.socket docker.service
systemctl is-active --quiet containerd.service
systemctl is-active --quiet docker.socket
systemctl is-active --quiet docker.service
"${SAFE_IO}" docker-runtime verify-live
"${METADATA_FIREWALL_SOURCE}" verify

download_source() {
  "${SAFE_IO}" gcs-download \
    --uri "gs://${SOURCE_BUCKET}/${SOURCE_OBJECT}" \
    --generation "${SOURCE_GENERATION}" \
    --size "${SOURCE_SIZE_BYTES}" \
    --sha256 "${SOURCE_ARCHIVE_SHA256}" \
    --output "${STARTUP_CONFIG_DIR}/source.tar.gz"
}

secret_value() {
  local name="$1" version="$2"
  "${SAFE_IO}" secret-access --project "${PROJECT_ID}" \
    --secret "${SECRET_PREFIX}${name}" --version "${version}"
}

set_env() {
  local key="$1" value="$2"
  printf '%s' "${value}" | "${SAFE_IO}" env-set --path "$PWD/infra/.env" --key "${key}"
}

load_required_secret() {
  local secret_name="$1" env_name="$2" version="$3"
  local value
  if [[ ! "${version}" =~ ^[1-9][0-9]*$ ]] || \
      ! value="$(secret_value "${secret_name}" "${version}")"; then
    echo "[omega-startup] required Secret Manager value unavailable: ${secret_name}" >&2
    exit 1
  fi
  printf -v "${env_name}" '%s' "${value}"
  export "${env_name}"
}

download_source
RELEASE_STAGE="$(mktemp -d "${APP_ROOT}/releases/.${SOURCE_SHA}.XXXXXX")"
"${SAFE_IO}" safe-extract --archive "${STARTUP_CONFIG_DIR}/source.tar.gz" \
  --destination "${RELEASE_STAGE}"
mv "${RELEASE_STAGE}" "${RELEASE_DIR}"
"${SAFE_IO}" fsync-dir "${APP_ROOT}/releases"
"${SAFE_IO}" symlink-publish --target "${RELEASE_DIR}" --link "${CURRENT_DIR}"
CURRENT_RESOLVED="$(readlink -f -- "${CURRENT_DIR}")"
if [[ "${CURRENT_RESOLVED}" != "${RELEASE_DIR}" || \
      ! "${CURRENT_RESOLVED}" =~ ^${APP_ROOT}/releases/[0-9a-f]{40}$ ]]; then
  echo "[omega-startup] current release publication differs" >&2
  exit 1
fi
cd "${RELEASE_DIR}"

RELEASE_WATCHDOG="${CURRENT_DIR}/scripts/gcp/operation-watchdog.sh"
RELEASE_REBOOT="${CURRENT_DIR}/scripts/gcp/reboot-runtime.sh"
RELEASE_CONTRACT="${CURRENT_DIR}/scripts/gcp/runtime_contract.py"
RELEASE_SAFE_IO="${CURRENT_DIR}/scripts/gcp/safe_io.py"
RELEASE_BOOTSTRAP="${CURRENT_DIR}/scripts/gcp/bootstrap-runtime.sh"
RELEASE_FIREWALL="${CURRENT_DIR}/scripts/gcp/metadata-firewall.sh"
RELEASE_GATE="${CURRENT_DIR}/infra/terraform-gcp/templates/omega-operation-gate"
if [[ ! -x "${RELEASE_WATCHDOG}" || ! -x "${RELEASE_REBOOT}" || \
      ! -x "${RELEASE_CONTRACT}" || ! -x "${RELEASE_SAFE_IO}" || \
      ! -x "${RELEASE_BOOTSTRAP}" || ! -x "${RELEASE_FIREWALL}" || \
      ! -x "${RELEASE_GATE}" ]] || \
    ! cmp -s "${RELEASE_WATCHDOG}" "${WATCHDOG}" || \
    ! cmp -s "${REBOOT_RUNTIME_SOURCE}" "${RELEASE_REBOOT}" || \
    ! cmp -s "${RUNTIME_CONTRACT_SOURCE}" "${RELEASE_CONTRACT}" || \
    ! cmp -s "${SAFE_IO}" "${RELEASE_SAFE_IO}" || \
    ! cmp -s "$0" "${RELEASE_BOOTSTRAP}" || \
    ! cmp -s "${METADATA_FIREWALL_SOURCE}" "${RELEASE_FIREWALL}" || \
    ! cmp -s "${OPERATION_GUARD_SOURCE}" "${RELEASE_GATE}"; then
  echo "[omega-startup] embedded/release safety helper identity differs" >&2
  exit 78
fi

mkdir -p "$(dirname "${SHARED_ENV}")"

load_required_secret control_room_evidence_signing_key_id \
  CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID "${CONTROL_ROOM_KEY_ID_VERSION}"
load_required_secret control_room_evidence_signing_key \
  CONTROL_ROOM_EVIDENCE_SIGNING_KEY "${CONTROL_ROOM_KEY_VERSION}"
load_required_secret control_room_evidence_signing_previous_keys \
  CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS "${CONTROL_ROOM_PREVIOUS_KEYS_VERSION}"

if [[ ! -f infra/.env ]]; then
  /bin/bash -p infra/bootstrap.sh
fi
chmod 600 infra/.env
"${SAFE_IO}" env-validate --path "$PWD/infra/.env" --forbid-prefix OMEGA_MIGRATION_ >/dev/null
set_env CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID "${CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID}"
set_env CONTROL_ROOM_EVIDENCE_SIGNING_KEY "${CONTROL_ROOM_EVIDENCE_SIGNING_KEY}"
set_env CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS "${CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS}"
/bin/bash -p infra/bootstrap-keys.sh infra/.env
chmod 600 infra/.env
/usr/bin/install -d -o 0 -g 0 -m 0755 airflow/plugins \
  airflow/dags/sap_successfactors airflow/dags/sap_hcm \
  airflow/dags/sap_s4hana airflow/dags/replicon airflow/dags/hubspot \
  airflow/dags/salesforce airflow/dags/banxico airflow/dags/inegi \
  airflow/dags/sec_edgar

set_env APP_ENV production
set_env COOKIE_SECURE "${COOKIE_SECURE}"
set_env CONSOLE_URL "${PUBLIC_CONSOLE_URL}"
set_env WORKSPACE_PUBLIC_URL "${PUBLIC_WORKSPACE_URL}"
ALLOWED_ORIGINS_VALUE="${PUBLIC_CONSOLE_URL},${PUBLIC_WORKSPACE_URL}"
if [[ -n "${TECHNICAL_CONSOLE_URL}" && -n "${TECHNICAL_WORKSPACE_URL}" ]]; then
  ALLOWED_ORIGINS_VALUE+=",${TECHNICAL_CONSOLE_URL},${TECHNICAL_WORKSPACE_URL}"
fi
set_env ALLOWED_ORIGINS "${ALLOWED_ORIGINS_VALUE}"
unset ALLOWED_ORIGINS_VALUE
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

if [[ -n "${GCS_HMAC_ACCESS_KEY_VERSION}" && \
      -n "${GCS_HMAC_SECRET_KEY_VERSION}" ]]; then
  hmac_access_key="$("${SAFE_IO}" secret-access --project "${PROJECT_ID}" \
    --secret "${SECRET_PREFIX}gcs_hmac_access_key_id" \
    --version "${GCS_HMAC_ACCESS_KEY_VERSION}")"
  hmac_secret_key="$("${SAFE_IO}" secret-access --project "${PROJECT_ID}" \
    --secret "${SECRET_PREFIX}gcs_hmac_secret_access_key" \
    --version "${GCS_HMAC_SECRET_KEY_VERSION}")"
  set_env AWS_ACCESS_KEY_ID "${hmac_access_key}"
  set_env MINIO_ACCESS_KEY "${hmac_access_key}"
  set_env GCS_ACCESS_KEY_ID "${hmac_access_key}"
  set_env LAKEHOUSE_ACCESS_KEY "${hmac_access_key}"
  set_env AWS_SECRET_ACCESS_KEY "${hmac_secret_key}"
  set_env MINIO_SECRET_KEY "${hmac_secret_key}"
  set_env GCS_SECRET_ACCESS_KEY "${hmac_secret_key}"
  set_env LAKEHOUSE_SECRET_KEY "${hmac_secret_key}"
elif [[ -z "${GCS_HMAC_ACCESS_KEY_VERSION}" && \
        -z "${GCS_HMAC_SECRET_KEY_VERSION}" ]]; then
  unset hmac_access_key hmac_secret_key
else
  unset hmac_access_key hmac_secret_key
  echo "[omega-startup] GCS HMAC secret pair is partial or unreadable" >&2
  exit 1
fi
unset hmac_access_key hmac_secret_key

# Publish the fully reconciled GCP runtime env only after every production,
# lakehouse, and optional HMAC value has been applied.  Copying it earlier
# leaves day-2 releases with the bootstrap defaults instead of the live GCP
# contract.
SHARED_ENV_TMP="$(mktemp "${SHARED_ROOT}/.infra.env.${SOURCE_SHA}.XXXXXX")"
install -m 0600 infra/.env "${SHARED_ENV_TMP}"
"${SAFE_IO}" fsync-file "${SHARED_ENV_TMP}"
mv -Tf "${SHARED_ENV_TMP}" "${SHARED_ENV}"
"${SAFE_IO}" env-validate --path "${SHARED_ENV}" --forbid-prefix OMEGA_MIGRATION_ >/dev/null
"${SAFE_IO}" fsync-dir "${SHARED_ROOT}"

/usr/bin/python3 -I - "${STARTUP_CONFIG}" "$PWD/infra/docker-compose.gcp.yml" <<'PY'
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
GCP_RUNTIME_COMPOSE_TMP="$(mktemp \
  "${SHARED_ROOT}/.docker-compose.gcp.yml.${SOURCE_SHA}.XXXXXX")"
install -m 0600 infra/docker-compose.gcp.yml "${GCP_RUNTIME_COMPOSE_TMP}"
"${SAFE_IO}" fsync-file "${GCP_RUNTIME_COMPOSE_TMP}"
mv -Tf "${GCP_RUNTIME_COMPOSE_TMP}" "${GCP_RUNTIME_COMPOSE}"
"${SAFE_IO}" fsync-dir "${SHARED_ROOT}"

# Production code/config lives only in one immutable release tree. Mutable
# Airflow logs and MinIO data are named Docker volumes; every code bind is RO.
if ! /usr/bin/find -P "${RELEASE_DIR}" -xdev -type f -exec chmod a-w -- {} +; then
  echo "[omega-startup] release file sealing failed" >&2
  exit 1
fi
if ! /usr/bin/find -P "${RELEASE_DIR}" -xdev -depth -type d -exec chmod a-w -- {} +; then
  echo "[omega-startup] release directory sealing failed" >&2
  exit 1
fi
RELEASE_TREE_SHA256="$("${SAFE_IO}" tree-sha256 \
  --root "${RELEASE_DIR}" --require-read-only)"
CURRENT_RESOLVED_AFTER_SEAL="$(readlink -f -- "${CURRENT_DIR}")"
if [[ ! "${RELEASE_TREE_SHA256}" =~ ^[0-9a-f]{64}$ || \
      "${CURRENT_RESOLVED_AFTER_SEAL}" != "${RELEASE_DIR}" || \
      "${CURRENT_RESOLVED_AFTER_SEAL}" != "${CURRENT_RESOLVED}" ]]; then
  echo "[omega-startup] immutable release tree contract failed" >&2
  exit 1
fi

COMPOSE=(docker compose --project-name infra --env-file infra/.env \
  -f infra/docker-compose.yml -f infra/docker-compose.gcp.yml --profile sap)
"${COMPOSE[@]}" config -q
COMPOSE_SECURITY_JSON="$(mktemp /run/omega-compose-security.XXXXXX)"
"${COMPOSE[@]}" config --format json > "${COMPOSE_SECURITY_JSON}"
chmod 0600 "${COMPOSE_SECURITY_JSON}"
"${RELEASE_CONTRACT}" compose-security --app-root "${APP_ROOT}" \
  --path "${COMPOSE_SECURITY_JSON}" >/dev/null || {
  rm -f -- "${COMPOSE_SECURITY_JSON}"
  echo "[omega-startup] rendered Compose static security contract failed" >&2
  exit 1
}
rm -f -- "${COMPOSE_SECURITY_JSON}"
"${COMPOSE[@]}" up --no-build --pull never -d
if [[ "${CANONICAL_WRITER}" == "false" ]]; then
  "${COMPOSE[@]}" stop airflow-scheduler
fi

echo "[omega-startup] waiting for console readiness"
ready=0
DATA_READY_RESPONSE="$(mktemp /run/omega-data-ready.XXXXXX)"
for _ in $(seq 1 180); do
  if curl -q -fsS --noproxy '*' --max-time 5 \
      http://127.0.0.1:8000/readyz >/dev/null 2>&1 && \
      curl -q -fsS --noproxy '*' --max-time 5 \
      'http://127.0.0.1:8000/readyz?require_data=1' \
      -o "$DATA_READY_RESPONSE" 2>/dev/null && \
      /usr/bin/python3 -I - "$DATA_READY_RESPONSE" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
raise SystemExit(0 if isinstance(payload, dict) and payload.get("ok") is True else 1)
PY
  then
    ready=1
    break
  fi
  sleep 10
done
rm -f -- "$DATA_READY_RESPONSE"
if [[ "${ready}" != "1" ]]; then
  echo "[omega-startup] console did not become ready"
  "${COMPOSE[@]}" ps || true
  "${COMPOSE[@]}" logs --tail=200 console || true
  exit 1
fi

CREDENTIALS_FILE="${APP_ROOT}/admin_credentials.txt"
ADMIN_PASSWORD="$(/usr/bin/python3 -I - "${CREDENTIALS_FILE}" \
  "${PUBLIC_CONSOLE_URL}" "${ADMIN_EMAIL}" "${SOURCE_SHA}" <<'PY'
import os
import pathlib
import re
import secrets
import stat
import sys

path = pathlib.Path(sys.argv[1])
expected = {"url": sys.argv[2], "email": sys.argv[3], "source_sha": sys.argv[4]}
flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags)
except FileNotFoundError:
    password = secrets.token_hex(24)
    payload = (
        f"url={expected['url']}\nemail={expected['email']}\n"
        f"password={password}\nsource_sha={expected['source_sha']}\n"
    ).encode("utf-8")
    create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, create_flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        if os.write(descriptor, payload) != len(payload):
            raise SystemExit("short credentials write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    print(password)
else:
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 1 <= before.st_size <= 4096
        ):
            raise SystemExit("unsafe credentials descriptor")
        raw = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise SystemExit("credentials changed during authoritative read")
    finally:
        os.close(descriptor)
    rows = raw.decode("utf-8", errors="strict").splitlines()
    parsed = {}
    for row in rows:
        key, separator, value = row.partition("=")
        if not separator or key in parsed:
            raise SystemExit("malformed credentials")
        parsed[key] = value
    if set(parsed) != {*expected, "password"} or any(
        parsed[key] != value for key, value in expected.items()
    ) or re.fullmatch(r"[0-9a-f]{48}", parsed["password"]) is None:
        raise SystemExit("credentials identity differs")
    print(parsed["password"])
PY
)" || {
  echo "[omega-startup] administrator credential record is unsafe" >&2
  exit 1
}

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
  if "${RUNTIME_CONTRACT}" bootstrap-record \
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
/usr/local/sbin/omega-metadata-firewall verify-container >/dev/null || {
  echo "[omega-startup] container metadata isolation verification failed" >&2
  exit 1
}

/usr/bin/python3 -I - "${STATE_STAGE}/bootstrap-state.json" \
  "${STATE_STAGE}/runtime-provenance.json" "${SOURCE_SHA}" "${VERSION}" \
  "${REBOOT_HELPER}" "${RUNTIME_CONTRACT}" "${RELEASE_SAFE_IO}" \
  "gs://${SOURCE_BUCKET}/${SOURCE_OBJECT}" "${SOURCE_GENERATION}" \
  "${SOURCE_SIZE_BYTES}" "${SOURCE_ARCHIVE_SHA256}" \
  "${RELEASE_TREE_SHA256}" "${CONTROL_ROOM_KEY_ID_VERSION}" \
  "${CONTROL_ROOM_KEY_VERSION}" "${CONTROL_ROOM_PREVIOUS_KEYS_VERSION}" \
  "${GCS_HMAC_ACCESS_KEY_VERSION}" "${GCS_HMAC_SECRET_KEY_VERSION}" \
  "${CANONICAL_WRITER}" <<'PY'
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
    "release_tree_sha256": sys.argv[12],
    "canonical_writer": sys.argv[18] == "true",
    "secret_versions": {
        "control_room_evidence_signing_key_id": sys.argv[13],
        "control_room_evidence_signing_key": sys.argv[14],
        "control_room_evidence_signing_previous_keys": sys.argv[15],
        "gcs_hmac_access_key_id": sys.argv[16],
        "gcs_hmac_secret_access_key": sys.argv[17],
    },
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
if [[ -e "${STATE_FINAL}" || -L "${STATE_FINAL}" ]]; then
  echo "[omega-startup] uncommitted bootstrap state bundle already exists" >&2
  rm -rf -- "${STATE_STAGE}"
  exit 1
fi
mv "${STATE_STAGE}" "${STATE_FINAL}"
"${SAFE_IO}" fsync-dir "${STATE_BUNDLES_ROOT}"
"${SAFE_IO}" symlink-publish --target "${STATE_FINAL}" --link "${STATE_LINK}"
if ! OMEGA_GCP_ALLOW_OPERATION_MARKER=1 /usr/local/sbin/omega-operation-gate; then
  echo "[omega-startup] published bootstrap state failed operation-gate verification" >&2
  systemctl stop docker.service docker.socket containerd.service >/dev/null 2>&1 || true
  exit 76
fi

rm -f -- /run/systemd/system/docker.service.d/omega-initial-bootstrap.conf
"${SAFE_IO}" fsync-dir /run/systemd/system/docker.service.d
systemctl daemon-reload
"${WATCHDOG}" complete "$$" "${OPERATION_MARKER}"
write_terminal_receipt live-bootstrapped "${SOURCE_SHA}" "${VERSION}"
BOOTSTRAP_MUTATION_STARTED=0
trap - EXIT

echo "[omega-startup] done $(date -Iseconds)"
