#!/usr/bin/env bash
set -euo pipefail
umask 077

SHARED_ENV_FILE="${1:-.env}"
ENV_CONFIG="${MODECISSIONS_AWS_ENTRYPOINT_CONFIG:-/etc/modecissions/aws-entrypoint.env}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEYRING_VALIDATOR="${MODECISSIONS_EVIDENCE_KEYRING_VALIDATOR:-${SCRIPT_DIR}/../../../scripts/validate-evidence-keyring.py}"
MATERIAL_FILE=""
STAGE_FILE=""

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

cleanup() {
  [[ -z "$MATERIAL_FILE" ]] || rm -f -- "$MATERIAL_FILE"
  [[ -z "$STAGE_FILE" ]] || rm -f -- "$STAGE_FILE"
}
trap cleanup EXIT

if [[ -L "$SHARED_ENV_FILE" ]]; then
  fail "shared env file must not be a symlink: $SHARED_ENV_FILE"
fi
if [[ ! -f "$SHARED_ENV_FILE" ]]; then
  fail "shared env file does not exist: $SHARED_ENV_FILE"
fi

SHARED_ENV_DIR="$(cd "$(dirname "$SHARED_ENV_FILE")" && pwd -P)"
SHARED_ENV_FILE="${SHARED_ENV_DIR}/$(basename "$SHARED_ENV_FILE")"

