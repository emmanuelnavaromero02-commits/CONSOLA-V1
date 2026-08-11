#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

if [[ "$#" -eq 0 ]]; then
  echo "ERROR: ghcr-auth-run.sh requires a command to run." >&2
  exit 2
fi

if [[ "${OMEGA_GHCR_AUTH_ACTIVE:-0}" == "1" ]]; then
  if [[ -z "${DOCKER_CONFIG:-}" || ! -s "${DOCKER_CONFIG}/config.json" ]]; then
    echo "ERROR: inherited GHCR authentication context is invalid." >&2
    exit 3
  fi
  exec "$@"
fi

deploy_env="${OMEGA_DEPLOY_ENV_FILE:-/opt/modecissions/infra/terraform/deploy/.env}"
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
if [[ -z "$region" && -f "$deploy_env" ]]; then
  region="$(awk -F= '$1 == "AWS_REGION" {print substr($0, index($0, "=") + 1)}' "$deploy_env" | tail -n 1 | tr -d " '\"\r")"
fi
region="${region:-us-east-1}"
if [[ ! "$region" =~ ^[a-z]{2}(-gov)?-[a-z]+-[0-9]+$ ]]; then
  echo "ERROR: invalid AWS region for GHCR credential lookup." >&2
  exit 4
fi

secret_id="${OMEGA_GHCR_PULL_SECRET_ID:-modecissions/ghcr_pull_credentials}"
if [[ -z "$secret_id" ]]; then
  echo "ERROR: GHCR credential secret id is empty." >&2
  exit 5
fi

auth_root="${OMEGA_GHCR_AUTH_TMPDIR:-/run}"
if [[ ! -d "$auth_root" || ! -w "$auth_root" ]]; then
  auth_root="${TMPDIR:-/tmp}"
fi
docker_config="$(mktemp -d "${auth_root%/}/omega-ghcr-auth.XXXXXX")"
chmod 700 "$docker_config"
export DOCKER_CONFIG="$docker_config"
logged_in=0
ghcr_token=""
secret_json=""
credentials=""

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if [[ "$logged_in" == "1" ]]; then
    docker logout ghcr.io >/dev/null 2>&1
  fi
  unset ghcr_token secret_json credentials
  case "$docker_config" in
    "${auth_root%/}"/omega-ghcr-auth.*)
      rm -rf -- "$docker_config"
      ;;
    *)
      echo "ERROR: refusing to remove unexpected Docker config path." >&2
      status=70
      ;;
  esac
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

if ! secret_json="$(
  aws --region "$region" secretsmanager get-secret-value \
    --secret-id "$secret_id" \
    --version-stage AWSCURRENT \
    --query SecretString \
    --output text
)"; then
  echo "ERROR: unable to read server-owned GHCR credentials." >&2
  exit 6
fi

if ! credentials="$(
  printf '%s' "$secret_json" | python3 -c '
import json
import re
import sys

try:
    value = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(1)
username = value.get("username") if isinstance(value, dict) else None
token = value.get("token") if isinstance(value, dict) else None
if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9-]+", username):
    raise SystemExit(1)
if not isinstance(token, str) or not token or any(ch.isspace() for ch in token):
    raise SystemExit(1)
sys.stdout.write(username + "\t" + token)
'
)"; then
  echo "ERROR: server-owned GHCR credentials have an invalid schema." >&2
  exit 7
fi
unset secret_json

if [[ "$credentials" != *$'\t'* ]]; then
  echo "ERROR: server-owned GHCR credentials are incomplete." >&2
  exit 8
fi
ghcr_username="${credentials%%$'\t'*}"
ghcr_token="${credentials#*$'\t'}"
unset credentials
if [[ -z "$ghcr_username" || -z "$ghcr_token" ]]; then
  echo "ERROR: server-owned GHCR credentials are incomplete." >&2
  exit 8
fi

if ! printf '%s' "$ghcr_token" | \
  docker login ghcr.io --username "$ghcr_username" --password-stdin >/dev/null; then
  echo "ERROR: GHCR authentication failed." >&2
  exit 9
fi
logged_in=1
unset ghcr_token ghcr_username
export OMEGA_GHCR_AUTH_ACTIVE=1

command_status=0
"$@" || command_status=$?
exit "$command_status"
