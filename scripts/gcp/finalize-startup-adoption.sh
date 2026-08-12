#!/bin/bash -p
# Complete the bounded startup-metadata adoption only after exact live read-back.
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

ADOPTION_ID="${1:-}"
CURRENT_REF="${2:-}"
HELPER_REF="${3:-}"
EXPECTED_STARTUP_SHA256="${4:-}"
APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
SHARED_ROOT="${APP_ROOT}/shared"
OPERATION_MARKER="${SHARED_ROOT}/operation-state.json"
COMPLETION_MARKER="${SHARED_ROOT}/startup-adoption-complete.json"
COMPLETION_HISTORY="${SHARED_ROOT}/startup-adoption-history"
COMPLETION_RECEIPT="${COMPLETION_HISTORY}/${ADOPTION_ID}.json"
WATCHDOG="/usr/local/sbin/omega-operation-watchdog"
SAFE_IO="${OMEGA_GCP_SAFE_IO:-/usr/local/sbin/omega-safe-io}"
CANDIDATE_RUNTIME_CONTRACT="/usr/local/sbin/omega-runtime-contract"
CANDIDATE_RUNTIME_RECORD="/var/lib/omega-gcp/candidate-runtime-contract.json"
DAY2_LOCK_DIR="/run/omega-gcp"
DAY2_LOCK="${DAY2_LOCK_DIR}/day2.lock"

fail() {
  printf 'OMEGA_GCP_ADOPTION_FINALIZE_CHECK\t%s\tFAIL\t%s\n' "$1" "${2:-}" >&2
  exit "${3:-20}"
}

