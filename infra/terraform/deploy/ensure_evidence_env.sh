#!/usr/bin/env bash
set -euo pipefail

SHARED_ENV_FILE="${1:-.env}"
TEMP_FILE=""

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

cleanup() {
  if [[ -n "$TEMP_FILE" ]]; then
    rm -f -- "$TEMP_FILE"
  fi
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

resolve_evidence_env_file() (
  local resolved

  # shellcheck disable=SC1090
  source "$SHARED_ENV_FILE" || return 1
  resolved="${MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE:-${AWS_ENV_FILE:-.env}.control-room-evidence}"
  if [[ -z "$resolved" || "$resolved" == *$'\n'* || "$resolved" == *$'\r'* ]]; then
    return 1
  fi
  printf '%s' "$resolved"
)

if ! EVIDENCE_ENV_FILE="$(resolve_evidence_env_file)"; then
  fail "invalid control room evidence env path"
fi
if [[ "$EVIDENCE_ENV_FILE" == */ ]]; then
  fail "control room evidence env path must name a file"
fi
if [[ "$EVIDENCE_ENV_FILE" != /* ]]; then
  EVIDENCE_ENV_FILE="${SHARED_ENV_DIR}/${EVIDENCE_ENV_FILE}"
fi

EVIDENCE_ENV_DIR="$(dirname "$EVIDENCE_ENV_FILE")"
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
EVIDENCE_ENV_FILE="${EVIDENCE_ENV_DIR}/$(basename "$EVIDENCE_ENV_FILE")"
if [[ "$EVIDENCE_ENV_FILE" == "$SHARED_ENV_FILE" ]]; then
  fail "shared and control room evidence env files must be different"
fi

validate_evidence_env() {
  local metadata

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
}

if [[ -e "$EVIDENCE_ENV_FILE" || -L "$EVIDENCE_ENV_FILE" ]]; then
  validate_evidence_env
  exit 0
fi

TEMP_FILE="$(mktemp "${EVIDENCE_ENV_DIR}/.evidence-env.XXXXXX")"
install -m 600 -o root -g root /dev/null "$TEMP_FILE"
if ! ln -- "$TEMP_FILE" "$EVIDENCE_ENV_FILE" 2>/dev/null; then
  rm -f -- "$TEMP_FILE"
  TEMP_FILE=""
  if [[ ! -e "$EVIDENCE_ENV_FILE" && ! -L "$EVIDENCE_ENV_FILE" ]]; then
    fail "cannot create control room evidence env file"
  fi
  validate_evidence_env
  exit 0
fi
rm -f -- "$TEMP_FILE"
TEMP_FILE=""
validate_evidence_env
