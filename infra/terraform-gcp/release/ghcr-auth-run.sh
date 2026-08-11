#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

if [[ "$#" -eq 0 ]]; then
  echo "ERROR: ghcr-auth-run.sh requires a command to run." >&2
  exit 2
fi
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: ghcr-auth-run.sh must run as root on the canonical host." >&2
  exit 2
fi
# Authentication state is established only by this process.  Inherited flags,
# registry paths, or ambient workflow credentials are never authority.
unset OMEGA_GHCR_AUTH_ACTIVE OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED DOCKER_CONFIG
unset GHCR_TOKEN GITHUB_TOKEN

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

auth_root="/run/omega-gcp-ghcr-auth"
if [[ "$(findmnt -n -o FSTYPE --target /run 2>/dev/null || true)" != "tmpfs" || \
      -L /run || "$(stat -c '%u:%g' /run)" != "0:0" ]]; then
  echo "ERROR: ephemeral GHCR auth tmpfs is unavailable." >&2
  exit 6
fi
if [[ -e "$auth_root" || -L "$auth_root" ]]; then
  if [[ -L "$auth_root" || ! -d "$auth_root" || \
        "$(stat -c '%u:%g:%a' "$auth_root")" != "0:0:700" ]]; then
    echo "ERROR: existing GHCR auth directory is unsafe." >&2
    exit 6
  fi
else
  install -d -m 0700 -o root -g root "$auth_root"
fi
if [[ "$(stat -c '%u:%g:%a' "$auth_root")" != "0:0:700" ]]; then
  echo "ERROR: ephemeral GHCR auth directory is unsafe." >&2
  exit 6
fi
# Canonical operations are serialized by the host day-2 lock. Remove residue
# from an untrappable prior SIGKILL before reading a new secret version.
find "$auth_root" -mindepth 1 -maxdepth 1 -type d \
  -name 'omega-gcp-ghcr-auth.*' -exec rm -rf -- {} +
docker_config="$(mktemp -d "${auth_root%/}/omega-gcp-ghcr-auth.XXXXXX")"
chown root:root "$docker_config"
chmod 700 "$docker_config"
if [[ -L "$docker_config" || "$(stat -c '%u:%g:%a' "$docker_config")" != "0:0:700" ]]; then
  echo "ERROR: ephemeral Docker authentication directory is unsafe." >&2
  exit 6
fi
curl_config="$docker_config/secret-manager.curl"
context_file="$docker_config/auth-context.json"
export DOCKER_CONFIG="$docker_config"
logged_in=0
metadata_access_token=""
metadata_token_response=""
secret_response=""
credentials=""
ghcr_token=""
github_config=""

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
    --max-filesize 16384 --max-redirs 0 --proto '=http' \
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

def exact_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value

try:
    raw = sys.stdin.buffer.read(16385)
    if not raw or len(raw) > 16384:
        raise ValueError
    value = json.loads(raw, object_pairs_hook=exact_object)
except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
    raise SystemExit(1)
if not isinstance(value, dict) or set(value) != {"access_token", "expires_in", "token_type"}:
    raise SystemExit(1)
token = value.get("access_token")
expires_in = value.get("expires_in")
if value.get("token_type") != "Bearer" or isinstance(expires_in, bool) or not isinstance(expires_in, int) or not 1 <= expires_in <= 3600:
    raise SystemExit(1)
if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{20,4096}", token):
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
    --max-filesize 16384 --max-redirs 0 --proto '=https' --proto-redir '=https' \
    --noproxy '*' \
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

def exact_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value

def crc32c(data):
    crc = 0xffffffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82f63b78 if crc & 1 else 0)
    return (~crc) & 0xffffffff

