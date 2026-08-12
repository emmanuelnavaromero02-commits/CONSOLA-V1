#!/bin/bash -p
set -Eeuo pipefail
set +x
umask 077

if [[ "$-" != *p* ]]; then
  echo "ERROR: ghcr-auth-run.sh requires privileged Bash mode." >&2
  exit 2
fi

readonly PYTHON_BIN=/usr/bin/python3
readonly CURL_BIN=/usr/bin/curl
readonly DOCKER_BIN=/usr/bin/docker
readonly FINDMNT_BIN=/usr/bin/findmnt
readonly STAT_BIN=/usr/bin/stat
readonly FIND_BIN=/usr/bin/find
readonly RM_BIN=/usr/bin/rm
readonly MKTEMP_BIN=/usr/bin/mktemp
readonly CHOWN_BIN=/usr/bin/chown
readonly CHMOD_BIN=/usr/bin/chmod
readonly ID_BIN=/usr/bin/id
readonly DIRNAME_BIN=/usr/bin/dirname
readonly CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
readonly HOST_IDENTITY=/etc/omega/gcp-host-identity.json
readonly SECRET_AUTHORITY=/etc/omega/ghcr-pull-secret-authority.json
readonly GCP_ENVIRONMENT=staging
readonly AUTH_PARENT_FD=4
readonly AUTH_ROOT_FD=5
readonly AUTH_LOCK_FD=9
readonly AUTH_LOCK_NAME=.omega-gcp-ghcr-release.lock

# Privileged Bash ignores BASH_ENV and exported functions at startup. Reject
# their raw encodings before any metadata request or secret read.
if ! "$PYTHON_BIN" -I - <<'PY'
import os

if any(name.startswith("BASH_FUNC_") for name in os.environ):
    raise SystemExit(1)
PY
then
  echo "ERROR: exported shell functions are forbidden in the auth boundary." >&2
  exit 2
fi

# A root-owned pull credential must never inherit executable, Python, TLS,
# Git, or Docker client authority from the invoking shell.  In particular,
# DOCKER_HOST/DOCKER_CONTEXT may not redirect credentials to a remote daemon.
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LANG=C.UTF-8 LC_ALL=C.UTF-8
export PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE
unset SSL_CERT_FILE SSL_CERT_DIR CURL_CA_BUNDLE CURL_HOME XDG_CONFIG_HOME
unset SSLKEYLOGFILE PYTHONHTTPSVERIFY OPENSSL_CONF CURL_SSL_BACKEND
unset LD_PRELOAD LD_LIBRARY_PATH DYLD_LIBRARY_PATH DYLD_INSERT_LIBRARIES
unset BASH_ENV ENV CDPATH
unset HOME TMPDIR TMP TEMP POSIXLY_CORRECT BLOCK_SIZE TIME_STYLE
unset BASH_XTRACEFD PS4 HISTFILE INPUTRC
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY NO_PROXY
unset all_proxy http_proxy https_proxy no_proxy
while IFS= read -r environment_name; do
  case "$environment_name" in
    DOCKER_*|COMPOSE_*|GIT_*) unset "$environment_name" ;;
  esac
done < <(compgen -e)
unset OMEGA_GCP_ENVIRONMENT

if [[ "$#" -ne 6 ]]; then
  echo "ERROR: ghcr-auth-run.sh requires the exact sibling preflight and five authority arguments." >&2
  exit 2
fi
preflight_script="$1"
owner="$2"
image_tag="$3"
source_sha="$4"
version="$5"
lock_file="$6"
if [[ "${EUID:-$("$ID_BIN" -u)}" -ne 0 ]]; then
  echo "ERROR: ghcr-auth-run.sh must run as root on the canonical host." >&2
  exit 2
fi