emit() {
  printf 'OMEGA_GCP_ADOPTION_FINALIZE_CHECK\t%s\tPASS\t%s\n' "$1" "${2:-}"
}

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  fail "operator identity" "root is required" 10
fi
if [[ ! "$ADOPTION_ID" =~ ^[0-9]{8}T[0-9]{6}\.[0-9]{9}Z-startup-adoption-[0-9a-f]{16}$ || \
      ! "$CURRENT_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$HELPER_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$EXPECTED_STARTUP_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "adoption identity" "exact adoption/ref/startup identities are required" 11
fi
if [[ ! -x "$WATCHDOG" || ! -x "$SAFE_IO" || \
      ! -x "$CANDIDATE_RUNTIME_CONTRACT" || \
      ! -x /usr/local/sbin/omega-operation-gate ]]; then
  fail "installed guards" "candidate guard/watchdog/safe-I/O helper missing" 12
fi

if ! exec 9>>"$DAY2_LOCK"; then
  fail "exclusive day-2 lock" "private lock descriptor cannot be opened" 12
fi
/usr/bin/python3 -I - "$DAY2_LOCK_DIR" "$DAY2_LOCK" <<'PY' || \
  fail "exclusive day-2 lock" "private lock descriptor is unsafe" 12
import os
import pathlib
import stat
import sys

directory = pathlib.Path(sys.argv[1])
lock = pathlib.Path(sys.argv[2])
info = directory.lstat()
if (
    not stat.S_ISDIR(info.st_mode)
    or stat.S_ISLNK(info.st_mode)
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
flock -n 9 || fail "exclusive day-2 lock" "another canonical operation is active" 12
/usr/bin/python3 -I - "$DAY2_LOCK" <<'PY' || \
  fail "exclusive day-2 lock" "lock pathname changed before critical section" 12
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

verify_candidate_runtime_contract() {
  local expected_helper_ref="${1:-$HELPER_REF}"
  local expected_deploy_ref="${2:-$CURRENT_REF}"
  local expected_startup_sha="${3:-$EXPECTED_STARTUP_SHA256}"
  /usr/bin/python3 -I - "$CANDIDATE_RUNTIME_RECORD" "$CANDIDATE_RUNTIME_CONTRACT" \
    "$expected_helper_ref" "$expected_deploy_ref" "$expected_startup_sha" <<'PY'
import hashlib
import json
import os
import pathlib
import re
import stat
import sys

record = pathlib.Path(sys.argv[1])
helper = pathlib.Path(sys.argv[2])
record_parent = record.parent.lstat()
if (
    not stat.S_ISDIR(record_parent.st_mode)
    or record_parent.st_uid != 0
    or record_parent.st_gid != 0
    or stat.S_IMODE(record_parent.st_mode) != 0o700
):
    raise SystemExit(1)


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


record_raw = read_owned(record, 0o600, 4096)
helper_raw = read_owned(helper, 0o755, 2 * 1024 * 1024)
try:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    payload = json.loads(
        record_raw.decode("utf-8", errors="strict"),
        object_pairs_hook=no_duplicates,
    )
except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
    raise SystemExit(1) from None
expected_helpers = {
    "bootstrap_runtime",
    "safe_io",
    "metadata_firewall",
    "operation_gate",
    "operation_watchdog",
    "reboot_runtime",
    "runtime_contract",
}
if not isinstance(payload, dict) or set(payload) != {
    "schema_version",
    "deploy_ref",
    "helper_ref",
    "startup_contract_sha256",
    "helper_sha256",
}:
    raise SystemExit(1)
helper_sha256 = payload.get("helper_sha256")
if (
    payload.get("schema_version") != 2
    or payload.get("deploy_ref") != sys.argv[4]
    or payload.get("helper_ref") != sys.argv[3]
    or payload.get("startup_contract_sha256") != sys.argv[5]
    or re.fullmatch(r"[0-9a-f]{40}", str(payload.get("deploy_ref", ""))) is None
    or re.fullmatch(r"[0-9a-f]{40}", str(payload.get("helper_ref", ""))) is None
    or re.fullmatch(
        r"[0-9a-f]{64}", str(payload.get("startup_contract_sha256", ""))
    ) is None
    or not isinstance(helper_sha256, dict)
    or set(helper_sha256) != expected_helpers
    or any(
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in helper_sha256.values()
    )
    or helper_sha256.get("runtime_contract")
    != hashlib.sha256(helper_raw).hexdigest()
):
    raise SystemExit(1)
PY
}

completion_context() {
  /usr/bin/python3 -I - "$COMPLETION_MARKER" "$COMPLETION_HISTORY" "$COMPLETION_RECEIPT" \
    "$ADOPTION_ID" "$CURRENT_REF" "$HELPER_REF" "$EXPECTED_STARTUP_SHA256" <<'PY'
from datetime import datetime, timezone
import json
import os
import pathlib
import re
import stat
import sys

current = pathlib.Path(sys.argv[1])
history = pathlib.Path(sys.argv[2])
receipt = pathlib.Path(sys.argv[3])
history_info = history.lstat()
current_info = current.lstat()
if (
    not stat.S_ISDIR(history_info.st_mode)
    or history.is_symlink()
    or history_info.st_uid != 0
    or history_info.st_gid != 0
    or stat.S_IMODE(history_info.st_mode) != 0o700
    or receipt.parent.resolve(strict=True) != history
    or not stat.S_ISLNK(current_info.st_mode)
    or current_info.st_uid != 0
    or current_info.st_gid != 0
):
    raise SystemExit(1)


def read_owned(path: pathlib.Path) -> bytes:
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
        return raw
    finally:
        os.close(descriptor)


def validate(payload: object, expected_id: str) -> tuple[dict, datetime]:
    keys = {
        "schema_version", "operation", "state", "adoption_id", "deploy_ref",
        "helper_ref", "startup_sha256", "completed_at",
    }
    if not isinstance(payload, dict) or set(payload) != keys:
        raise SystemExit(1)
    adoption_id = payload.get("adoption_id")
    if (
        payload.get("schema_version") != 1
        or payload.get("operation") != "startup-adoption"
        or payload.get("state") != "complete"
        or adoption_id != expected_id
        or re.fullmatch(
            r"[0-9]{8}T[0-9]{6}[.][0-9]{9}Z-startup-adoption-[0-9a-f]{16}",
            str(adoption_id),
        ) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(payload.get("deploy_ref", ""))) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(payload.get("helper_ref", ""))) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(payload.get("startup_sha256", ""))) is None
    ):
        raise SystemExit(1)
    try:
        identity = str(adoption_id)
        identity_time = datetime.strptime(
            identity[:15], "%Y%m%dT%H%M%S"
        ).replace(
            microsecond=int(identity[16:22]),
            tzinfo=timezone.utc,
        )
        completed = datetime.fromisoformat(str(payload["completed_at"]))
    except (KeyError, TypeError, ValueError):
        raise SystemExit(1)
    if (
        completed.tzinfo is None
        or completed.utcoffset() != timezone.utc.utcoffset(completed)
        or completed < identity_time
    ):
        raise SystemExit(1)
    return payload, completed


requested, requested_completed = validate(
    json.loads(read_owned(receipt).decode("utf-8", errors="strict")), sys.argv[4]
)
requested_expected = {
    "deploy_ref": sys.argv[5],
    "helper_ref": sys.argv[6],
    "startup_sha256": sys.argv[7],
}
if any(requested.get(key) != value for key, value in requested_expected.items()):
    raise SystemExit(1)

target = os.readlink(current)
match = re.fullmatch(
    r"startup-adoption-history/([0-9]{8}T[0-9]{6}[.][0-9]{9}Z-startup-adoption-[0-9a-f]{16})\.json",
    target,
)
if match is None:
    raise SystemExit(1)
current_receipt = current.parent / target
if (
    current_receipt.parent.resolve(strict=True) != history
    or current.resolve(strict=True) != current_receipt
):
    raise SystemExit(1)
active, active_completed = validate(
    json.loads(read_owned(current_receipt).decode("utf-8", errors="strict")),
    match.group(1),
)
current_after = current.lstat()
if (
    (current_info.st_dev, current_info.st_ino, current_info.st_ctime_ns)
    != (current_after.st_dev, current_after.st_ino, current_after.st_ctime_ns)
    or os.readlink(current) != target
):
    raise SystemExit(1)
if active["adoption_id"] == sys.argv[4]:
    if current_receipt != receipt or active != requested:
        raise SystemExit(1)
    status = "current"
elif active["adoption_id"] > sys.argv[4] and active_completed >= requested_completed:
    status = "superseded"
else:
    raise SystemExit(1)
print(
    "\t".join(
        (
            status,
            str(active["deploy_ref"]),
            str(active["helper_ref"]),
            str(active["startup_sha256"]),
        )
    )
)
PY
}

