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

gcp_environment="${OMEGA_GCP_ENVIRONMENT:-}"
secret_version="${OMEGA_GHCR_PULL_SECRET_VERSION:-}"
if [[ ! "$gcp_environment" =~ ^[a-z][a-z0-9-]{0,29}$ ]]; then
  echo "ERROR: OMEGA_GCP_ENVIRONMENT must be an explicit GCP environment name." >&2
  exit 4
fi
if [[ ! "$secret_version" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: OMEGA_GHCR_PULL_SECRET_VERSION must be an explicit numeric version." >&2
  exit 5
fi
secret_id="omega-${gcp_environment}-ghcr_pull_credentials"

auth_root="${OMEGA_GHCR_AUTH_TMPDIR:-/run}"
if [[ ! -d "$auth_root" || ! -w "$auth_root" ]]; then
  auth_root="${TMPDIR:-/tmp}"
fi
docker_config="$(mktemp -d "${auth_root%/}/omega-gcp-ghcr-auth.XXXXXX")"
chmod 700 "$docker_config"
curl_config="$docker_config/secret-manager.curl"
export DOCKER_CONFIG="$docker_config"
logged_in=0
metadata_access_token=""
metadata_token_response=""
secret_response=""
credentials=""
ghcr_token=""

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if [[ "$logged_in" == "1" ]]; then
    docker logout ghcr.io >/dev/null 2>&1
  fi
  unset metadata_access_token metadata_token_response secret_response
  unset credentials ghcr_token ghcr_username
  case "$docker_config" in
    "${auth_root%/}"/omega-gcp-ghcr-auth.*)
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

metadata_get() {
  local path="$1"
  curl --fail --silent --show-error --max-time 5 \
    --noproxy '*' \
    -H 'Metadata-Flavor: Google' \
    "http://metadata.google.internal/computeMetadata/v1/${path}"
}

if ! project_id="$(metadata_get project/project-id)"; then
  echo "ERROR: GCP project identity is unavailable from instance metadata." >&2
  exit 6
fi
if [[ ! "$project_id" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  echo "ERROR: GCP project identity returned by metadata is invalid." >&2
  exit 6
fi

if ! metadata_token_response="$(
  metadata_get instance/service-accounts/default/token
)"; then
  echo "ERROR: GCP service-account token is unavailable from metadata." >&2
  exit 7
fi
if ! metadata_access_token="$(
  printf '%s' "$metadata_token_response" | python3 -c '
import json
import re
import sys

try:
    value = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(1)
token = value.get("access_token") if isinstance(value, dict) else None
token_type = value.get("token_type") if isinstance(value, dict) else None
if token_type not in (None, "Bearer"):
    raise SystemExit(1)
if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~-]+", token):
    raise SystemExit(1)
sys.stdout.write(token)
'
)"; then
  echo "ERROR: GCP metadata returned an invalid service-account token." >&2
  exit 7
fi
unset metadata_token_response

# A curl config keeps the metadata token out of process arguments. Both this
# file and Docker's auth config live in the same mode-0700 ephemeral directory.
printf 'header = "Authorization: Bearer %s"\n' "$metadata_access_token" > "$curl_config"
chmod 600 "$curl_config"
unset metadata_access_token

secret_url="https://secretmanager.googleapis.com/v1/projects/${project_id}/secrets/${secret_id}/versions/${secret_version}:access"
if ! secret_response="$(
  curl --fail --silent --show-error --max-time 10 \
    --config "$curl_config" "$secret_url"
)"; then
  echo "ERROR: unable to read server-owned GHCR credentials." >&2
  exit 8
fi
rm -f -- "$curl_config"

expected_version="projects/${project_id}/secrets/${secret_id}/versions/${secret_version}"
if ! credentials="$(
  printf '%s' "$secret_response" | python3 -c '
import base64
import binascii
import json
import re
import sys

expected = sys.argv[1]
try:
    response = json.load(sys.stdin)
    if response.get("name") != expected:
        raise ValueError
    encoded = response["payload"]["data"]
    decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    value = json.loads(decoded)
except (KeyError, TypeError, ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
    raise SystemExit(1)
username = value.get("username") if isinstance(value, dict) else None
token = value.get("token") if isinstance(value, dict) else None
if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", username):
    raise SystemExit(1)
if not isinstance(token, str) or not token or any(ch.isspace() for ch in token):
    raise SystemExit(1)
sys.stdout.write(username + "\t" + token)
' "$expected_version"
)"; then
  echo "ERROR: server-owned GHCR credentials have an invalid schema or version." >&2
  exit 9
fi
unset secret_response

if [[ "$credentials" != *$'\t'* ]]; then
  echo "ERROR: server-owned GHCR credentials are incomplete." >&2
  exit 10
fi
ghcr_username="${credentials%%$'\t'*}"
ghcr_token="${credentials#*$'\t'}"
unset credentials
if [[ -z "$ghcr_username" || -z "$ghcr_token" ]]; then
  echo "ERROR: server-owned GHCR credentials are incomplete." >&2
  exit 10
fi

if ! printf '%s' "$ghcr_token" | \
  docker login ghcr.io --username "$ghcr_username" --password-stdin >/dev/null; then
  echo "ERROR: GHCR authentication failed." >&2
  exit 11
fi
logged_in=1
unset ghcr_token ghcr_username
export OMEGA_GHCR_AUTH_ACTIVE=1

command_status=0
"$@" || command_status=$?
exit "$command_status"