if [[ "${BASH_SOURCE[0]}" != "/proc/self/fd/11" ]]; then
  if [[ -n "${OMEGA_GHCR_BUNDLE_BOUND:-}" || \
        -n "${OMEGA_GHCR_LOCK_BOOTSTRAPPED:-}" ]]; then
    echo "ERROR: caller-supplied GHCR bundle state is forbidden." >&2
    exit 2
  fi
  script_dir="$(cd -P -- "$("$DIRNAME_BIN" -- "${BASH_SOURCE[0]}")" && pwd -P)"
  runner_script="${script_dir}/ghcr-auth-run.sh"
  session_helper_path="${script_dir}/secure-ghcr-session.py"
  if [[ "${BASH_SOURCE[0]}" != "$runner_script" || \
        "$preflight_script" != "${script_dir}/preflight-release-images.sh" || \
        ! -f "$session_helper_path" || -L "$session_helper_path" ]]; then
    echo "ERROR: initial GHCR bundle entrypoints differ." >&2
    exit 2
  fi
  exec 19<"$session_helper_path"
  exec "$PYTHON_BIN" -I /proc/self/fd/19 lock-exec \
    --auth-root /run/omega-gcp-ghcr-auth --bundle-dir "$script_dir" \
    --script "$runner_script" --preflight "$preflight_script" \
    --owner "$owner" --image-tag "$image_tag" --source-sha "$source_sha" \
    --version "$version" --lock-file "$lock_file"
fi
if [[ "${OMEGA_GHCR_BUNDLE_DIRECTORY_FD:-}" != "10" || \
      "${OMEGA_GHCR_BUNDLE_RUNNER_FD:-}" != "11" || \
      "${OMEGA_GHCR_BUNDLE_HELPER_FD:-}" != "12" || \
      "${OMEGA_GHCR_BUNDLE_PREFLIGHT_FD:-}" != "13" || \
      "${OMEGA_GHCR_BUNDLE_AUTHORITY_VALIDATOR_FD:-}" != "14" || \
      "${OMEGA_GHCR_BUNDLE_LOCK_VALIDATOR_FD:-}" != "15" || \
      "${OMEGA_GHCR_BUNDLE_PUBLISHER_FD:-}" != "16" || \
      "${OMEGA_GHCR_BUNDLE_RELEASE_IMAGES_FD:-}" != "17" || \
      "${OMEGA_GHCR_BUNDLE_MANIFEST_FD:-}" != "18" ]]; then
  echo "ERROR: sealed GHCR bundle descriptors are unavailable." >&2
  exit 2
fi
if ! "$PYTHON_BIN" -I - <<'PY'
import hashlib
import json
import os
import re
import stat

directory_fd = 10
files = {
    "ghcr-auth-run.sh": (11, 0o500),
    "secure-ghcr-session.py": (12, 0o400),
    "preflight-release-images.sh": (13, 0o500),
    "validate-image-authority.py": (14, 0o400),
    "validate-lock-output.py": (15, 0o400),
    "publish-image-lock.py": (16, 0o400),
    "release_images.py": (17, 0o400),
    "bundle-manifest.json": (18, 0o400),
}

def exact_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value

def read_exact(descriptor, maximum):
    info = os.fstat(descriptor)
    raw = os.pread(descriptor, maximum + 1, 0)
    if not raw or len(raw) != info.st_size or len(raw) > maximum:
        raise ValueError
    return raw

try:
    directory = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != 0
        or directory.st_gid != 0
        or stat.S_IMODE(directory.st_mode) != 0o500
        or set(os.listdir(directory_fd)) != set(files)
    ):
        raise ValueError
    manifest_raw = read_exact(18, 131072)
    manifest = json.loads(manifest_raw, object_pairs_hook=exact_object)
    if (
        manifest_raw
        != (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        or set(manifest) != {"files", "schema_version", "source_sha"}
        or manifest.get("schema_version") != 1
        or re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("source_sha", ""))) is None
        or not isinstance(manifest.get("files"), dict)
        or set(manifest["files"]) != set(files) - {"bundle-manifest.json"}
    ):
        raise ValueError
    for name, (descriptor, mode) in files.items():
        info = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != mode
            or info.st_nlink != 1
            or not 1 <= info.st_size <= 8 * 1024 * 1024
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ValueError
        if name != "bundle-manifest.json":
            raw = read_exact(descriptor, 8 * 1024 * 1024)
            row = manifest["files"].get(name)
            if (
                not isinstance(row, dict)
                or set(row) != {"mode", "sha256", "size"}
                or row.get("mode") != f"{mode:04o}"
                or row.get("size") != len(raw)
                or row.get("sha256") != hashlib.sha256(raw).hexdigest()
            ):
                raise ValueError