verify_completion() {
  local context status deploy_ref helper_ref startup_sha
  context="$(completion_context)" || return 1
  IFS=$'\t' read -r status deploy_ref helper_ref startup_sha <<<"$context"
  [[ "$status" == "current" && "$deploy_ref" == "$CURRENT_REF" && \
     "$helper_ref" == "$HELPER_REF" && "$startup_sha" == "$EXPECTED_STARTUP_SHA256" ]]
}

verify_live_startup() {
  local expected_sha="${1:-$EXPECTED_STARTUP_SHA256}" temporary actual
  temporary="$(mktemp /tmp/omega-startup-metadata.XXXXXX)"
  if ! curl -q --fail --silent --show-error --noproxy '*' --max-time 10 \
      -H 'Metadata-Flavor: Google' \
      'http://metadata.google.internal/computeMetadata/v1/instance/attributes/startup-script' \
      -o "$temporary"; then
    rm -f -- "$temporary"
    return 1
  fi
  actual="$(sha256sum "$temporary" | awk '{print $1}')"
  rm -f -- "$temporary"
  [[ "$actual" == "$expected_sha" ]]
}

verify_exact_runtime() {
  local allow_marker="$1" expected_deploy_ref="${2:-$CURRENT_REF}"
  local expected_helper_ref="${3:-$HELPER_REF}"
  local expected_startup_sha="${4:-$EXPECTED_STARTUP_SHA256}"
  local release_identity deploy_ref version current_release
  if [[ "$allow_marker" == "1" ]]; then
    OMEGA_GCP_ALLOW_OPERATION_MARKER=1 \
      /usr/local/sbin/omega-operation-gate >/dev/null || return 1
  else
    /usr/local/sbin/omega-operation-gate >/dev/null || return 1
  fi
  verify_candidate_runtime_contract "$expected_helper_ref" "$expected_deploy_ref" \
    "$expected_startup_sha" || return 1
  release_identity="$("$CANDIDATE_RUNTIME_CONTRACT" release-identity \
    --app-root "$APP_ROOT")" || return 1
  IFS=$'\t' read -r deploy_ref version current_release <<<"$release_identity"
  [[ "$deploy_ref" == "$expected_deploy_ref" && \
     "$current_release" == "$APP_ROOT/releases/$expected_deploy_ref" && \
     "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || return 1
  "$CANDIDATE_RUNTIME_CONTRACT" live-state --app-root "$APP_ROOT" \
    --compose-project infra >/dev/null
}

idempotent_fail_closed() {
  local check="$1" detail="$2" code="$3"
  local rc="$code"
  set +e
  OMEGA_GCP_SAFE_IO="$SAFE_IO" \
    "$WATCHDOG" fence-failure 1 "$OPERATION_MARKER" || rc=90
  fail "$check" "$detail; runtime hard-fenced" "$rc"
}

if [[ ! -e "$OPERATION_MARKER" && ! -L "$OPERATION_MARKER" ]]; then
  COMPLETION_CONTEXT="$(completion_context)" || \
    idempotent_fail_closed "idempotent completion" "marker absent without exact completion history/current" 13
  IFS=$'\t' read -r COMPLETION_STATUS COMPLETION_DEPLOY_REF \
    COMPLETION_HELPER_REF COMPLETION_STARTUP_SHA <<<"$COMPLETION_CONTEXT"
  verify_live_startup "$COMPLETION_STARTUP_SHA" || \
    idempotent_fail_closed "startup metadata read-back" "live startup hash differs" 13
  /usr/local/sbin/omega-operation-gate >/dev/null || \
    idempotent_fail_closed "runtime operation gate" "canonical runtime state is not valid" 13
  verify_exact_runtime 0 "$COMPLETION_DEPLOY_REF" "$COMPLETION_HELPER_REF" \
    "$COMPLETION_STARTUP_SHA" || \
    idempotent_fail_closed "exact live runtime" "persisted provenance or global inventory drifted" 13
  if [[ "$COMPLETION_STATUS" == "superseded" ]]; then
    emit "superseded completion" "adoption=${ADOPTION_ID} current=${COMPLETION_DEPLOY_REF}"
    printf 'OMEGA_GCP_ADOPTION_FINALIZE_JSON={"status":"PASS","operation":"startup-adoption-finalize","idempotent":true,"superseded":true,"secrets_included":false}\n'
  elif [[ "$COMPLETION_STATUS" == "current" ]]; then
    emit "idempotent completion" "adoption=${ADOPTION_ID} startup hash exact"
    printf 'OMEGA_GCP_ADOPTION_FINALIZE_JSON={"status":"PASS","operation":"startup-adoption-finalize","idempotent":true,"superseded":false,"secrets_included":false}\n'
  else
    idempotent_fail_closed "completion status" "completion classification is invalid" 13
  fi
  exit 0
fi

ADOPTED=0
adoption_fail_closed() {
  local rc=$?
  trap - EXIT
  set +e
  if [[ "$ADOPTED" == "1" ]]; then
    OMEGA_GCP_SAFE_IO="$SAFE_IO" \
      "$WATCHDOG" fence-failure "$$" "$OPERATION_MARKER" || rc=90
  fi
  exit "$rc"
}

# The controller leaves the watchdog-bound bootstrap/recovery marker in place.
# Only the watchdog may replace it: marker bytes and durable state identity are
# rebound under the monitor lock, so path-only replacement can never be
# interpreted as a valid ownership transfer.
"$WATCHDOG" adopt-rebind "$$" "$OPERATION_MARKER" 900 \
  "$ADOPTION_ID" "$CURRENT_REF" "$HELPER_REF" || \
  fail "watchdog marker rebind" "atomic adoption handoff failed closed" 14
ADOPTED=1
trap adoption_fail_closed EXIT

/usr/bin/python3 -I - "$OPERATION_MARKER" "$ADOPTION_ID" "$CURRENT_REF" "$HELPER_REF" <<'PY' || \
  fail "durable adoption marker" "marker identity/state differs" 14
import json
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
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
payload = json.loads(raw.decode("utf-8", errors="strict"))
expected = {
    "schema_version": 1,
    "operation": "startup-adoption",
    "state": "metadata-cas-pending",
    "adoption_id": sys.argv[2],
    "deploy_ref": sys.argv[3],
    "helper_ref": sys.argv[4],
}
if set(payload) != set(expected) | {"updated_at"}:
    raise SystemExit(1)
for key, value in expected.items():
    if payload.get(key) != value:
        raise SystemExit(1)
PY

OMEGA_GCP_ALLOW_OPERATION_MARKER=1 /usr/local/sbin/omega-operation-gate >/dev/null || \
  fail "runtime operation gate" "adopted state/helper pair is not exact" 15
verify_live_startup || \
  fail "startup metadata read-back" "metadata-server startup hash differs from controller CAS" 15
verify_exact_runtime 1 || \
  fail "exact live runtime" "persisted provenance or global inventory drifted" 15

/usr/bin/python3 -I - "$COMPLETION_MARKER" "$COMPLETION_HISTORY" "$COMPLETION_RECEIPT" \
  "$ADOPTION_ID" "$CURRENT_REF" "$HELPER_REF" "$EXPECTED_STARTUP_SHA256" <<'PY' || \
  fail "completion record" "append-only receipt/current publication failed" 16
import json
import os
import pathlib
import re
import secrets
import stat
import sys
from datetime import datetime, timezone

current = pathlib.Path(sys.argv[1])
history = pathlib.Path(sys.argv[2])
receipt = pathlib.Path(sys.argv[3])
parent_info = current.parent.lstat()
if (
    not stat.S_ISDIR(parent_info.st_mode)
    or parent_info.st_uid != 0
    or parent_info.st_gid != 0
    or stat.S_IMODE(parent_info.st_mode) & 0o022
):
    raise SystemExit(1)
try:
    history.mkdir(mode=0o700)
except FileNotFoundError:
    raise SystemExit(1)
except FileExistsError:
    pass
history_info = history.lstat()
if (
    not stat.S_ISDIR(history_info.st_mode)
    or history.is_symlink()
    or history_info.st_uid != 0
    or history_info.st_gid != 0
    or stat.S_IMODE(history_info.st_mode) != 0o700
    or history.parent.resolve(strict=True) != current.parent.resolve(strict=True)
):
    raise SystemExit(1)


def read_owned(path: pathlib.Path) -> bytes:
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
        return raw
    finally:
        os.close(descriptor)


def validate_receipt(value: object, expected_id: str) -> tuple[dict, datetime]:
    keys = {
        "schema_version", "operation", "state", "adoption_id", "deploy_ref",
        "helper_ref", "startup_sha256", "completed_at",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise SystemExit(1)
    if (
        value.get("schema_version") != 1
        or value.get("operation") != "startup-adoption"
        or value.get("state") != "complete"
        or value.get("adoption_id") != expected_id
        or re.fullmatch(
            r"[0-9]{8}T[0-9]{6}[.][0-9]{9}Z-startup-adoption-[0-9a-f]{16}",
            expected_id,
        ) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(value.get("deploy_ref", ""))) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(value.get("helper_ref", ""))) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("startup_sha256", ""))) is None
    ):
        raise SystemExit(1)
    try:
        identity_time = datetime.strptime(
            expected_id[:15], "%Y%m%dT%H%M%S"
        ).replace(
            microsecond=int(expected_id[16:22]),
            tzinfo=timezone.utc,
        )
        completed = datetime.fromisoformat(str(value["completed_at"]))
    except (KeyError, TypeError, ValueError):
        raise SystemExit(1)
    if (
        completed.tzinfo is None
        or completed.utcoffset() != timezone.utc.utcoffset(completed)
        or completed < identity_time
    ):
        raise SystemExit(1)
    return value, completed