if [[ -f "$ENV_CONFIG" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_CONFIG" >/dev/null 2>&1 ||
    fail "cannot load AWS entrypoint config"
fi
if ! source "$SHARED_ENV_FILE" >/dev/null 2>&1; then
  fail "cannot load shared env file"
fi
MATERIAL_FILE=""
STAGE_FILE=""

AWS_REGION="${AWS_REGION:-us-east-1}"
EVIDENCE_ENV_FILE="${MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE:-${AWS_ENV_FILE:-.env}.control-room-evidence}"
if [[ -z "$EVIDENCE_ENV_FILE" || "$EVIDENCE_ENV_FILE" == *$'\n'* || "$EVIDENCE_ENV_FILE" == *$'\r'* ]]; then
  fail "invalid control room evidence env path"
fi
if [[ "$EVIDENCE_ENV_FILE" == */ ]]; then
  fail "control room evidence env path must name a file"
fi
if [[ "$EVIDENCE_ENV_FILE" != /* ]]; then
  EVIDENCE_ENV_FILE="${SHARED_ENV_DIR}/${EVIDENCE_ENV_FILE}"
fi

EVIDENCE_ENV_DIR="$(dirname "$EVIDENCE_ENV_FILE")"
EVIDENCE_ENV_BASENAME="$(basename "$EVIDENCE_ENV_FILE")"
if [[ -L "$EVIDENCE_ENV_DIR" ]]; then
  fail "control room evidence env directory must not be a symlink"
fi
if [[ -e "$EVIDENCE_ENV_DIR" && ! -d "$EVIDENCE_ENV_DIR" ]]; then
  fail "control room evidence env parent is not a directory"
fi
if [[ -d "$EVIDENCE_ENV_DIR" ]]; then
  EVIDENCE_ENV_DIR="$(cd "$EVIDENCE_ENV_DIR" && pwd -P)"
  EVIDENCE_ENV_FILE="${EVIDENCE_ENV_DIR}/${EVIDENCE_ENV_BASENAME}"
fi
if [[ "$EVIDENCE_ENV_FILE" == "$SHARED_ENV_FILE" ]]; then
  fail "shared and control room evidence env files must be different"
fi

keyring_material_valid() {
  local current_id="$1"
  local current_key="$2"
  local previous_keys="$3"

  CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID="$current_id" \
    CONTROL_ROOM_EVIDENCE_SIGNING_KEY="$current_key" \
    CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS="$previous_keys" \
    python3 "$KEYRING_VALIDATOR" >/dev/null 2>&1
}

keyring_complete() (
  local current_id current_key previous_keys

  unset CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID
  unset CONTROL_ROOM_EVIDENCE_SIGNING_KEY
  unset CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS
  # shellcheck disable=SC1090
  source "$1" >/dev/null 2>&1 || return 1
  current_id="${CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID:-}"
  current_key="${CONTROL_ROOM_EVIDENCE_SIGNING_KEY:-}"
  previous_keys="${CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS:-}"
  keyring_material_valid "$current_id" "$current_key" "$previous_keys"
)

existing_keyring_complete() {
  local metadata

  if [[ ! -e "$EVIDENCE_ENV_FILE" && ! -L "$EVIDENCE_ENV_FILE" ]]; then
    return 1
  fi
  if [[ -L "$EVIDENCE_ENV_FILE" ]]; then
    fail "control room evidence env file must not be a symlink"
  fi
  if [[ ! -f "$EVIDENCE_ENV_FILE" ]]; then
    fail "control room evidence env path must be a regular file"
  fi
  metadata="$(stat -c '%u:%a' -- "$EVIDENCE_ENV_FILE")" ||
    fail "cannot inspect control room evidence env file"
  if [[ "$metadata" != "0:600" ]]; then
    fail "control room evidence env file must be owned by root with mode 600"
  fi
  keyring_complete "$EVIDENCE_ENV_FILE"
}

if existing_keyring_complete; then
  exit 0
fi

secret_arn() {
  local name="$1"
  local arn_var="MODECISSIONS_SECRET_${name}_ARN"
  printf '%s' "${!arn_var:-}"
}

default_secret_id() {
  local lower_name
  lower_name="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  printf 'modecissions/%s' "$lower_name"
}

fetch_secret() {
  local secret_id="$1"
  local attempts="$2"
  local attempt value

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if value="$(aws --region "$AWS_REGION" secretsmanager get-secret-value \
      --secret-id "$secret_id" \
      --query SecretString \
      --output text 2>/dev/null)" &&
      [[ -n "$value" && "$value" != *$'\n'* && "$value" != *$'\r'* ]]; then
      printf '%s' "$value"
      return 0
    fi
    if [[ "$attempt" -lt "$attempts" ]]; then
      sleep "$attempt"
    fi
  done
  return 1
}

required_secret() {
  local name="$1"
  local secret_id

  secret_id="$(secret_arn "$name")"
  if [[ -z "$secret_id" ]]; then
    secret_id="$(default_secret_id "$name")"
  fi
  fetch_secret "$secret_id" 3
}

ID_NAME="CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID"
KEY_NAME="CONTROL_ROOM_EVIDENCE_SIGNING_KEY"
PREVIOUS_NAME="CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS"

if ! CURRENT_ID="$(required_secret "$ID_NAME")"; then
  fail "cannot fetch required evidence signing key id"
fi
if ! CURRENT_KEY="$(required_secret "$KEY_NAME")"; then
  fail "cannot fetch required evidence signing key"
fi
PREVIOUS_SECRET_ID="$(secret_arn "$PREVIOUS_NAME")"
if [[ -n "$PREVIOUS_SECRET_ID" ]]; then
  if ! PREVIOUS_KEYS="$(fetch_secret "$PREVIOUS_SECRET_ID" 3)"; then
    fail "cannot fetch configured previous evidence signing keys"
  fi
else
  PREVIOUS_SECRET_ID="$(default_secret_id "$PREVIOUS_NAME")"
  if ! PREVIOUS_KEYS="$(fetch_secret "$PREVIOUS_SECRET_ID" 1)"; then
    PREVIOUS_KEYS="{}"
  fi
fi
if ! keyring_material_valid "$CURRENT_ID" "$CURRENT_KEY" "$PREVIOUS_KEYS"; then
  fail "fetched evidence signing keyring is invalid"
fi

write_env_line() {
  local output_file="$1"
  local name="$2"
  local value="$3"
  local escaped

  escaped="${value//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  escaped="${escaped//\$/\\$}"
  escaped="${escaped//\`/\\\`}"
  printf '%s="%s"\n' "$name" "$escaped" >> "$output_file"
}

MATERIAL_FILE="$(mktemp)"
chmod 600 "$MATERIAL_FILE"
write_env_line "$MATERIAL_FILE" "$ID_NAME" "$CURRENT_ID"
write_env_line "$MATERIAL_FILE" "$KEY_NAME" "$CURRENT_KEY"
write_env_line "$MATERIAL_FILE" "$PREVIOUS_NAME" "$PREVIOUS_KEYS"

if [[ -L "$EVIDENCE_ENV_DIR" ]]; then
  fail "control room evidence env directory must not be a symlink"
fi
if [[ -e "$EVIDENCE_ENV_DIR" && ! -d "$EVIDENCE_ENV_DIR" ]]; then
  fail "control room evidence env parent is not a directory"
fi
if [[ ! -d "$EVIDENCE_ENV_DIR" ]]; then
  install -d -m 700 -o root -g root -- "$EVIDENCE_ENV_DIR"
fi
if [[ -L "$EVIDENCE_ENV_DIR" || ! -d "$EVIDENCE_ENV_DIR" ]]; then
  fail "control room evidence env directory is unsafe"
fi

EVIDENCE_ENV_DIR="$(cd "$EVIDENCE_ENV_DIR" && pwd -P)"
EVIDENCE_ENV_FILE="${EVIDENCE_ENV_DIR}/${EVIDENCE_ENV_BASENAME}"
if [[ "$EVIDENCE_ENV_FILE" == "$SHARED_ENV_FILE" ]]; then
  fail "shared and control room evidence env files must be different"
fi
if existing_keyring_complete; then
  exit 0
fi

STAGE_FILE="$(mktemp "${EVIDENCE_ENV_DIR}/.evidence-env.XXXXXX")"
install -m 600 -o root -g root "$MATERIAL_FILE" "$STAGE_FILE"
mv -f -- "$STAGE_FILE" "$EVIDENCE_ENV_FILE"
STAGE_FILE=""
existing_keyring_complete ||
  fail "control room evidence env file was not installed securely"