except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
    raise SystemExit(1)
PY
then
  echo "ERROR: sealed GHCR helper bundle failed descriptor validation." >&2
  exit 2
fi
runner_script=/proc/self/fd/11
session_helper=/proc/self/fd/12
preflight_script=/proc/self/fd/13

if ! "$PYTHON_BIN" -I - "$CA_BUNDLE" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
try:
    info = os.lstat(path)
    if (
        not os.path.isabs(path)
        or os.path.realpath(path) != path
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
        or info.st_nlink != 1
        or not 1024 <= info.st_size <= 8 * 1024 * 1024
    ):
        raise ValueError
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        current = os.fstat(descriptor)
        if (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mode,
            current.st_uid,
            current.st_gid,
            current.st_nlink,
        ) != (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mode,
            info.st_uid,
            info.st_gid,
            info.st_nlink,
        ):
            raise ValueError
        raw = os.read(descriptor, 8 * 1024 * 1024 + 1)
        if len(raw) != current.st_size or b"-----BEGIN CERTIFICATE-----" not in raw:
            raise ValueError
    finally:
        os.close(descriptor)
except (OSError, ValueError):
    raise SystemExit(1)
PY
then
  echo "ERROR: canonical root-owned TLS CA bundle is unavailable." >&2
  exit 6
fi

docker_socket="/run/docker.sock"
if ! "$PYTHON_BIN" -I - "$docker_socket" <<'PY'
import os
import stat
import sys

try:
    info = os.lstat(sys.argv[1])
except OSError:
    raise SystemExit(1)
if (
    not stat.S_ISSOCK(info.st_mode)
    or info.st_uid != 0
    or info.st_nlink != 1
    or stat.S_IMODE(info.st_mode) & 0o002
):
    raise SystemExit(1)
PY
then
  echo "ERROR: canonical local root-owned Docker socket is unavailable." >&2
  exit 6
fi
export DOCKER_HOST="unix://${docker_socket}"

# Authentication state is established only by this process.  Inherited flags,
# registry paths, or ambient workflow credentials are never authority.
unset OMEGA_GHCR_AUTH_ACTIVE OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED DOCKER_CONFIG
unset GHCR_TOKEN GITHUB_TOKEN

gcp_environment="$GCP_ENVIRONMENT"
requested_secret_version="${OMEGA_GHCR_PULL_SECRET_VERSION:-}"
if [[ -n "$requested_secret_version" && \
      ! "$requested_secret_version" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: requested GHCR secret version must be an explicit numeric version." >&2
  exit 5
fi
if ! secret_authority_fields="$("$PYTHON_BIN" -I - "$SECRET_AUTHORITY" \
    "$gcp_environment" <<'PY'
import json
import os
import re
import stat
import sys

path, expected_environment = sys.argv[1:]

def exact_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value

try:
    named = os.lstat(path)
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        raw = os.read(descriptor, 4097)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not os.path.isabs(path)
        or os.path.realpath(path) != path
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_gid != 0
        or stat.S_IMODE(before.st_mode) != 0o400
        or before.st_nlink != 1
        or not 64 <= before.st_size <= 4096
        or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        or len(raw) != before.st_size
    ):
        raise ValueError
    value = json.loads(raw, object_pairs_hook=exact_object)
    canonical = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if (
        raw != canonical
        or set(value) != {"environment", "project_id", "schema_version", "secret_id", "secret_version_alias"}
        or value.get("schema_version") != 1
        or value.get("environment") != expected_environment
        or re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", str(value.get("project_id", ""))) is None
        or value.get("secret_id") != f"omega-{expected_environment}-ghcr_pull_credentials"
        or value.get("secret_version_alias") != "active"
    ):
        raise ValueError
except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
    raise SystemExit(1)
sys.stdout.write(
    value["project_id"] + "\t" + value["secret_id"] + "\t" + value["secret_version_alias"]
)
PY
)"; then
  echo "ERROR: root-owned GHCR secret-version authority is unavailable." >&2
  exit 5
fi
IFS=$'\t' read -r authority_project_id secret_id secret_version_alias \
  <<<"$secret_authority_fields"
unset secret_authority_fields

