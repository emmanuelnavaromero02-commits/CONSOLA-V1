#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

die() { printf '[gcp-secret-hydration] ERROR: %s\n' "$*" >&2; exit 1; }

: "${OMEGA_GCP_PROJECT_ID:?OMEGA_GCP_PROJECT_ID is required}"
: "${OMEGA_GCP_SECRET_PREFIX:?OMEGA_GCP_SECRET_PREFIX is required}"
hydration_mode="${OMEGA_SECRET_HYDRATION_MODE:-apply}"
[[ "${hydration_mode}" == "apply" || "${hydration_mode}" == "check" ]] \
  || die "OMEGA_SECRET_HYDRATION_MODE must be apply or check"

updates_file=""
if [[ "${hydration_mode}" == "apply" ]]; then
  : "${OMEGA_ENV_FILE:?OMEGA_ENV_FILE is required}"
  env_dir="$(dirname -- "${OMEGA_ENV_FILE}")"
  mkdir -p -- "${env_dir}"
  updates_file="$(mktemp "${env_dir}/.omega-secret-updates.XXXXXX")"
fi
metadata_access_token=""
secret_response=""
control_room_key_id=""
control_room_key=""
control_room_previous_keys=""
gcs_access_key_id=""
gcs_secret_access_key=""

cleanup() {
  unset metadata_access_token secret_response control_room_key_id control_room_key
  unset control_room_previous_keys gcs_access_key_id gcs_secret_access_key
  [[ -z "${updates_file}" ]] || rm -f -- "${updates_file}"
}
trap cleanup EXIT

if [[ -n "${OMEGA_GCP_ACCESS_TOKEN:-}" ]]; then
  metadata_access_token="${OMEGA_GCP_ACCESS_TOKEN}"
else
  metadata_access_token="$(
    curl -fsS --max-time 10 -H 'Metadata-Flavor: Google' \
      'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' \
      | python3 -c 'import json,sys; value=json.load(sys.stdin).get("access_token", ""); sys.exit(1) if not value else print(value)'
  )" || die "unable to obtain the VM service-account token"
fi
[[ -n "${metadata_access_token}" ]] || die "VM service-account token is empty"

secret_value() {
  local secret_name="$1"
  secret_response="$(
    curl -fsS --max-time 15 \
      -H "Authorization: Bearer ${metadata_access_token}" \
      "https://secretmanager.googleapis.com/v1/projects/${OMEGA_GCP_PROJECT_ID}/secrets/${OMEGA_GCP_SECRET_PREFIX}${secret_name}/versions/latest:access" \
      2>/dev/null
  )" || return 1
  printf '%s' "${secret_response}" | python3 -c '
import base64
import json
import sys

try:
    encoded = json.load(sys.stdin).get("payload", {}).get("data", "")
    raw = base64.b64decode(encoded, validate=True)
    value = raw.decode("utf-8")
except Exception:
    raise SystemExit(1)
if not value or any(char in value for char in ("\x00", "\r", "\n")):
    raise SystemExit(1)
sys.stdout.write(value)
' 2>/dev/null
}

load_required_secret() {
  local secret_name="$1" variable_name="$2" value
  value="$(secret_value "${secret_name}")" \
    || die "required Secret Manager value unavailable: ${secret_name}"
  [[ -n "${value}" ]] || die "required Secret Manager value is empty: ${secret_name}"
  printf -v "${variable_name}" '%s' "${value}"
}

load_required_secret control_room_evidence_signing_key_id control_room_key_id
load_required_secret control_room_evidence_signing_key control_room_key
load_required_secret control_room_evidence_signing_previous_keys control_room_previous_keys
load_required_secret gcs_hmac_access_key_id gcs_access_key_id
load_required_secret gcs_hmac_secret_access_key gcs_secret_access_key

if [[ "${hydration_mode}" == "check" ]]; then
  printf '[gcp-secret-hydration] runtime secrets validated; environment unchanged\n'
  exit 0
fi

append_update() {
  local name="$1" value="$2" encoded
  encoded="$(printf '%s' "${value}" | base64 | tr -d '\r\n')"
  printf '%s\t%s\n' "${name}" "${encoded}" >> "${updates_file}"
}

append_update CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID "${control_room_key_id}"
append_update CONTROL_ROOM_EVIDENCE_SIGNING_KEY "${control_room_key}"
append_update CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS "${control_room_previous_keys}"
append_update GCS_ACCESS_KEY_ID "${gcs_access_key_id}"
append_update GCS_SECRET_ACCESS_KEY "${gcs_secret_access_key}"

for alias_name in \
  LAKEHOUSE_ACCESS_KEY LAKEHOUSE_SECRET_KEY \
  GOOGLE_HMAC_ACCESS_KEY_ID GOOGLE_HMAC_SECRET_ACCESS_KEY \
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN; do
  append_update "${alias_name}" ""
done

python3 - "${OMEGA_ENV_FILE}" "${updates_file}" <<'PY'
import base64
import os
from pathlib import Path
import tempfile
import sys

target = Path(sys.argv[1])
updates_path = Path(sys.argv[2])
updates: dict[str, str] = {}
for raw_line in updates_path.read_text(encoding="utf-8").splitlines():
    key, encoded = raw_line.split("\t", 1)
    updates[key] = base64.b64decode(encoded, validate=True).decode("utf-8")

original = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
output: list[str] = []
written: set[str] = set()
for line in original:
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in updates:
        if key not in written:
            output.append(f"{key}={updates[key]}")
            written.add(key)
        continue
    output.append(line)
for key, value in updates.items():
    if key not in written:
        output.append(f"{key}={value}")

target.parent.mkdir(parents=True, exist_ok=True)
fd, temporary_name = tempfile.mkstemp(prefix=".omega-env.", dir=target.parent)
try:
    os.fchmod(fd, 0o600)
    if target.exists():
        stat = target.stat()
        try:
            os.fchown(fd, stat.st_uid, stat.st_gid)
        except PermissionError:
            pass
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        fd = -1
        handle.write("\n".join(output) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_name, target)
    directory_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    if fd >= 0:
        os.close(fd)
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
PY

printf '[gcp-secret-hydration] runtime secrets updated atomically\n'