payload = {
    "schema_version": 1,
    "operation": "startup-adoption",
    "state": "complete",
    "adoption_id": sys.argv[4],
    "deploy_ref": sys.argv[5],
    "helper_ref": sys.argv[6],
    "startup_sha256": sys.argv[7],
    "completed_at": datetime.now(timezone.utc).isoformat(),
}
payload, payload_completed = validate_receipt(payload, sys.argv[4])
expected_without_time = {
    key: value for key, value in payload.items() if key != "completed_at"
}
if receipt.name != f"{sys.argv[4]}.json":
    raise SystemExit(1)
try:
    descriptor = os.open(
        receipt,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
except FileExistsError:
    existing, existing_completed = validate_receipt(
        json.loads(read_owned(receipt).decode("utf-8", errors="strict")),
        sys.argv[4],
    )
    if (
        {key: existing.get(key) for key in expected_without_time}
        != expected_without_time
    ):
        raise SystemExit(1)
    payload = existing
    payload_completed = existing_completed
else:
    try:
        os.fchmod(descriptor, 0o600)
        encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise SystemExit(1)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(history, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

# The fixed path is only an atomic current pointer. Every old completion remains
# immutable in the private history directory, so a later adoption never erases
# evidence from an earlier one.
pointer_already_current = False
try:
    current_info = current.lstat()
except FileNotFoundError:
    current_info = None
if current_info is not None:
    if (
        not stat.S_ISLNK(current_info.st_mode)
        or current_info.st_uid != 0
        or current_info.st_gid != 0
    ):
        raise SystemExit(1)
    old_target = os.readlink(current)
    match = re.fullmatch(
        r"startup-adoption-history/([0-9]{8}T[0-9]{6}[.][0-9]{9}Z-startup-adoption-[0-9a-f]{16})\.json",
        old_target,
    )
    if match is None:
        raise SystemExit(1)
    old_receipt = current.parent / old_target
    if (
        old_receipt.parent.resolve(strict=True) != history
        or current.resolve(strict=True) != old_receipt
    ):
        raise SystemExit(1)
    old_payload, old_completed = validate_receipt(
        json.loads(read_owned(old_receipt).decode("utf-8", errors="strict")),
        match.group(1),
    )
    current_after = current.lstat()
    if (
        (current_info.st_dev, current_info.st_ino, current_info.st_ctime_ns)
        != (current_after.st_dev, current_after.st_ino, current_after.st_ctime_ns)
        or os.readlink(current) != old_target
    ):
        raise SystemExit(1)
    if old_payload["adoption_id"] == payload["adoption_id"]:
        if old_receipt != receipt or old_payload != payload:
            raise SystemExit(1)
        pointer_already_current = True
    elif not (
        old_payload["adoption_id"] < payload["adoption_id"]
        and old_completed <= payload_completed
    ):
        raise SystemExit(1)

if not pointer_already_current:
    relative_target = f"startup-adoption-history/{sys.argv[4]}.json"
    temporary_link = current.parent / (
        f".{current.name}.{os.getpid()}.{secrets.token_hex(8)}"
    )
    try:
        os.symlink(relative_target, temporary_link)
        os.replace(temporary_link, current)
        directory = os.open(current.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.lexists(temporary_link):
            os.unlink(temporary_link)
PY
verify_completion || fail "completion record" "exact durable record read-back failed" 16
OMEGA_GCP_ALLOW_OPERATION_MARKER=1 /usr/local/sbin/omega-operation-gate >/dev/null || \
  fail "final pre-complete operation gate" "runtime state/helper pair drifted" 17
verify_live_startup || \
  fail "final startup metadata read-back" "live startup hash changed" 17
verify_exact_runtime 1 || \
  fail "final exact live runtime" "runtime drifted before completion" 17
"$WATCHDOG" complete "$$" "$OPERATION_MARKER"
/usr/local/sbin/omega-operation-gate >/dev/null || \
  fail "post-complete operation gate" "runtime did not unfence exactly" 17
verify_exact_runtime 0 || \
  fail "post-complete exact live runtime" "runtime drifted after completion" 17
ADOPTED=0
trap - EXIT

emit "startup metadata read-back" "sha256=${EXPECTED_STARTUP_SHA256}"
emit "bounded adoption fence" "durable marker removed only after exact CAS read-back"
printf 'OMEGA_GCP_ADOPTION_FINALIZE_JSON={"status":"PASS","operation":"startup-adoption-finalize","idempotent":false,"secrets_included":false}\n'