auth_root="/run/omega-gcp-ghcr-auth"
if [[ "$("$FINDMNT_BIN" -n -o FSTYPE --target /run 2>/dev/null || true)" != "tmpfs" || \
      -L /run || "$("$STAT_BIN" -c '%u:%g' /run)" != "0:0" ]]; then
  echo "ERROR: ephemeral GHCR auth tmpfs is unavailable." >&2
  exit 6
fi
if [[ "${OMEGA_GHCR_LOCK_BOOTSTRAPPED:-0}" != "1" ]]; then
  if [[ ! -f "$session_helper" ]]; then
    echo "ERROR: secure GHCR session helper is unavailable." >&2
    exit 6
  fi
  exec "$PYTHON_BIN" -I "$session_helper" lock-exec \
    --auth-root "$auth_root" --script "$runner_script" \
    --preflight "$preflight_script" --owner "$owner" \
    --image-tag "$image_tag" --source-sha "$source_sha" \
    --version "$version" --lock-file "$lock_file"
fi
unset OMEGA_GHCR_LOCK_BOOTSTRAPPED
if [[ "${OMEGA_GHCR_AUTH_PARENT_FD:-}" != "$AUTH_PARENT_FD" || \
      "${OMEGA_GHCR_AUTH_ROOT_FD:-}" != "$AUTH_ROOT_FD" || \
      "${OMEGA_GHCR_AUTH_ROOT_NAME:-}" != "${auth_root##*/}" || \
      "${OMEGA_GHCR_AUTH_LOCK_FD:-}" != "$AUTH_LOCK_FD" ]] || \
   ! "$PYTHON_BIN" -I - "$AUTH_PARENT_FD" "$AUTH_ROOT_FD" "$AUTH_LOCK_FD" \
       "$AUTH_LOCK_NAME" "${auth_root##*/}" <<'PY'
import fcntl
import os
import stat
import sys

