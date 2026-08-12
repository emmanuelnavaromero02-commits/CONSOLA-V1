#!/bin/bash -p
# Independent systemd watchdog and hard runtime fence for canonical GCP writes.
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

UNIT="omega-gcp-operation-watchdog.service"
OWNER_UNIT="omega-gcp-operation.service"
RUNTIME_AUTH_DIR="/run/systemd/system/docker.service.d"
RUNTIME_DIR="/run/omega-gcp"
LOCK_FILE="${RUNTIME_DIR}/watchdog.lock"
FENCE_SENTINEL="${RUNTIME_DIR}/watchdog.fenced"
STATE_FILE="${RUNTIME_DIR}/watchdog-state.json"
SAFE_IO="${OMEGA_GCP_SAFE_IO:-/usr/local/sbin/omega-safe-io}"
ACTION="${1:-}"
WATCHED_PID="${2:-}"
OPERATION_MARKER="${3:-}"
WATCHED_STARTTIME="${4:-}"
WATCHED_DEADLINE="${5:-}"
ADOPTION_DEPLOY_REF="${6:-}"
ADOPTION_HELPER_REF="${7:-}"
EXPECTED_MARKER_SHA256="${8:-}"
EXPECTED_OLD_DEPLOY_REF="${9:-}"
EXPECTED_OLD_HELPER_REF="${10:-}"
EXPECTED_OLD_STARTUP_SHA256="${11:-}"
EXPECTED_OLD_WATCHDOG_STATE_SHA256="${12:-}"
MAX_WATCHDOG_TIMEOUT=86400

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "omega-operation-watchdog: root is required" >&2
  exit 10
fi
if [[ ! "$OPERATION_MARKER" =~ ^/[A-Za-z0-9._/-]+/shared/operation-state\.json$ || \
      "$OPERATION_MARKER" == *"/../"* ]]; then
  echo "omega-operation-watchdog: invalid operation marker" >&2
  exit 11