expected = sys.argv[1]
try:
    raw = sys.stdin.buffer.read(16385)
    if not raw or len(raw) > 16384:
        raise ValueError
    response = json.loads(raw, object_pairs_hook=exact_object)
    if not isinstance(response, dict) or set(response) != {"name", "payload"} or response.get("name") != expected:
        raise ValueError
    payload = response["payload"]
    if not isinstance(payload, dict) or set(payload) != {"data", "dataCrc32c"}:
        raise ValueError
    checksum_text = payload["dataCrc32c"]
    if not isinstance(checksum_text, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", checksum_text):
        raise ValueError
    checksum = int(checksum_text)
    if checksum > 0xffffffff:
        raise ValueError
    encoded = payload["data"]
    if not isinstance(encoded, str) or not 4 <= len(encoded) <= 12288 or re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", encoded) is None:
        raise ValueError
    decoded_bytes = base64.b64decode(encoded, validate=True)
    if len(decoded_bytes) > 8192 or crc32c(decoded_bytes) != checksum:
        raise ValueError
    decoded = decoded_bytes.decode("utf-8")
    value = json.loads(decoded, object_pairs_hook=exact_object)
except (KeyError, TypeError, ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
    raise SystemExit(1)
if not isinstance(value, dict) or set(value) != {"username", "token"}:
    raise SystemExit(1)
username = value.get("username")
token = value.get("token")
if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", username):
    raise SystemExit(1)
if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9._~-]{20,512}", token) is None:
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

# Pullability alone cannot prove the mandated packages remain private. Query
# their authenticated package metadata with the same server-owned token and
# retain only a boolean capability in the child process.
github_config="$docker_config/github-api.curl"
github_headers="$docker_config/github-api.headers"
package_response_file="$docker_config/github-package.json"
printf 'header = "Authorization: Bearer %s"\n' "$ghcr_token" > "$github_config"
chmod 600 "$github_config"
for package in banxico inegi sec_edgar; do
  : > "$github_headers"
  : > "$package_response_file"
  if ! curl --fail --silent --show-error --max-time 10 \
      --max-filesize 65536 --max-redirs 0 --proto '=https' --proto-redir '=https' \
      --noproxy '*' \
      --config "$github_config" \
      --dump-header "$github_headers" \
      --output "$package_response_file" \
      -H 'Accept: application/vnd.github+json' \
      -H 'X-GitHub-Api-Version: 2022-11-28' \
      "https://api.github.com/users/emmanuelnavaromero02-commits/packages/container/${package}" \
      || ! python3 - "$package_response_file" "$package" <<'PY'
import json
import sys

def exact_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value

path, expected = sys.argv[1:]
try:
    raw = open(path, "rb").read(65537)
    if not raw or len(raw) > 65536:
        raise ValueError
    payload = json.loads(raw, object_pairs_hook=exact_object)
except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
    raise SystemExit(1)
if (
    not isinstance(payload, dict)
    or not isinstance(payload.get("id"), int)
    or isinstance(payload.get("id"), bool)
    or payload.get("id", 0) <= 0
    or payload.get("name") != expected
    or payload.get("package_type") != "container"
    or payload.get("visibility") != "private"
):
    raise SystemExit(1)
PY
  then
    echo "ERROR: required GHCR package privacy could not be verified." >&2
    exit 10
  fi
  if [[ "$package" == "banxico" ]] && ! python3 - "$github_headers" <<'PY'
import pathlib
import sys

values = []
for raw in pathlib.Path(sys.argv[1]).read_text(encoding="iso-8859-1").splitlines():
    name, separator, value = raw.partition(":")
    if separator and name.strip().lower() == "x-oauth-scopes":
        values.extend(item.strip() for item in value.split(",") if item.strip())
if set(values) != {"read:packages"} or len(values) != 1:
    raise SystemExit(1)
PY
  then
    echo "ERROR: GHCR pull credential scope is missing or exceeds read:packages." >&2
    exit 10
  fi
done
rm -f -- "$github_config" "$github_headers" "$package_response_file"
github_config=""

if ! printf '%s' "$ghcr_token" | \
  docker login ghcr.io --username "$ghcr_username" --password-stdin >/dev/null; then
  echo "ERROR: GHCR authentication failed." >&2
  exit 11
fi
logged_in=1
if [[ -L "$docker_config/config.json" || ! -f "$docker_config/config.json" ]]; then
  echo "ERROR: Docker did not create a regular GHCR auth file." >&2
  exit 11
fi
chown root:root "$docker_config/config.json"
chmod 600 "$docker_config/config.json"
python3 - "$docker_config/config.json" "$context_file" <<'PY'
import hashlib
import json
import os
import stat
import sys

config_path, context_path = sys.argv[1:]
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(config_path, flags)
try:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or not 2 <= info.st_size <= 65536:
        raise SystemExit(1)
    raw = os.read(fd, 65537)
    if len(raw) != info.st_size or len(raw) > 65536:
        raise SystemExit(1)
finally:
    os.close(fd)
document = {
    "config_sha256": hashlib.sha256(raw).hexdigest(),
    "owner": "emmanuelnavaromero02-commits",
    "private_packages": ["banxico", "inegi", "sec_edgar"],
    "registry": "ghcr.io",
    "schema_version": 1,
}
payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
fd = os.open(context_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400)
try:
    os.write(fd, payload)
    os.fsync(fd)
finally:
    os.close(fd)
PY
chown root:root "$context_file"
chmod 400 "$context_file"
unset ghcr_token ghcr_username
export OMEGA_GHCR_AUTH_ACTIVE=1
export OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED=1

command_status=0
"$@" || command_status=$?
exit "$command_status"