parent_fd, root_fd, lock_fd = (int(value) for value in sys.argv[1:4])
lock_name, root_name = sys.argv[4:]
try:
    parent = os.fstat(parent_fd)
    root = os.fstat(root_fd)
    lock = os.fstat(lock_fd)
    named_root = os.stat(root_name, dir_fd=parent_fd, follow_symlinks=False)
    named_lock = os.stat(lock_name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or parent.st_gid != os.getegid()
        or stat.S_IMODE(parent.st_mode) & 0o022
        or not stat.S_ISDIR(root.st_mode)
        or root.st_uid != os.geteuid()
        or root.st_gid != os.getegid()
        or stat.S_IMODE(root.st_mode) != 0o700
        or (root.st_dev, root.st_ino) != (named_root.st_dev, named_root.st_ino)
        or not stat.S_ISREG(lock.st_mode)
        or lock.st_uid != os.geteuid()
        or lock.st_gid != os.getegid()
        or stat.S_IMODE(lock.st_mode) != 0o600
        or lock.st_nlink != 1
        or (lock.st_dev, lock.st_ino) != (named_lock.st_dev, named_lock.st_ino)
    ):
        raise ValueError
    child = os.fork()
    if child == 0:
        os.close(lock_fd)
        probe = os.open(
            lock_name,
            os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os._exit(0)
        else:
            fcntl.flock(probe, fcntl.LOCK_UN)
            os._exit(1)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        raise ValueError
except (OSError, ValueError):
    raise SystemExit(1)
PY
then
  echo "ERROR: inherited canonical GHCR host lock is invalid or not held." >&2
  exit 6
fi
auth_lock="${auth_root%/*}/${AUTH_LOCK_NAME}"
auth_root_fd_path="/proc/self/fd/${AUTH_ROOT_FD}"
# Canonical operations are serialized by the same open file description.
# Remove only session directories below the stable root descriptor and never
# pass the lock descriptor to find/rm.
"$FIND_BIN" "$auth_root_fd_path" -mindepth 1 -maxdepth 1 -type d \
  -name 'omega-gcp-ghcr-auth.*' -exec "$RM_BIN" -rf -- {} + 4>&- 9>&-
docker_config="$("$MKTEMP_BIN" -d "${auth_root_fd_path}/omega-gcp-ghcr-auth.XXXXXX" 4>&- 9>&-)"
docker_config_name="${docker_config##*/}"
without_lock_fd() (
  exec 4>&- 9>&-
  exec "$@"
)
write_private_stdin() (
  exec 4>&- 9>&-
  exec "$PYTHON_BIN" -I -c '
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
mode = int(sys.argv[2], 8)
maximum = int(sys.argv[3])
if not path.is_absolute() or path.name in {"", ".", ".."}:
    raise SystemExit(1)
payload = sys.stdin.buffer.read(maximum + 1)
if not payload or len(payload) > maximum:
    raise SystemExit(1)
flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
parent_fd = os.open(path.parent, flags)
try:
    parent = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or parent.st_gid != os.getegid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise ValueError
    descriptor = os.open(
        path.name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        mode,
        dir_fd=parent_fd,
    )
    try:
        os.fchown(descriptor, os.geteuid(), os.getegid())
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(parent_fd)
finally:
    os.close(parent_fd)
' "$@"
)
without_lock_fd "$CHOWN_BIN" root:root "$docker_config"
without_lock_fd "$CHMOD_BIN" 700 "$docker_config"
if [[ -L "$docker_config" || "$(without_lock_fd "$STAT_BIN" -c '%u:%g:%a' "$docker_config")" != "0:0:700" ]]; then
  echo "ERROR: ephemeral Docker authentication directory is unsafe." >&2
  exit 6
fi
docker_config_identity="$(without_lock_fd "$STAT_BIN" -c '%d:%i' "$docker_config")"
if [[ ! "$docker_config_identity" =~ ^[0-9]+:[0-9]+$ ]]; then
  echo "ERROR: ephemeral Docker authentication identity is invalid." >&2
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
    without_lock_fd "$PYTHON_BIN" -I "$session_helper" docker-exec \
      --docker "$DOCKER_BIN" --operation logout >/dev/null 2>&1
  fi
  unset metadata_access_token metadata_token_response secret_response
  unset secret_http_response secret_http_status package_http_status
  unset credentials ghcr_token ghcr_username
  case "$docker_config_name" in
    omega-gcp-ghcr-auth.??????)
      without_lock_fd "$RM_BIN" -rf -- "$docker_config"
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
  local response http_status body
  if ! response="$(
    without_lock_fd "$CURL_BIN" -q --fail --silent --show-error \
      --connect-timeout 3 --max-time 5 --max-filesize 16384 --max-redirs 0 \
      --proto '=http' --request GET --noproxy '*' \
      --resolve 'metadata.google.internal:80:169.254.169.254' \
      --write-out $'\n%{http_code}' \
      -H 'Metadata-Flavor: Google' \
      "http://metadata.google.internal/computeMetadata/v1/${path}"
  )"; then
    return 1
  fi
  http_status="${response##*$'\n'}"
  body="${response%$'\n'*}"
  if [[ "$http_status" != "200" || "$body" == "$response" ]]; then
    return 1
  fi
  printf '%s' "$body"
}

if ! project_id="$(metadata_get project/project-id)" || \
   ! instance_id="$(metadata_get instance/id)" || \
   ! instance_name="$(metadata_get instance/name)" || \
   ! instance_zone_resource="$(metadata_get instance/zone)" || \
   ! service_account_email="$(metadata_get instance/service-accounts/default/email)"; then
  echo "ERROR: complete GCP host identity is unavailable from instance metadata." >&2
  exit 6
fi
if [[ ! "$instance_zone_resource" =~ ^projects/[0-9]+/zones/([a-z][a-z0-9-]{2,62})$ ]]; then
  echo "ERROR: GCP host identity returned by metadata is invalid." >&2
  exit 6
fi
instance_zone="${BASH_REMATCH[1]}"
if [[ "$project_id" != "$authority_project_id" ]]; then
  echo "ERROR: server-owned GHCR secret authority belongs to another project." >&2
  exit 6
fi
unset authority_project_id

# The root-owned image-pull authority is pinned to one provisioned VM.  This
# local attestation is validated before the service-account token endpoint or
# Secret Manager is contacted; ambient environment variables are never host
# identity. The reviewed bundle installer derives the non-secret authority
# from this exact provisioned identity without accepting caller-selected
# project, environment, secret, or version values.
if ! "$PYTHON_BIN" -I - "$HOST_IDENTITY" "$project_id" "$instance_id" \
    "$instance_name" "$instance_zone" "$service_account_email" <<'PY'