fi
read -r SAFE_IO_UID SAFE_IO_GID SAFE_IO_MODE < <(
  stat -c '%u %g %a' -- "$SAFE_IO" 2>/dev/null || true
)
if [[ "$SAFE_IO" != /* || -L "$SAFE_IO" || ! -f "$SAFE_IO" || ! -x "$SAFE_IO" || \
      "$SAFE_IO_UID" != "0" || "$SAFE_IO_GID" != "0" || \
      ! "$SAFE_IO_MODE" =~ ^[0-7]{3,4}$ ]] || \
    (( (8#$SAFE_IO_MODE & 8#022) != 0 )); then
  echo "omega-operation-watchdog: safe I/O helper is not root-owned executable code" >&2
  exit 11
fi

prepare_runtime_dir() {
  /usr/bin/python3 -I - "$RUNTIME_DIR" "${EUID:-$(id -u)}" <<'PY'
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
expected_uid = int(sys.argv[2])
try:
    path.mkdir(mode=0o700)
except FileExistsError:
    pass
info = path.lstat()
if (
    stat.S_ISLNK(info.st_mode)
    or not stat.S_ISDIR(info.st_mode)
    or info.st_uid != expected_uid
    or info.st_gid != 0
    or stat.S_IMODE(info.st_mode) != 0o700
):
    raise SystemExit(1)
PY
}

open_watchdog_lock() {
  prepare_runtime_dir || return 1
  # The parent is private and append mode is non-destructive. Validate the
  # exact descriptor that every caller subsequently flocks; never validate one
  # pathname open and then lock a second, replaceable open.
  exec 9>>"$LOCK_FILE"
  if ! /usr/bin/python3 -I - "$LOCK_FILE" "${EUID:-$(id -u)}" <<'PY'
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
expected_uid = int(sys.argv[2])
before = os.fstat(9)
try:
    named = path.lstat()
except OSError:
    raise SystemExit(1) from None
after = os.fstat(9)
identity_before = (
    before.st_dev,
    before.st_ino,
    before.st_mode,
    before.st_uid,
    before.st_gid,
    before.st_nlink,
)
identity_after = (
    after.st_dev,
    after.st_ino,
    after.st_mode,
    after.st_uid,
    after.st_gid,
    after.st_nlink,
)
if (
    identity_before != identity_after
    or stat.S_ISLNK(named.st_mode)
    or not stat.S_ISREG(named.st_mode)
    or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
    or not stat.S_ISREG(before.st_mode)
    or before.st_uid != expected_uid
    or before.st_gid != 0
    or stat.S_IMODE(before.st_mode) != 0o600
    or before.st_nlink != 1
):
    raise SystemExit(1)
PY
  then
    exec 9>&-
    return 1
  fi
  # Acquire first, then repeat the descriptor/name identity check while the
  # same descriptor is locked. This closes replacement between validation and
  # entry into the critical section for every watchdog action.
  if ! flock -x 9; then
    exec 9>&-
    return 1
  fi
  if ! /usr/bin/python3 -I - "$LOCK_FILE" "${EUID:-$(id -u)}" <<'PY'
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
expected_uid = int(sys.argv[2])
descriptor = os.fstat(9)
named = path.lstat()
if (
    stat.S_ISLNK(named.st_mode)
    or not stat.S_ISREG(named.st_mode)
    or (named.st_dev, named.st_ino) != (descriptor.st_dev, descriptor.st_ino)
    or descriptor.st_uid != expected_uid
    or descriptor.st_gid != 0
    or stat.S_IMODE(descriptor.st_mode) != 0o600
    or descriptor.st_nlink != 1
):
    raise SystemExit(1)
PY
  then
    flock -u 9 >/dev/null 2>&1 || :
    exec 9>&-
    return 1
  fi
}

prepare_runtime_dir || {
  echo "omega-operation-watchdog: private runtime directory is unsafe" >&2
  exit 11
}

fsync_dir() {
  /usr/bin/python3 -I - "$1" <<'PY'
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if path.is_dir():
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
PY
}

docker_bounded() {
  timeout --signal=TERM --kill-after=2s 8s docker "$@"
}

systemctl_bounded() {
  timeout --signal=TERM --kill-after=2s 10s systemctl "$@"
}

service_fully_stopped() {
  local service="$1" active substate main_pid control_pid
  active="$(systemctl_bounded show "$service" --property=ActiveState --value 2>/dev/null)" || return 1
  substate="$(systemctl_bounded show "$service" --property=SubState --value 2>/dev/null)" || return 1
  main_pid="$(systemctl_bounded show "$service" --property=MainPID --value 2>/dev/null)" || return 1
  control_pid="$(systemctl_bounded show "$service" --property=ControlPID --value 2>/dev/null)" || return 1
  case "$active:$substate:$main_pid:$control_pid" in
    inactive:dead:0:0|failed:failed:0:0|failed:dead:0:0) return 0 ;;
    *) return 1 ;;
  esac
}

unit_fully_stopped_or_absent() {
  local service="$1" load_state
  load_state="$(systemctl_bounded show "$service" --property=LoadState --value 2>/dev/null)" || return 1
  [[ "$load_state" == "not-found" ]] && return 0
  service_fully_stopped "$service"
}

service_hard_fenced_or_absent() {
  local service="$1" load_state enabled enabled_rc=0
  load_state="$(systemctl_bounded show "$service" --property=LoadState --value 2>/dev/null)" || return 1
  [[ "$load_state" == "not-found" ]] && return 0
  service_fully_stopped "$service" || return 1
  enabled="$(systemctl_bounded is-enabled "$service" 2>/dev/null)" || enabled_rc=$?
  case "$enabled:$enabled_rc" in
    masked:1|masked-runtime:1) return 0 ;;
    *) return 1 ;;
  esac
}

safe_io_bounded() {
  timeout --signal=TERM --kill-after=2s 30s "$SAFE_IO" "$@"
}

monotonic_seconds() {
  local uptime whole
  read -r uptime _ < /proc/uptime || return 1
  whole="${uptime%%.*}"
  [[ "$whole" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$whole"
}

valid_timeout() {
  [[ "$1" =~ ^[1-9][0-9]*$ && "$1" -le "$MAX_WATCHDOG_TIMEOUT" ]]
}

revoke_runtime_authorization() {
  rm -f -- "${RUNTIME_AUTH_DIR}/omega-initial-bootstrap.conf" \
    "${RUNTIME_AUTH_DIR}/omega-reboot-recovery.conf"
  fsync_dir "$RUNTIME_AUTH_DIR"
  systemctl_bounded daemon-reload
}

record_fence_evidence() {
  local temporary
  temporary="$(mktemp "${FENCE_SENTINEL}.XXXXXX")" || return 1
  printf '%s\n' 'watchdog-fenced' > "$temporary" || return 1
  chmod 0600 "$temporary" || return 1
  /usr/bin/python3 -I - "$temporary" <<'PY' || return 1
import os
import sys

descriptor = os.open(sys.argv[1], os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
  mv -Tf "$temporary" "$FENCE_SENTINEL" || return 1
  fsync_dir "$(dirname "$FENCE_SENTINEL")" || return 1
  /usr/bin/python3 -I - "$OPERATION_MARKER" <<'PY'
import json
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
parent = path.parent
try:
    parent_info = parent.lstat()
except OSError:
    raise SystemExit(1)
if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
    raise SystemExit(1)
try:
    info = path.lstat()
except FileNotFoundError:
    info = None
except OSError:
    raise SystemExit(1)
if info is not None:
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise SystemExit(1)
else:
    payload = json.dumps(
        {"schema_version": 1, "operation": "watchdog", "state": "fenced"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode() + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        descriptor = None
    if descriptor is not None:
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}

database_fence_best_effort() {
  local container port database
  for spec in "mode_postgres:5432:modecissions" "mode_postgres_gold:5433:modecissions_gold"; do
    IFS=: read -r container port database <<<"$spec"
    docker_bounded exec "$container" psql -v ON_ERROR_STOP=1 -At -U postgres \
      -d postgres -p "$port" \
      -c "ALTER DATABASE ${database} CONNECTION LIMIT 0; ALTER DATABASE ${database} SET default_transaction_read_only = on;" \
      >/dev/null 2>&1 || :
    docker_bounded exec "$container" psql -v ON_ERROR_STOP=1 -At -U postgres \
      -d postgres -p "$port" \
      -c "SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a JOIN pg_roles r ON r.rolname = a.usename WHERE a.datname = '${database}' AND a.pid <> pg_backend_pid() AND NOT r.rolsuper;" \
      >/dev/null 2>&1 || :
  done
}

emergency_fence() (
  # Keep errexit changes inside this subshell: callers must observe a nonzero
  # fence and retry in-process instead of accidentally exiting SUCCESS.
  local record_failure="${1:-1}" safe=1 docker_output="" service id
  local -a container_ids=() fence_args=(moby-fence)
  declare -A seen_ids=()
  set +e
  open_watchdog_lock || exit 1

  if [[ "$record_failure" == "1" ]]; then
    record_fence_evidence || safe=0
  fi
  revoke_runtime_authorization || safe=0
  # The startup pre-fence is a runtime-only safety action. Persistent database
  # read-only/connection settings are reserved for an actual failed operation;
  # otherwise a normal reboot would silently leave both databases fenced.
  if [[ "$record_failure" == "1" ]]; then
    database_fence_best_effort
  fi

  if docker_output="$(docker_bounded ps --no-trunc -q 2>/dev/null)"; then
    while IFS= read -r id; do
      [[ -z "$id" ]] && continue
      if [[ ! "$id" =~ ^[0-9a-f]{64}$ ]]; then
        safe=0
        continue
      fi
      if [[ -z "${seen_ids[$id]:-}" ]]; then
        seen_ids[$id]=1
        container_ids+=("$id")
        fence_args+=(--container-id "$id")
      fi
    done <<<"$docker_output"
  fi
  docker_bounded info >/dev/null 2>&1 || :
  if [[ "${#container_ids[@]}" -gt 0 ]]; then
    docker_bounded stop --time 5 "${container_ids[@]}" >/dev/null 2>&1 || :
  fi
  docker_bounded ps --no-trunc -q >/dev/null 2>&1 || :

  systemctl_bounded stop docker.socket docker.service containerd.service \
    >/dev/null 2>&1 || :
  systemctl_bounded mask --runtime docker.socket docker.service containerd.service \
    >/dev/null 2>&1 || :

  if [[ ! -x "$SAFE_IO" ]] || \
      ! safe_io_bounded "${fence_args[@]}" >/dev/null 2>&1; then
    safe=0
  fi

  # Reassert and verify the unit fence after cgroup.kill removes live-restore
  # tasks. A timed-out systemctl query is never mistaken for an inactive unit.
  systemctl_bounded stop docker.socket docker.service containerd.service \
    >/dev/null 2>&1 || :
  systemctl_bounded mask --runtime docker.socket docker.service containerd.service \
    >/dev/null 2>&1 || :
  for service in docker.socket docker.service containerd.service; do
    service_hard_fenced_or_absent "$service" || safe=0
  done
  if [[ "$record_failure" == "1" ]]; then
    record_fence_evidence || safe=0
  fi
  if [[ "$safe" == "1" ]]; then
    logger -t omega-operation-watchdog \
      "Docker runtime hard-fenced; cgroup-v2 reports two stable empty sweeps"
    exit 0
  fi
  logger -t omega-operation-watchdog \
    "emergency fence incomplete; watchdog will retry"
  exit 1
)

fence_until_safe() {
  local record_failure="${1:-1}"
  until emergency_fence "$record_failure"; do
    sleep 1
  done
}

watchdog_state() {
  local operation="$1"
  shift
  /usr/bin/python3 -I - "$STATE_FILE" "${EUID:-$(id -u)}" "$operation" "$@" <<'PY'
import json
import os
import pathlib
import re
import stat
import sys
import tempfile

path = pathlib.Path(sys.argv[1])
expected_uid = int(sys.argv[2])
operation = sys.argv[3]
expected_keys = {
    "schema_version", "mode", "pid", "starttime", "deadline", "marker",
    "marker_identity",
}


def validate_parent() -> None:
    info = path.parent.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != expected_uid
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise SystemExit(1)


def validate(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise SystemExit(1)
    mode = payload.get("mode")
    pid = payload.get("pid")
    starttime = payload.get("starttime")
    deadline = payload.get("deadline")
    marker = payload.get("marker")
    marker_identity = payload.get("marker_identity")
    if (
        payload.get("schema_version") != 1
        or mode not in {"owner", "hold", "foundation", "fencing", "complete"}
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or not isinstance(starttime, int)
        or isinstance(starttime, bool)
        or not isinstance(deadline, int)
        or isinstance(deadline, bool)
        or deadline < 1
        or not isinstance(marker, str)
        or re.fullmatch(r"/[A-Za-z0-9._/-]+/shared/operation-state[.]json", marker)
        is None
        or "/../" in marker
    ):
        raise SystemExit(1)
    identity_pattern = (
        r"[0-9a-f]{64}:[0-9]+:[0-9]+:[0-9]+:"
        r"(?:bootstrap|reboot-recovery|startup-adoption|foundation-ready):[0-9a-f]{40}"
    )
    if mode == "fencing":
        if marker_identity != "fenced" and (
            not isinstance(marker_identity, str)
            or re.fullmatch(identity_pattern, marker_identity) is None
        ):
            raise SystemExit(1)
    elif (
        not isinstance(marker_identity, str)
        or re.fullmatch(identity_pattern, marker_identity) is None
    ):
        raise SystemExit(1)
    if mode in {"owner", "complete"} and (pid <= 1 or starttime < 1):
        raise SystemExit(1)
    if mode in {"hold", "foundation", "fencing"} and (pid != 0 or starttime != 0):
        raise SystemExit(1)
    return payload


def read_existing() -> dict[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != expected_uid
            or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 4096
        ):
            raise SystemExit(1)
        if info.st_nlink != 1:
            raise SystemExit(1)
        payload = os.read(descriptor, 4097)
        after = os.fstat(descriptor)
        if (
            info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns,
        ) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise SystemExit(1)
    finally:
        os.close(descriptor)
    if len(payload) > 4096:
        raise SystemExit(1)
    try:
        return validate(json.loads(payload.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SystemExit(1) from None


validate_parent()
if operation == "read":
    value = read_existing()
    print(
        value["mode"], value["pid"], value["starttime"],
        value["deadline"], value["marker"], value["marker_identity"], sep="\t"
    )
elif operation == "write":
    if len(sys.argv) != 10:
        raise SystemExit(1)
    try:
        value = validate(
            {
                "schema_version": 1,
                "mode": sys.argv[4],
                "pid": int(sys.argv[5]),
                "starttime": int(sys.argv[6]),
                "deadline": int(sys.argv[7]),
                "marker": sys.argv[8],
                "marker_identity": sys.argv[9],
            }
        )
    except ValueError:
        raise SystemExit(1) from None
    try:
        read_existing()
    except FileNotFoundError:
        pass
    descriptor, temporary = tempfile.mkstemp(prefix=".watchdog-state.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
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
elif operation == "delete":
    read_existing()
    path.unlink()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
else:
    raise SystemExit(1)
PY
}

STATE_MODE=""
STATE_PID=""
STATE_STARTTIME=""
STATE_DEADLINE=""
STATE_MARKER=""
STATE_MARKER_IDENTITY=""

load_state_locked() {
  local row
  row="$(watchdog_state read)" || return 1
  IFS=$'\t' read -r STATE_MODE STATE_PID STATE_STARTTIME STATE_DEADLINE STATE_MARKER \
    STATE_MARKER_IDENTITY \
    <<<"$row"
  [[ "$STATE_MARKER" == "$OPERATION_MARKER" ]]
}

write_state_locked() {
  local marker_contract="${STATE_MARKER_IDENTITY:-fenced}"
  watchdog_state write "$1" "$2" "$3" "$4" "$OPERATION_MARKER" \
    "$marker_contract"
}

marker_identity() {
  /usr/bin/python3 -I - "$OPERATION_MARKER" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
import os
import pathlib
import re
import stat
import sys

path = pathlib.Path(sys.argv[1])
parent = path.parent.lstat()
if (
    stat.S_ISLNK(parent.st_mode)
    or not stat.S_ISDIR(parent.st_mode)
    or parent.st_uid != 0
    or parent.st_gid != 0
    or stat.S_IMODE(parent.st_mode) & 0o022
):
    raise SystemExit(1)
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
    identity_before = (
        before.st_dev, before.st_ino, before.st_size,
        before.st_mtime_ns, before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev, after.st_ino, after.st_size,
        after.st_mtime_ns, after.st_ctime_ns,
    )
    if len(raw) != before.st_size or identity_before != identity_after:
        raise SystemExit(1)
finally:
    os.close(descriptor)
try:
    payload = json.loads(raw.decode("utf-8", errors="strict"))
except (UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(1) from None
operation = payload.get("operation") if isinstance(payload, dict) else None
common = {"schema_version", "operation", "state", "deploy_ref", "updated_at"}
expected_schema = 1
if operation == "bootstrap":
    expected_keys = common
    expected_state = "initializing"
elif operation == "reboot-recovery":
    expected_keys = common
    expected_state = "fencing"
elif operation == "startup-adoption":
    expected_keys = common | {"adoption_id", "helper_ref"}
    expected_state = "metadata-cas-pending"
elif operation == "foundation-ready":
    expected_schema = 2
    helper_names = {
        "bootstrap_runtime", "safe_io", "metadata_firewall", "operation_gate",
        "operation_watchdog", "reboot_runtime", "runtime_contract",
    }
    expected_keys = common | {
        "helper_ref", "helper_sha256", "startup_contract_sha256",
    }
    expected_state = "awaiting-runtime-authority"
else:
    raise SystemExit(1)
deploy_ref = payload.get("deploy_ref")
if (
    set(payload) != expected_keys
    or payload.get("schema_version") != expected_schema
    or payload.get("state") != expected_state
    or not isinstance(deploy_ref, str)
    or re.fullmatch(r"[0-9a-f]{40}", deploy_ref) is None
):
    raise SystemExit(1)
if operation == "startup-adoption" and (
    re.fullmatch(
        r"[0-9]{8}T[0-9]{6}[.][0-9]{9}Z-startup-adoption-[0-9a-f]{16}",
        str(payload.get("adoption_id", "")),
    ) is None
    or re.fullmatch(r"[0-9a-f]{40}", str(payload.get("helper_ref", ""))) is None
):
    raise SystemExit(1)
if operation == "foundation-ready":
    helper_hashes = payload.get("helper_sha256")
    if (
        payload.get("schema_version") != 2
        or re.fullmatch(r"[0-9a-f]{40}", str(payload.get("helper_ref", ""))) is None
        or re.fullmatch(
            r"[0-9a-f]{64}", str(payload.get("startup_contract_sha256", ""))
        ) is None
        or not isinstance(helper_hashes, dict)
        or set(helper_hashes) != helper_names
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value)) is None
            for value in helper_hashes.values()
        )
    ):
        raise SystemExit(1)
try:
    updated = datetime.fromisoformat(str(payload["updated_at"]))
except (TypeError, ValueError):
    raise SystemExit(1) from None
if updated.tzinfo is None or updated.utcoffset() != timezone.utc.utcoffset(updated):
    raise SystemExit(1)
print(
    f"{hashlib.sha256(raw).hexdigest()}:{before.st_dev}:{before.st_ino}:"
    f"{before.st_ctime_ns}:{operation}:{deploy_ref}"
)
PY
}

marker_is_valid() {
  marker_identity >/dev/null 2>&1
}

marker_matches_state() {
  local current_identity
  current_identity="$(marker_identity 2>/dev/null)" || return 1
  [[ "$current_identity" == "$STATE_MARKER_IDENTITY" ]]
}

foundation_evidence_locked() {
  local current_identity marker_sha state_sha
  current_identity="$(marker_identity 2>/dev/null)" || return 1
  [[ "$STATE_MODE" == "foundation" && \
     "$STATE_MARKER_IDENTITY" == "$current_identity" ]] || return 1
  marker_sha="${current_identity%%:*}"
  state_sha="$(/usr/bin/python3 -I - "$STATE_FILE" "${EUID:-$(id -u)}" <<'PY'
import hashlib
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
expected_uid = int(sys.argv[2])
descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
try:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != expected_uid
        or before.st_gid != 0
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
        or not 1 <= before.st_size <= 4096
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
finally:
    os.close(descriptor)
print(hashlib.sha256(raw).hexdigest())
PY
)" || return 1
  [[ "$marker_sha" =~ ^[0-9a-f]{64}$ && "$state_sha" =~ ^[0-9a-f]{64}$ ]] || \
    return 1
  printf '%s\t%s\t%s\n' "$marker_sha" "$state_sha" "$current_identity"
}

publish_adoption_marker_locked() {
  /usr/bin/python3 -I - "$OPERATION_MARKER" "$STATE_MARKER_IDENTITY" \
    "$WATCHED_DEADLINE" "$ADOPTION_DEPLOY_REF" "$ADOPTION_HELPER_REF" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
import tempfile

path = pathlib.Path(sys.argv[1])
expected_old_identity = sys.argv[2]
adoption_id = sys.argv[3]
deploy_ref = sys.argv[4]
helper_ref = sys.argv[5]
if (
    re.fullmatch(
        r"[0-9]{8}T[0-9]{6}[.][0-9]{9}Z-startup-adoption-[0-9a-f]{16}",
        adoption_id,
    ) is None
    or re.fullmatch(r"[0-9a-f]{40}", deploy_ref) is None
    or re.fullmatch(r"[0-9a-f]{40}", helper_ref) is None
):
    raise SystemExit(1)
parent = path.parent.lstat()
if (
    stat.S_ISLNK(parent.st_mode)
    or not stat.S_ISDIR(parent.st_mode)
    or parent.st_uid != 0
    or parent.st_gid != 0
    or stat.S_IMODE(parent.st_mode) & 0o022
):
    raise SystemExit(1)


def read_identity() -> str:
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
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SystemExit(1) from None
    if not isinstance(value, dict):
        raise SystemExit(1)
    return (
        f"{hashlib.sha256(raw).hexdigest()}:{before.st_dev}:{before.st_ino}:"
        f"{before.st_ctime_ns}:{value.get('operation')}:{value.get('deploy_ref')}"
    )


if read_identity() != expected_old_identity:
    raise SystemExit(1)
payload = {
    "schema_version": 1,
    "operation": "startup-adoption",
    "state": "metadata-cas-pending",
    "adoption_id": adoption_id,
    "deploy_ref": deploy_ref,
    "helper_ref": helper_ref,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    temporary = ""
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if temporary and os.path.exists(temporary):
        os.unlink(temporary)
print(read_identity())
PY
}

remove_marker_locked() {
  /usr/bin/python3 -I - "$OPERATION_MARKER" "$STATE_MARKER_IDENTITY" <<'PY'
import hashlib
import json
import os
import pathlib
import re
import stat
import sys

path = pathlib.Path(sys.argv[1])
info = path.lstat()
if (
    stat.S_ISLNK(info.st_mode)
    or not stat.S_ISREG(info.st_mode)
    or info.st_uid != 0
    or info.st_gid != 0
    or stat.S_IMODE(info.st_mode) != 0o600
):
    raise SystemExit(1)
descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
try:
    before = os.fstat(descriptor)
    raw = os.read(descriptor, before.st_size + 1)
    after = os.fstat(descriptor)
finally:
    os.close(descriptor)
try:
    payload = json.loads(raw.decode("utf-8", errors="strict"))
except (UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(1) from None
observed = (
    f"{hashlib.sha256(raw).hexdigest()}:{before.st_dev}:{before.st_ino}:"
    f"{before.st_ctime_ns}:{payload.get('operation')}:{payload.get('deploy_ref')}"
)
if (
    len(raw) != before.st_size
    or before.st_nlink != 1
    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    or observed != sys.argv[2]
):
    raise SystemExit(1)
path.unlink()
directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}

process_identity() {
  /usr/bin/python3 -I - "$1" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path("/proc") / sys.argv[1] / "stat"
try:
    value = path.read_text(encoding="utf-8")
except (FileNotFoundError, PermissionError, OSError):
    raise SystemExit(1)
match = re.fullmatch(r"[0-9]+ \(.*\) (.*)\n?", value)
if match is None:
    raise SystemExit(1)
fields = match.group(1).split()
if len(fields) < 20 or not fields[19].isdigit():
    raise SystemExit(1)
print(f"{fields[0]}\t{fields[19]}")
PY
}

owner_unit_matches() {
  local pid="$1" main_pid active proc_cgroup
  main_pid="$(systemctl_bounded show "$OWNER_UNIT" --property=MainPID --value 2>/dev/null)" || return 1
  active="$(systemctl_bounded show "$OWNER_UNIT" --property=ActiveState --value 2>/dev/null)" || return 1
  proc_cgroup="$(awk -F: '$1 == "0" && $2 == "" {print $3}' "/proc/${pid}/cgroup" 2>/dev/null)" || return 1
  [[ "$main_pid" == "$pid" && \
     "$proc_cgroup" == "/system.slice/${OWNER_UNIT}" && \
     "$active" == "active" ]]
}

owner_unit_contract() {
  local control_group kill_mode transient
  control_group="$(systemctl_bounded show "$OWNER_UNIT" --property=ControlGroup --value 2>/dev/null)" || return 1
  kill_mode="$(systemctl_bounded show "$OWNER_UNIT" --property=KillMode --value 2>/dev/null)" || return 1
  transient="$(systemctl_bounded show "$OWNER_UNIT" --property=Transient --value 2>/dev/null)" || return 1
  [[ "$control_group" == "/system.slice/${OWNER_UNIT}" && \
     "$kill_mode" == "control-group" && "$transient" == "yes" ]]
}

owner_unit_cgroup_empty() {
  /usr/bin/python3 -I - "$OWNER_UNIT" <<'PY'
import os
import pathlib
import stat
import sys

root = pathlib.Path("/sys/fs/cgroup")
relative = pathlib.PurePosixPath("/system.slice") / sys.argv[1]
path = root
for component in relative.parts[1:]:
    path = path / component
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise SystemExit(0)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SystemExit(1)
events = path / "cgroup.events"
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
try:
    descriptor = os.open(events, flags)
except OSError:
    raise SystemExit(1)
try:
    payload = os.read(descriptor, 4097)
finally:
    os.close(descriptor)
if len(payload) > 4096:
    raise SystemExit(1)
rows = dict(
    row.split(maxsplit=1)
    for row in payload.decode("utf-8").splitlines()
    if len(row.split(maxsplit=1)) == 2
)
raise SystemExit(0 if rows.get("populated") == "0" else 1)
PY
}

kill_owner_unit() (
  local record_failure="${1:-1}" safe=1 load_state
  set +e
  open_watchdog_lock || exit 1
  if [[ "$record_failure" == "1" ]]; then
    record_fence_evidence || safe=0
  fi
  revoke_runtime_authorization || safe=0
  load_state="$(systemctl_bounded show "$OWNER_UNIT" --property=LoadState --value 2>/dev/null)"
  if [[ "$load_state" != "not-found" ]]; then
    owner_unit_contract || safe=0
    systemctl_bounded kill --kill-whom=all --signal=SIGSTOP "$OWNER_UNIT" \
      >/dev/null 2>&1 || :
    systemctl_bounded kill --kill-whom=all --signal=SIGKILL "$OWNER_UNIT" \
      >/dev/null 2>&1 || :
    systemctl_bounded stop "$OWNER_UNIT" >/dev/null 2>&1 || :
  fi
  unit_fully_stopped_or_absent "$OWNER_UNIT" || safe=0
  owner_unit_cgroup_empty || safe=0
  if [[ "$record_failure" == "1" ]]; then
    record_fence_evidence || safe=0
  fi
  [[ "$safe" == "1" ]]
)

process_is_same() {
  local pid="$1" expected_starttime="$2" identity state starttime
  identity="$(process_identity "$pid" 2>/dev/null)" || return 1
  IFS=$'\t' read -r state starttime <<<"$identity"
  [[ "$state" != "Z" && "$starttime" == "$expected_starttime" ]] && \
    owner_unit_matches "$pid"
}

monitor() {
  local now failed
  while :; do
    failed=0
    if ! open_watchdog_lock; then
      # Never use a stale descriptor as authority for state/evidence writes.
      # The hard-fence path retries until it holds the canonical lock.
      fence_until_safe 1
      return 0
    fi
    if [[ "$failed" == "0" ]] && ! load_state_locked; then failed=1; fi
    if [[ "$failed" == "0" && "$STATE_MODE" == "complete" ]]; then
      if [[ -e "$OPERATION_MARKER" || -L "$OPERATION_MARKER" || \
            -e "$FENCE_SENTINEL" || -L "$FENCE_SENTINEL" ]]; then
        failed=1
      fi
      if [[ "$failed" == "0" ]]; then now="$(monotonic_seconds)" || failed=1; fi
      if [[ "$failed" == "0" ]] && (( now >= STATE_DEADLINE )); then failed=1; fi
      if [[ "$failed" == "0" ]]; then
        flock -u 9
        exec 9>&-
        return 0
      fi
    fi
    if [[ "$failed" == "0" ]] && \
        { [[ -e "$FENCE_SENTINEL" || -L "$FENCE_SENTINEL" ]] || \
          ! marker_matches_state; }; then
      failed=1
    fi
    if [[ "$failed" == "0" ]]; then now="$(monotonic_seconds)" || failed=1; fi
    if [[ "$failed" == "0" ]] && (( now >= STATE_DEADLINE )); then failed=1; fi
    if [[ "$failed" == "0" && "$STATE_MODE" == "owner" ]] && \
        ! process_is_same "$STATE_PID" "$STATE_STARTTIME"; then
      failed=1
    elif [[ "$failed" == "0" && "$STATE_MODE" != "owner" && \
            "$STATE_MODE" != "hold" ]]; then
      failed=1
    fi
    if [[ "$failed" == "0" ]]; then
      now="$(monotonic_seconds)" || failed=1
      if [[ "$failed" == "0" ]] && (( now >= STATE_DEADLINE )); then failed=1; fi
    fi
    if [[ "$failed" != "0" ]]; then
      now="$(monotonic_seconds 2>/dev/null || printf '1')"
      write_state_locked fencing 0 0 "${STATE_DEADLINE:-$now}" >/dev/null 2>&1 || :
      record_fence_evidence >/dev/null 2>&1 || :
      flock -u 9 >/dev/null 2>&1 || :
      exec 9>&-
      until kill_owner_unit; do
        emergency_fence 1 || :
        sleep 1
      done
      fence_until_safe 1
      return 0
    fi
    flock -u 9
    exec 9>&-
    sleep 0.2
  done
}

require_transient_unit_absent() {
  local _
  for _ in $(seq 1 50); do
    if [[ "$(systemctl_bounded show "$UNIT" --property=LoadState --value 2>/dev/null)" == "not-found" ]]; then
      return 0
    fi
    sleep 0.1
  done
  return 1
}

wait_for_monitor() {
  local _
  for _ in $(seq 1 50); do
    monitor_unit_exact && return 0
    sleep 0.1
  done
  echo "omega-operation-watchdog: transient monitor did not become active" >&2
  return 1
}

monitor_unit_exact() {
  local monitor_cgroup monitor_pid
  systemctl_bounded is-active --quiet "$UNIT" || return 1
  monitor_cgroup="$(systemctl_bounded show "$UNIT" --property=ControlGroup --value 2>/dev/null)" || return 1
  monitor_pid="$(systemctl_bounded show "$UNIT" --property=MainPID --value 2>/dev/null)" || return 1
  [[ "$monitor_cgroup" == "/system.slice/${UNIT}" && \
     "$monitor_cgroup" != "/system.slice/${OWNER_UNIT}" && \
     "$monitor_pid" =~ ^[1-9][0-9]*$ && "$monitor_pid" != "1" ]]
}

arm() (
  local timeout_seconds="$WATCHED_STARTTIME" identity state starttime now deadline rc=0
  if [[ ! "$WATCHED_PID" =~ ^[1-9][0-9]*$ || "$WATCHED_PID" == "1" ]] || \
      ! valid_timeout "$timeout_seconds" || ! marker_is_valid; then
    echo "omega-operation-watchdog: PID/marker/timeout precondition failed" >&2
    exit 13
  fi
  open_watchdog_lock || exit 13
  if [[ -e "$FENCE_SENTINEL" || -L "$FENCE_SENTINEL" || \
        -e "$STATE_FILE" || -L "$STATE_FILE" ]] || ! marker_is_valid; then
    rc=13
  fi
  if [[ "$rc" == "0" ]]; then
    STATE_MARKER_IDENTITY="$(marker_identity 2>/dev/null)" || rc=13
  fi
  if [[ "$rc" == "0" ]] && ! require_transient_unit_absent; then rc=14; fi
  if [[ "$rc" == "0" ]] && \
      { ! owner_unit_contract || ! owner_unit_matches "$WATCHED_PID"; }; then
    rc=13
  fi
  identity="$(process_identity "$WATCHED_PID" 2>/dev/null)" || rc=13
  IFS=$'\t' read -r state starttime <<<"$identity"
  if [[ "$state" == "Z" || ! "$starttime" =~ ^[1-9][0-9]*$ ]]; then rc=13; fi
  now="$(monotonic_seconds)" || rc=13
  deadline=$((now + timeout_seconds))
  if [[ "$rc" == "0" ]] && ! timeout --signal=TERM --kill-after=2s 15s \
      systemd-run --quiet --collect --unit="${UNIT%.service}" \
      --property=Type=exec \
      --property=Restart=on-failure \
      --property=RestartSec=2s \
      --property=StartLimitIntervalSec=0 \
      -- "$0" monitor 0 "$OPERATION_MARKER"; then
    rc=14
  fi
  if [[ "$rc" == "0" ]] && ! wait_for_monitor; then rc=14; fi
  # Publish state only after the monitor exists and is blocked on this lock.
  # Owner death at every earlier point makes the monitor observe missing state
  # and hard-fence; there is no state-without-monitor kill window.
  if [[ "$rc" == "0" ]]; then
    write_state_locked owner "$WATCHED_PID" "$starttime" "$deadline" || rc=14
  fi
  if [[ "$rc" == "0" ]] && ! monitor_unit_exact; then rc=14; fi
  if [[ "$rc" == "0" ]]; then
    now="$(monotonic_seconds)" || rc=14
    if [[ "$rc" == "0" ]] && (( now >= deadline )); then rc=14; fi
  fi
  if [[ "$rc" != "0" ]]; then
    write_state_locked fencing 0 0 "${deadline:-1}" >/dev/null 2>&1 || :
    record_fence_evidence >/dev/null 2>&1 || :
  fi
  flock -u 9 >/dev/null 2>&1 || :
  exec 9>&-
  if [[ "$rc" != "0" ]]; then fence_until_safe 1; fi
  exit "$rc"
)

transition() (
  local target_mode="$1" timeout_seconds="$WATCHED_STARTTIME"
  local identity state starttime now candidate deadline rc=0
  if [[ ! "$WATCHED_PID" =~ ^[1-9][0-9]*$ || "$WATCHED_PID" == "1" ]] || \
      ! valid_timeout "$timeout_seconds"; then
    exit 13
  fi
  open_watchdog_lock || exit 13
  if [[ -e "$FENCE_SENTINEL" || -L "$FENCE_SENTINEL" ]] || \
      ! marker_is_valid || ! load_state_locked; then
    rc=15
  fi
  if [[ "$rc" == "0" ]] && ! marker_matches_state; then rc=15; fi
  if [[ "$rc" == "0" ]] && ! monitor_unit_exact; then rc=15; fi
  if [[ "$rc" == "0" ]]; then
    now="$(monotonic_seconds)" || rc=15
    if [[ "$rc" == "0" ]] && (( now >= STATE_DEADLINE )); then rc=15; fi
  fi
  if [[ "$rc" == "0" && "$target_mode" == "hold" ]]; then
    [[ "$STATE_MODE" == "owner" && "$STATE_PID" == "$WATCHED_PID" ]] || rc=15
  elif [[ "$rc" == "0" && "$target_mode" == "owner" ]]; then
    [[ "$STATE_MODE" == "hold" ]] || rc=15
  fi
  identity="$(process_identity "$WATCHED_PID" 2>/dev/null)" || rc=15
  IFS=$'\t' read -r state starttime <<<"$identity"
  if [[ "$state" == "Z" || ! "$starttime" =~ ^[1-9][0-9]*$ ]] || \
      ! owner_unit_contract || ! owner_unit_matches "$WATCHED_PID"; then
    rc=15
  fi
  if [[ "$rc" == "0" && "$target_mode" == "hold" && \
        "$starttime" != "$STATE_STARTTIME" ]]; then
    rc=15
  fi
  now="$(monotonic_seconds)" || rc=15
  if [[ "$rc" == "0" ]] && (( now >= STATE_DEADLINE )); then rc=15; fi
  candidate=$((now + timeout_seconds))
  deadline="$STATE_DEADLINE"
  if [[ "$rc" == "0" ]] && (( candidate < deadline )); then deadline="$candidate"; fi
  now="$(monotonic_seconds)" || rc=15
  if [[ "$rc" == "0" ]] && (( now >= deadline )); then rc=15; fi
  if [[ "$rc" == "0" && "$target_mode" == "hold" ]]; then
    write_state_locked hold 0 0 "$deadline" || rc=15
  elif [[ "$rc" == "0" ]]; then
    write_state_locked owner "$WATCHED_PID" "$starttime" "$deadline" || rc=15
  fi
  if [[ "$rc" == "0" ]] && ! monitor_unit_exact; then rc=15; fi
  if [[ "$rc" == "0" ]]; then
    now="$(monotonic_seconds)" || rc=15
    if [[ "$rc" == "0" ]] && (( now >= deadline )); then rc=15; fi
  fi
  if [[ "$rc" != "0" ]]; then
    write_state_locked fencing 0 0 "${deadline:-${STATE_DEADLINE:-1}}" >/dev/null 2>&1 || :
    record_fence_evidence >/dev/null 2>&1 || :
  fi
  flock -u 9 >/dev/null 2>&1 || :
  exec 9>&-
  if [[ "$rc" != "0" ]]; then fence_until_safe 1; fi
  exit "$rc"
)

handoff() { transition hold; }
adopt() { transition owner; }

adopt_rebind() (
  local timeout_seconds="$WATCHED_STARTTIME"
  local identity state starttime now candidate deadline new_marker_identity rc=0
  if [[ ! "$WATCHED_PID" =~ ^[1-9][0-9]*$ || "$WATCHED_PID" == "1" ]] || \
      ! valid_timeout "$timeout_seconds" || \
      [[ ! "$WATCHED_DEADLINE" =~ ^[0-9]{8}T[0-9]{6}\.[0-9]{9}Z-startup-adoption-[0-9a-f]{16}$ ]] || \
      [[ ! "$ADOPTION_DEPLOY_REF" =~ ^[0-9a-f]{40}$ ]] || \
      [[ ! "$ADOPTION_HELPER_REF" =~ ^[0-9a-f]{40}$ ]]; then
    exit 13
  fi
  open_watchdog_lock || exit 13
  if [[ -e "$FENCE_SENTINEL" || -L "$FENCE_SENTINEL" ]] || \
      ! marker_is_valid || ! load_state_locked; then
    rc=15
  fi
  if [[ "$rc" == "0" ]] && ! marker_matches_state; then rc=15; fi
  if [[ "$rc" == "0" && "$STATE_MODE" != "hold" ]]; then rc=15; fi
  if [[ "$rc" == "0" ]] && ! monitor_unit_exact; then rc=15; fi
  identity="$(process_identity "$WATCHED_PID" 2>/dev/null)" || rc=15
  IFS=$'\t' read -r state starttime <<<"$identity"
  if [[ "$state" == "Z" || ! "$starttime" =~ ^[1-9][0-9]*$ ]] || \
      ! owner_unit_contract || ! owner_unit_matches "$WATCHED_PID"; then
    rc=15
  fi
  now="$(monotonic_seconds)" || rc=15
  if [[ "$rc" == "0" ]] && (( now >= STATE_DEADLINE )); then rc=15; fi
  candidate=$((now + timeout_seconds))
  deadline="$STATE_DEADLINE"
  if [[ "$rc" == "0" ]] && (( candidate < deadline )); then deadline="$candidate"; fi
  if [[ "$rc" == "0" ]]; then
    new_marker_identity="$(publish_adoption_marker_locked)" || rc=15
  fi
  if [[ "$rc" == "0" ]]; then
    # The monitor is blocked on the same lock. Bind the newly fsynced marker
    # into the durable state before releasing it; any crash between these two
    # publications yields an identity mismatch and therefore a hard fence.
    STATE_MARKER_IDENTITY="$new_marker_identity"
    write_state_locked owner "$WATCHED_PID" "$starttime" "$deadline" || rc=15
  fi
  if [[ "$rc" == "0" ]] && ! marker_matches_state; then rc=15; fi
  if [[ "$rc" == "0" ]] && ! monitor_unit_exact; then rc=15; fi
  now="$(monotonic_seconds)" || rc=15
  if [[ "$rc" == "0" ]] && (( now >= deadline )); then rc=15; fi
  if [[ "$rc" != "0" ]]; then
    write_state_locked fencing 0 0 "${deadline:-${STATE_DEADLINE:-1}}" >/dev/null 2>&1 || :
    record_fence_evidence >/dev/null 2>&1 || :
  fi
  flock -u 9 >/dev/null 2>&1 || :
  exec 9>&-
  if [[ "$rc" != "0" ]]; then fence_until_safe 1; fi
  exit "$rc"
)

foundation_handoff() (
  local identity rc=0
  fence_until_safe 0
  open_watchdog_lock || exit 16
  if [[ -e "$FENCE_SENTINEL" || -L "$FENCE_SENTINEL" || -L "$STATE_FILE" ]]; then
    rc=15
  fi
  identity="$(marker_identity 2>/dev/null)" || rc=15
  if [[ "$rc" == "0" && "$identity" != *":foundation-ready:"* ]]; then rc=15; fi
  if [[ "$rc" == "0" ]] && ! require_transient_unit_absent; then rc=15; fi
  if [[ "$rc" == "0" && -e "$STATE_FILE" ]]; then
    if ! load_state_locked || [[ "$STATE_MODE" != "foundation" ]] || \
        [[ "$STATE_MARKER_IDENTITY" != "$identity" ]]; then
      rc=15
    fi
  elif [[ "$rc" == "0" ]]; then
    STATE_MARKER_IDENTITY="$identity"
    write_state_locked foundation 0 0 9223372036854775807 || rc=15
  fi
  if [[ "$rc" == "0" ]] && ! load_state_locked; then rc=15; fi
  if [[ "$rc" == "0" && "$STATE_MODE" != "foundation" ]]; then rc=15; fi
  if [[ "$rc" == "0" ]] && ! marker_matches_state; then rc=15; fi
  if [[ "$rc" != "0" ]]; then
    write_state_locked fencing 0 0 1 >/dev/null 2>&1 || :
    record_fence_evidence >/dev/null 2>&1 || :
  fi
  flock -u 9 >/dev/null 2>&1 || :
  exec 9>&-
  if [[ "$rc" != "0" ]]; then fence_until_safe 1; fi
  exit "$rc"
)

foundation_check() (
  local rc=0
  fence_until_safe 0
  open_watchdog_lock || exit 16
  if [[ -e "$FENCE_SENTINEL" || -L "$FENCE_SENTINEL" ]] || \
      ! load_state_locked || [[ "$STATE_MODE" != "foundation" ]] || \
      ! marker_matches_state || ! require_transient_unit_absent; then
    rc=15
  fi
  if [[ "$rc" == "0" ]]; then foundation_evidence_locked || rc=15; fi
  flock -u 9 >/dev/null 2>&1 || :
  exec 9>&-
  if [[ "$rc" != "0" ]]; then fence_until_safe 1; fi
  exit "$rc"
)

foundation_evidence() (
  local rc=0
  fence_until_safe 0
  open_watchdog_lock || exit 16
  if [[ -e "$FENCE_SENTINEL" || -L "$FENCE_SENTINEL" ]] || \
      ! load_state_locked || [[ "$STATE_MODE" != "foundation" ]] || \
      ! marker_matches_state || ! require_transient_unit_absent; then
    rc=15
  fi
  if [[ "$rc" == "0" ]]; then foundation_evidence_locked || rc=15; fi
  flock -u 9 >/dev/null 2>&1 || :
  exec 9>&-
  if [[ "$rc" != "0" ]]; then fence_until_safe 1; fi
  exit "$rc"
)

foundation_consume() (
  local rc=0 current_evidence current_marker_sha current_state_sha _identity
  if [[ ! "$ADOPTION_DEPLOY_REF" =~ ^[0-9a-f]{40}$ || \
        ! "$ADOPTION_HELPER_REF" =~ ^[0-9a-f]{40}$ || \
        ! "$EXPECTED_MARKER_SHA256" =~ ^[0-9a-f]{64}$ || \
        ! "$EXPECTED_OLD_DEPLOY_REF" =~ ^[0-9a-f]{40}$ || \
        ! "$EXPECTED_OLD_HELPER_REF" =~ ^[0-9a-f]{40}$ || \
        ! "$EXPECTED_OLD_STARTUP_SHA256" =~ ^[0-9a-f]{64}$ || \
        ! "$EXPECTED_OLD_WATCHDOG_STATE_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    exit 13
  fi
  fence_until_safe 0
  open_watchdog_lock || exit 16
  if [[ -e "$FENCE_SENTINEL" || -L "$FENCE_SENTINEL" ]] || \
      ! load_state_locked || [[ "$STATE_MODE" != "foundation" ]] || \
      ! marker_matches_state || ! require_transient_unit_absent; then
    rc=15
  fi
  if [[ "$rc" == "0" ]]; then
    current_evidence="$(foundation_evidence_locked)" || rc=15
    IFS=$'\t' read -r current_marker_sha current_state_sha _identity \
      <<<"$current_evidence"
    if [[ "$current_marker_sha" != "$EXPECTED_MARKER_SHA256" || \
          "$current_state_sha" != "$EXPECTED_OLD_WATCHDOG_STATE_SHA256" ]]; then
      rc=15
    fi
  fi
  if [[ "$rc" == "0" ]]; then
    /usr/bin/python3 -I - "$OPERATION_MARKER" "$STATE_MARKER_IDENTITY" \
      "$ADOPTION_DEPLOY_REF" "$ADOPTION_HELPER_REF" \
      "$EXPECTED_MARKER_SHA256" "$EXPECTED_OLD_DEPLOY_REF" \
      "$EXPECTED_OLD_HELPER_REF" "$EXPECTED_OLD_STARTUP_SHA256" <<'PY' || rc=15
import hashlib
import json
import os
import pathlib
import re
import stat
import sys

path = pathlib.Path(sys.argv[1])
descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
try:
    before = os.fstat(descriptor)
    raw = os.read(descriptor, before.st_size + 1)
    after = os.fstat(descriptor)
finally:
    os.close(descriptor)
try:
    payload = json.loads(raw.decode("utf-8", errors="strict"))
except (UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(1) from None
identity = (
    f"{hashlib.sha256(raw).hexdigest()}:{before.st_dev}:{before.st_ino}:"
    f"{before.st_ctime_ns}:{payload.get('operation')}:{payload.get('deploy_ref')}"
)
if (
    not stat.S_ISREG(before.st_mode)
    or before.st_uid != 0
    or before.st_gid != 0
    or stat.S_IMODE(before.st_mode) != 0o600
    or before.st_nlink != 1
    or len(raw) != before.st_size
    or (
        before.st_dev, before.st_ino, before.st_size,
        before.st_mtime_ns, before.st_ctime_ns,
    ) != (
        after.st_dev, after.st_ino, after.st_size,
        after.st_mtime_ns, after.st_ctime_ns,
    )
    or identity != sys.argv[2]
    or payload.get("operation") != "foundation-ready"
    or payload.get("state") != "awaiting-runtime-authority"
    or payload.get("schema_version") != 2
    or hashlib.sha256(raw).hexdigest() != sys.argv[5]
    or payload.get("deploy_ref") != sys.argv[6]
    or payload.get("helper_ref") != sys.argv[7]
    or payload.get("startup_contract_sha256") != sys.argv[8]
    or (payload.get("deploy_ref"), payload.get("helper_ref"))
    == (sys.argv[3], sys.argv[4])
):
    raise SystemExit(1)
PY
  fi
  # Delete durable authority before the marker while holding one exclusive
  # lock. The handoff action is deliberately idempotent: after a crash at this
  # boundary it recreates only the state bound to the still-exact old marker,
  # allowing this same expected-old CAS to resume without authorizing runtime.
  if [[ "$rc" == "0" ]]; then watchdog_state delete || rc=15; fi
  if [[ "$rc" == "0" ]]; then remove_marker_locked || rc=15; fi
  if [[ "$rc" != "0" ]]; then
    record_fence_evidence >/dev/null 2>&1 || :
  fi
  flock -u 9 >/dev/null 2>&1 || :
  exec 9>&-
  if [[ "$rc" != "0" ]]; then fence_until_safe 1; fi
  exit "$rc"
)

stop_monitor_locked() {
  systemctl_bounded stop "$UNIT" >/dev/null 2>&1 || return 1
  systemctl_bounded reset-failed "$UNIT" >/dev/null 2>&1 || :
  unit_fully_stopped_or_absent "$UNIT"
}

complete() (
  local rc=0 failure_code=16 now identity state starttime
  if [[ ! "$WATCHED_PID" =~ ^[1-9][0-9]*$ || "$WATCHED_PID" == "1" ]]; then
    exit 15
  fi
  set +e
  open_watchdog_lock || exit 16
  if [[ -e "$FENCE_SENTINEL" || -L "$FENCE_SENTINEL" ]] || \
      ! marker_is_valid || ! load_state_locked || \
      [[ "$STATE_MODE" != "owner" || "$STATE_PID" != "$WATCHED_PID" ]]; then
    echo "omega-operation-watchdog: refusing completion after invalid/fenced operation" >&2
    rc=1
    failure_code=15
  fi
  if [[ "$rc" == "0" ]] && ! marker_matches_state; then
    rc=1
    failure_code=15
  fi
  if [[ "$rc" == "0" ]] && ! monitor_unit_exact; then rc=1; fi
  if [[ "$rc" == "0" ]]; then
    identity="$(process_identity "$WATCHED_PID" 2>/dev/null)" || rc=1
    IFS=$'\t' read -r state starttime <<<"$identity"
    [[ "$state" != "Z" && "$starttime" == "$STATE_STARTTIME" ]] || rc=1
    owner_unit_contract && owner_unit_matches "$WATCHED_PID" || rc=1
    now="$(monotonic_seconds)" || rc=1
    if [[ "$rc" == "0" ]] && (( now >= STATE_DEADLINE )); then rc=1; failure_code=15; fi
    if [[ "$rc" == "0" ]] && ! monitor_unit_exact; then rc=1; fi
  fi
  if [[ "$rc" == "0" ]]; then
    now="$(monotonic_seconds)" || rc=1
    if [[ "$rc" == "0" ]] && (( now >= STATE_DEADLINE )); then rc=1; failure_code=15; fi
  fi
  if [[ "$rc" == "0" ]]; then remove_marker_locked || rc=1; fi
  if [[ "$rc" == "0" ]]; then
    now="$(monotonic_seconds)" || rc=1
    if [[ "$rc" == "0" ]] && (( now >= STATE_DEADLINE )); then rc=1; failure_code=15; fi
  fi
  if [[ "$rc" == "0" ]]; then
    # This fsynced terminal state is the only successful self-disarm signal.
    # Missing/corrupt state always fences, including after monitor restart.
    write_state_locked complete "$STATE_PID" "$STATE_STARTTIME" "$STATE_DEADLINE" || rc=1
  fi
  if [[ "$rc" == "0" ]]; then
    # The atomic write itself may block long enough to cross the absolute
    # deadline. Recheck under the same lock before stopping the monitor.
    now="$(monotonic_seconds)" || rc=1
    if [[ "$rc" == "0" ]] && (( now >= STATE_DEADLINE )); then
      rc=1
      failure_code=15
    fi
  fi
  if [[ "$rc" == "0" ]] && ! stop_monitor_locked; then
    echo "omega-operation-watchdog: transient monitor remained active" >&2
    rc=1
  fi
  if [[ "$rc" == "0" ]]; then watchdog_state delete || rc=1; fi
  if [[ "$rc" != "0" ]]; then
    write_state_locked fencing 0 0 "${STATE_DEADLINE:-${now:-1}}" >/dev/null 2>&1 || :
    record_fence_evidence >/dev/null 2>&1 || :
  fi
  flock -u 9 || rc=1
  exec 9>&-
  if [[ "$rc" != "0" ]]; then
    fence_until_safe 1
    exit "$failure_code"
  fi
  exit 0
)

fence_external() (
  local now deadline=1 self_cgroup
  set +e
  self_cgroup="$(awk -F: '$1 == "0" && $2 == "" {print $3}' "/proc/$$/cgroup" 2>/dev/null)"
  if [[ "$self_cgroup" == "/system.slice/${OWNER_UNIT}" || \
        "$self_cgroup" == "/system.slice/${OWNER_UNIT}/"* ]]; then
    fence_until_safe 1
    exit 15
  fi
  open_watchdog_lock || exit 16
  now="$(monotonic_seconds 2>/dev/null || printf '1')"
  if load_state_locked; then deadline="$STATE_DEADLINE"; else deadline="$now"; fi
  write_state_locked fencing 0 0 "$deadline" >/dev/null 2>&1 || :
  record_fence_evidence >/dev/null 2>&1 || :
  flock -u 9 >/dev/null 2>&1 || :
  exec 9>&-
  # Prove the runtime stopped before killing a possibly hostile old owner. If
  # the caller is interrupted after this point, no writer can survive.
  fence_until_safe 1
  until kill_owner_unit; do
    emergency_fence 1 || :
    sleep 1
  done
  fence_until_safe 1
)

fence_prestart() (
  local self_cgroup
  set +e
  self_cgroup="$(awk -F: '$1 == "0" && $2 == "" {print $3}' "/proc/$$/cgroup" 2>/dev/null)"
  if [[ "$self_cgroup" == "/system.slice/${OWNER_UNIT}" || \
        "$self_cgroup" == "/system.slice/${OWNER_UNIT}/"* ]]; then
    fence_until_safe 1
    exit 15
  fi
  # Runtime and mutator restart policies are already fenced by the caller.
  # Stop any stale monitor before killing the complete old owner cgroup.
  fence_until_safe 0
  systemctl_bounded stop "$UNIT" >/dev/null 2>&1 || :
  systemctl_bounded reset-failed "$UNIT" >/dev/null 2>&1 || :
  if ! unit_fully_stopped_or_absent "$UNIT"; then
    fence_until_safe 1
    exit 15
  fi
  until kill_owner_unit 0; do
    emergency_fence 0 || :
    sleep 1
  done
  # A foundation handoff is a durable, marker-bound hard-fenced state with no
  # monitor. Preserve it so the next reviewed controller can consume it by CAS.
  if [[ -e "$STATE_FILE" && ! -L "$STATE_FILE" && \
        ! -e "$FENCE_SENTINEL" && ! -L "$FENCE_SENTINEL" ]]; then
    open_watchdog_lock || { fence_until_safe 1; exit 15; }
    if load_state_locked && [[ "$STATE_MODE" == "foundation" ]] && \
        marker_matches_state && require_transient_unit_absent; then
      flock -u 9
      exec 9>&-
      fence_until_safe 0
      exit 0
    fi
    flock -u 9 >/dev/null 2>&1 || :
    exec 9>&-
  fi
  # Any other durable watchdog state means this is not a clean reentry.
  if [[ -e "$STATE_FILE" || -L "$STATE_FILE" || \
        -e "$FENCE_SENTINEL" || -L "$FENCE_SENTINEL" ]]; then
    fence_until_safe 1
    exit 15
  fi
  fence_until_safe 0
)

case "$ACTION" in
  marker-check) marker_is_valid ;;
  arm) arm ;;
  handoff) handoff ;;
  adopt) adopt ;;
  adopt-rebind) adopt_rebind ;;
  foundation-handoff) foundation_handoff ;;
  foundation-check) foundation_check ;;
  foundation-evidence) foundation_evidence ;;
  foundation-consume) foundation_consume ;;
  monitor) monitor ;;
  fence) fence_until_safe 0 ;;
  fence-failure) fence_until_safe 1 ;;
  fence-external) fence_external ;;
  fence-prestart) fence_prestart ;;
  complete) complete ;;
  *)
    echo "usage: omega-operation-watchdog {marker-check|arm|handoff|adopt|adopt-rebind|foundation-handoff|foundation-check|foundation-evidence|foundation-consume|monitor|fence|fence-failure|fence-external|complete} PID MARKER [TIMEOUT] [ADOPTION_ID DEPLOY_REF HELPER_REF EXPECTED_MARKER_SHA256 EXPECTED_OLD_DEPLOY_REF EXPECTED_OLD_HELPER_REF EXPECTED_OLD_STARTUP_SHA256 EXPECTED_OLD_WATCHDOG_STATE_SHA256]" >&2
    exit 64
    ;;
esac
