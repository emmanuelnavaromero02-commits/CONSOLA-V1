#!/usr/bin/env bash
# Complete the bounded startup-metadata adoption only after exact live read-back.
set -Eeuo pipefail
set +x
umask 077

ADOPTION_ID="${1:-}"
CURRENT_REF="${2:-}"
HELPER_REF="${3:-}"
EXPECTED_STARTUP_SHA256="${4:-}"
APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
SHARED_ROOT="${APP_ROOT}/shared"
OPERATION_MARKER="${SHARED_ROOT}/operation-state.json"
COMPLETION_MARKER="${SHARED_ROOT}/startup-adoption-complete.json"
WATCHDOG="/usr/local/sbin/omega-operation-watchdog"
SAFE_IO="${OMEGA_GCP_SAFE_IO:-/usr/local/sbin/omega-safe-io}"

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
if [[ ! "$ADOPTION_ID" =~ ^[0-9]{8}T[0-9]{6}Z-startup-adoption$ || \
      ! "$CURRENT_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$HELPER_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$EXPECTED_STARTUP_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "adoption identity" "exact adoption/ref/startup identities are required" 11
fi
if [[ ! -x "$WATCHDOG" || ! -x "$SAFE_IO" || \
      ! -x /usr/local/sbin/omega-operation-gate ]]; then
  fail "installed guards" "candidate guard/watchdog/safe-I/O helper missing" 12
fi

verify_completion() {
  python3 - "$COMPLETION_MARKER" "$ADOPTION_ID" "$CURRENT_REF" "$HELPER_REF" \
    "$EXPECTED_STARTUP_SHA256" <<'PY'
import json
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
info = path.lstat()
if (
    not stat.S_ISREG(info.st_mode)
    or info.st_uid != 0
    or info.st_gid != 0
    or stat.S_IMODE(info.st_mode) != 0o600
    or path.resolve(strict=True) != path
):
    raise SystemExit(1)
payload = json.loads(path.read_text(encoding="utf-8"))
expected = {
    "schema_version": 1,
    "operation": "startup-adoption",
    "state": "complete",
    "adoption_id": sys.argv[2],
    "deploy_ref": sys.argv[3],
    "helper_ref": sys.argv[4],
    "startup_sha256": sys.argv[5],
}
if set(payload) != set(expected) | {"completed_at"}:
    raise SystemExit(1)
for key, value in expected.items():
    if payload.get(key) != value:
        raise SystemExit(1)
if not isinstance(payload.get("completed_at"), str):
    raise SystemExit(1)
PY
}

verify_live_startup() {
  local temporary actual
  temporary="$(mktemp /tmp/omega-startup-metadata.XXXXXX)"
  if ! curl --fail --silent --show-error --max-time 10 \
      -H 'Metadata-Flavor: Google' \
      'http://metadata.google.internal/computeMetadata/v1/instance/attributes/startup-script' \
      -o "$temporary"; then
    rm -f -- "$temporary"
    return 1
  fi
  actual="$(sha256sum "$temporary" | awk '{print $1}')"
  rm -f -- "$temporary"
  [[ "$actual" == "$EXPECTED_STARTUP_SHA256" ]]
}

verify_runtime_unchanged() {
  local scheduler_ids scheduler_id scheduler_project
  docker info >/dev/null 2>&1 || return 1
  scheduler_ids="$(docker ps -q --filter 'label=com.docker.compose.service=airflow-scheduler')"
  [[ "$(grep -c . <<<"$scheduler_ids" || true)" == "1" ]] || return 1
  scheduler_id="$scheduler_ids"
  scheduler_project="$(docker inspect "$scheduler_id" \
    --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || true)"
  [[ "$scheduler_project" == "infra" ]]
}

if [[ ! -e "$OPERATION_MARKER" ]]; then
  verify_completion || fail "idempotent completion" "marker absent without exact completion record" 13
  verify_live_startup || fail "startup metadata read-back" "live startup hash differs" 13
  /usr/local/sbin/omega-operation-gate >/dev/null || \
    fail "runtime operation gate" "canonical runtime state is not valid" 13
  emit "idempotent completion" "adoption=${ADOPTION_ID} startup hash exact"
  printf 'OMEGA_GCP_ADOPTION_FINALIZE_JSON={"status":"PASS","operation":"startup-adoption-finalize","idempotent":true,"secrets_included":false}\n'
  exit 0
fi

python3 - "$OPERATION_MARKER" "$ADOPTION_ID" "$CURRENT_REF" "$HELPER_REF" <<'PY' || \
  fail "durable adoption marker" "marker identity/state differs" 14
import json
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
    raise SystemExit(1)
payload = json.loads(path.read_text(encoding="utf-8"))
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
verify_runtime_unchanged || \
  fail "live runtime continuity" "Docker or the unique canonical scheduler drifted" 15

python3 - "$COMPLETION_MARKER" "$ADOPTION_ID" "$CURRENT_REF" "$HELPER_REF" \
  "$EXPECTED_STARTUP_SHA256" <<'PY'
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "operation": "startup-adoption",
    "state": "complete",
    "adoption_id": sys.argv[2],
    "deploy_ref": sys.argv[3],
    "helper_ref": sys.argv[4],
    "startup_sha256": sys.argv[5],
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
verify_completion || fail "completion record" "exact durable record read-back failed" 16
rm -f -- "$OPERATION_MARKER"
"$SAFE_IO" fsync-dir "$SHARED_ROOT"
"$WATCHDOG" disarm 1 "$OPERATION_MARKER"
/usr/local/sbin/omega-operation-gate >/dev/null || \
  fail "final operation gate" "runtime did not unfence after exact CAS read-back" 17

emit "startup metadata read-back" "sha256=${EXPECTED_STARTUP_SHA256}"
emit "bounded adoption fence" "durable marker removed only after exact CAS read-back"
printf 'OMEGA_GCP_ADOPTION_FINALIZE_JSON={"status":"PASS","operation":"startup-adoption-finalize","idempotent":false,"secrets_included":false}\n'