import json
import os
import re
import stat
import sys

path, project_id, instance_id, instance_name, zone, service_account_email = sys.argv[1:]

def exact_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value

try:
    if (
        not os.path.isabs(path)
        or os.path.realpath(path) != path
        or os.path.islink(path)
        or re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project_id) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", instance_id) is None
        or re.fullmatch(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", instance_name) is None
        or re.fullmatch(r"[a-z][a-z0-9-]{2,62}", zone) is None
        or re.fullmatch(
            r"[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com",
            service_account_email,
        ) is None
    ):
        raise ValueError
    named = os.lstat(path)
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_nlink != 1
            or not 128 <= before.st_size <= 4096
            or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ValueError
        raw = os.read(descriptor, 4097)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError
    finally:
        os.close(descriptor)
    value = json.loads(raw, object_pairs_hook=exact_object)
    expected = {
        "instance_id": instance_id,
        "instance_name": instance_name,
        "project_id": project_id,
        "schema_version": 1,
        "service_account_email": service_account_email,
        "zone": zone,
    }
    canonical = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if (
        isinstance(value.get("schema_version"), bool)
        or not isinstance(value.get("schema_version"), int)
        or value != expected
        or raw != canonical
    ):
        raise ValueError
except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
    raise SystemExit(1)
PY
then
  echo "ERROR: canonical root-owned GCP host identity does not match metadata." >&2
  exit 6
fi
unset instance_id instance_name instance_zone instance_zone_resource service_account_email

if ! metadata_token_response="$(
  metadata_get instance/service-accounts/default/token
)"; then
  echo "ERROR: GCP service-account token is unavailable from metadata." >&2
  exit 7
fi
if ! metadata_access_token="$(
  printf '%s' "$metadata_token_response" | without_lock_fd "$PYTHON_BIN" -I -c '
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
printf 'header = "Authorization: Bearer %s"\n' "$metadata_access_token" | \
  write_private_stdin "$curl_config" 0600 8192
unset metadata_access_token

secret_url="https://secretmanager.googleapis.com/v1/projects/${project_id}/secrets/${secret_id}/versions/${secret_version_alias}:access"
secret_http_response=""
if ! secret_http_response="$(
  without_lock_fd "$CURL_BIN" -q --fail --silent --show-error --max-time 10 \
    --connect-timeout 5 --max-filesize 16384 --max-redirs 0 \
    --proto '=https' --proto-redir '=https' --tlsv1.2 --request GET \
    --cacert "$CA_BUNDLE" \
    --noproxy '*' \
    --write-out $'\n%{http_code}' \
    --config "$curl_config" "$secret_url"
)"; then
  echo "ERROR: unable to read server-owned GHCR credentials." >&2
  exit 8
fi
secret_http_status="${secret_http_response##*$'\n'}"
secret_response="${secret_http_response%$'\n'*}"
unset secret_http_response
if [[ "$secret_http_status" != "200" || "$secret_response" == "$secret_http_status" ]]; then
  echo "ERROR: Secret Manager returned an unexpected HTTP status." >&2
  exit 8
fi
unset secret_http_status
without_lock_fd "$RM_BIN" -f -- "$curl_config"

expected_version_prefix="projects/${project_id}/secrets/${secret_id}/versions/"
if ! credentials="$(
  printf '%s' "$secret_response" | without_lock_fd "$PYTHON_BIN" -I -c '
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

expected_prefix = sys.argv[1]
try:
    raw = sys.stdin.buffer.read(16385)
    if not raw or len(raw) > 16384:
        raise ValueError
    response = json.loads(raw, object_pairs_hook=exact_object)
    if (
        not isinstance(response, dict)
        or set(response) != {"name", "payload"}
        or not isinstance(response.get("name"), str)
        or re.fullmatch(re.escape(expected_prefix) + r"[1-9][0-9]*", response["name"])
        is None
    ):
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
sys.stdout.write(response["name"] + "\t" + username + "\t" + token)
' "$expected_version_prefix"
)"; then
  echo "ERROR: server-owned GHCR credentials have an invalid schema or version." >&2
  exit 9
fi
unset secret_response

if [[ "$credentials" != *$'\t'*$'\t'* ]]; then
  echo "ERROR: server-owned GHCR credentials are incomplete." >&2
  exit 10
fi
resolved_secret_version_resource="${credentials%%$'\t'*}"
credential_fields="${credentials#*$'\t'}"
ghcr_username="${credential_fields%%$'\t'*}"
ghcr_token="${credential_fields#*$'\t'}"
unset credentials
unset credential_fields
resolved_secret_version="${resolved_secret_version_resource##*/}"
if [[ ! "$resolved_secret_version" =~ ^[1-9][0-9]*$ || \
      ( -n "$requested_secret_version" && \
        "$requested_secret_version" != "$resolved_secret_version" ) ]]; then
  echo "ERROR: resolved GHCR secret version differs from caller assertion." >&2
  exit 9
fi
unset requested_secret_version resolved_secret_version
if [[ -z "$ghcr_username" || -z "$ghcr_token" ]]; then
  echo "ERROR: server-owned GHCR credentials are incomplete." >&2
  exit 10
fi

# Pullability alone cannot prove the mandated packages remain private. Query
# their authenticated package metadata with the same server-owned token and
# retain only a boolean capability in the child process.
github_config="$docker_config/github-api.curl"
github_headers=""
package_response_file=""
printf 'header = "Authorization: Bearer %s"\n' "$ghcr_token" | \
  write_private_stdin "$github_config" 0600 2048
for package in banxico inegi sec_edgar; do
  github_headers="$(without_lock_fd "$MKTEMP_BIN" "${docker_config}/github-api.headers.XXXXXX")"
  package_response_file="$(without_lock_fd "$MKTEMP_BIN" "${docker_config}/github-package.json.XXXXXX")"
  without_lock_fd "$CHMOD_BIN" 600 "$github_headers" "$package_response_file"
  package_http_status=""
  if ! package_http_status="$(without_lock_fd "$CURL_BIN" -q --fail --silent --show-error --max-time 10 \
      --connect-timeout 5 --max-filesize 65536 --max-redirs 0 \
      --proto '=https' --proto-redir '=https' --tlsv1.2 --request GET \
      --cacert "$CA_BUNDLE" \
      --noproxy '*' \
      --write-out '%{http_code}' \
      --config "$github_config" \
      --dump-header "$github_headers" \
      --output "$package_response_file" \
      -H 'Accept: application/vnd.github+json' \
      -H 'X-GitHub-Api-Version: 2022-11-28' \
      "https://api.github.com/users/emmanuelnavaromero02-commits/packages/container/${package}")" \
      || [[ "$package_http_status" != "200" ]] \
      || ! without_lock_fd "$PYTHON_BIN" -I - "$package_response_file" "$package" <<'PY'
import json
import os
import re
import stat
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
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        raw = os.read(descriptor, 65537)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or not raw
        or len(raw) != info.st_size
        or len(raw) > 65536
    ):
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
  if [[ "$package" == "banxico" ]] && ! without_lock_fd "$PYTHON_BIN" -I - "$github_headers" <<'PY'
import os
import stat
import sys

values = []
descriptor = os.open(
    sys.argv[1],
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
)
try:
    info = os.fstat(descriptor)
    raw_headers = os.read(descriptor, 65537)
finally:
    os.close(descriptor)
if (
    not stat.S_ISREG(info.st_mode)
    or info.st_uid != os.geteuid()
    or info.st_gid != os.getegid()
    or stat.S_IMODE(info.st_mode) != 0o600
    or info.st_nlink != 1
    or len(raw_headers) != info.st_size
    or len(raw_headers) > 65536
):
    raise SystemExit(1)
for raw in raw_headers.decode("iso-8859-1").splitlines():
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
  without_lock_fd "$RM_BIN" -f -- "$github_headers" "$package_response_file"
  github_headers=""
  package_response_file=""
done
without_lock_fd "$RM_BIN" -f -- "$github_config"
github_config=""

if ! printf '%s' "$ghcr_token" | \
  without_lock_fd "$PYTHON_BIN" -I "$session_helper" docker-exec \
    --docker "$DOCKER_BIN" --operation login --username "$ghcr_username" \
    >/dev/null; then
  echo "ERROR: GHCR authentication failed." >&2
  exit 11
fi
logged_in=1
if [[ -L "$docker_config/config.json" || ! -f "$docker_config/config.json" ]]; then
  echo "ERROR: Docker did not create a regular GHCR auth file." >&2
  exit 11
fi
without_lock_fd "$PYTHON_BIN" -I - "$docker_config/config.json" "$context_file" \
  "$resolved_secret_version_resource" <<'PY'
import hashlib
import json
import os
import re
import stat
import sys

config_path, context_path, secret_version_resource = sys.argv[1:]
if not re.fullmatch(
    r"projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/secrets/"
    r"omega-staging-ghcr_pull_credentials/versions/[1-9][0-9]*",
    secret_version_resource,
):
    raise SystemExit(1)
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(config_path, flags)
try:
    os.fchown(fd, os.geteuid(), os.getegid())
    os.fchmod(fd, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or not 2 <= info.st_size <= 65536:
        raise SystemExit(1)
    raw = os.read(fd, 65537)
    if len(raw) != info.st_size or len(raw) > 65536:
        raise SystemExit(1)
    os.fsync(fd)
finally:
    os.close(fd)
document = {
    "config_sha256": hashlib.sha256(raw).hexdigest(),
    "owner": "emmanuelnavaromero02-commits",
    "private_packages": ["banxico", "inegi", "sec_edgar"],
    "registry": "ghcr.io",
    "schema_version": 1,
    "secret_version_resource": secret_version_resource,
}
payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
fd = os.open(context_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400)
try:
    os.fchown(fd, os.geteuid(), os.getegid())
    os.fchmod(fd, 0o400)
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise OSError
        offset += written
    os.fsync(fd)
finally:
    os.close(fd)
parent_fd = os.open(
    os.path.dirname(context_path),
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0),
)
try:
    os.fsync(parent_fd)
finally:
    os.close(parent_fd)
PY
unset resolved_secret_version_resource
unset ghcr_token ghcr_username
export OMEGA_GHCR_AUTH_ACTIVE=1
export OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED=1

child_environment=(
  "PATH=/usr/sbin:/usr/bin:/sbin:/bin"
  "LANG=C.UTF-8"
  "PYTHONSAFEPATH=1"
  "PYTHONNOUSERSITE=1"
  "DOCKER_HOST=$DOCKER_HOST"
)
for environment_name in \
  OMEGA_GCP_IMAGE_AUTHORITY_MODE \
  OMEGA_RELEASE_CANDIDATE_MANIFEST_SHA256 \
  OMEGA_RELEASE_CANDIDATE_RUN_ID \
  OMEGA_RELEASE_CANDIDATE_RUN_ATTEMPT \
  OMEGA_RELEASE_TAG_MANIFEST_SHA256 \
  OMEGA_RELEASE_TAG_OBJECT_SHA \
  OMEGA_RELEASE_ANNOTATED_TAG_OBJECT_FILE \
  OMEGA_GCP_ROLLBACK_RUNTIME_IMAGES \
  OMEGA_GCP_LEGACY_TAG_COMMIT; do
  if [[ -n "${!environment_name-}" ]]; then
    child_environment+=("${environment_name}=${!environment_name}")
  fi
done
command_status=0
session_command=(
  "$PYTHON_BIN" -I "$session_helper" auth-exec
  --parent-fd "$AUTH_PARENT_FD"
  --root-fd "$AUTH_ROOT_FD"
  --lock-fd "$AUTH_LOCK_FD"
  --auth-root-name "${auth_root##*/}"
  --config-directory "$docker_config_name"
  --expected-directory-identity "$docker_config_identity"
  --runner-script "$runner_script"
  --preflight "$preflight_script"
  --owner "$owner"
  --image-tag "$image_tag"
  --source-sha "$source_sha"
  --version "$version"
  --lock-file "$lock_file"
)
for environment_value in "${child_environment[@]}"; do
  session_command+=(--environment "$environment_value")
done
"${session_command[@]}" || command_status=$?
exit "$command_status"
