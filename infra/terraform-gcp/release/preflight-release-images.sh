#!/bin/bash -p
set -Eeuo pipefail
set +x
umask 077

if [[ "$-" != *p* ]]; then
  echo "ERROR: image preflight requires privileged Bash mode." >&2
  exit 2
fi
readonly PYTHON_BIN=/usr/bin/python3
readonly DOCKER_BIN=/usr/bin/docker
readonly DF_BIN=/usr/bin/df
readonly AWK_BIN=/usr/bin/awk
readonly SED_BIN=/usr/bin/sed
readonly MKTEMP_BIN=/usr/bin/mktemp
readonly CHMOD_BIN=/usr/bin/chmod
readonly RM_BIN=/usr/bin/rm
readonly DIRNAME_BIN=/usr/bin/dirname
readonly ID_BIN=/usr/bin/id

if ! "$PYTHON_BIN" -I - <<'PY'
import os

if any(name.startswith("BASH_FUNC_") for name in os.environ):
    raise SystemExit(1)
PY
then
  echo "ERROR: exported shell functions are forbidden in the preflight boundary." >&2
  exit 2
fi
unset BASH_ENV ENV CDPATH
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE
unset SSL_CERT_FILE SSL_CERT_DIR SSLKEYLOGFILE PYTHONHTTPSVERIFY
unset CURL_CA_BUNDLE CURL_HOME OPENSSL_CONF CURL_SSL_BACKEND
unset LD_PRELOAD LD_LIBRARY_PATH DYLD_LIBRARY_PATH DYLD_INSERT_LIBRARIES
unset HOME TMPDIR TMP TEMP POSIXLY_CORRECT BLOCK_SIZE TIME_STYLE
unset BASH_XTRACEFD PS4 HISTFILE INPUTRC
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY NO_PROXY
unset all_proxy http_proxy https_proxy no_proxy
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1
while IFS= read -r environment_name; do
  case "$environment_name" in
    DOCKER_HOST|DOCKER_CONFIG) ;;
    DOCKER_*|COMPOSE_*|GIT_*) unset "$environment_name" ;;
  esac
done < <(compgen -e)

if [[ "$#" -ne 5 ]]; then
  echo "Usage: preflight-release-images.sh <ghcr-owner> <immutable-tag> <exact-revision> <exact-version> <absolute-lock-file>" >&2
  exit 2
fi
if [[ "${EUID:-$("$ID_BIN" -u)}" -ne 0 ]]; then
  echo "ERROR: image preflight must run as root on the canonical host." >&2
  exit 2
fi
if [[ "${OMEGA_GHCR_BUNDLE_BOUND:-0}" != "1" || \
      "${OMEGA_GHCR_BUNDLE_DIRECTORY_FD:-}" != "10" || \
      "${OMEGA_GHCR_BUNDLE_RUNNER_FD:-}" != "11" || \
      "${OMEGA_GHCR_BUNDLE_HELPER_FD:-}" != "12" || \
      "${OMEGA_GHCR_BUNDLE_PREFLIGHT_FD:-}" != "13" || \
      "${OMEGA_GHCR_BUNDLE_AUTHORITY_VALIDATOR_FD:-}" != "14" || \
      "${OMEGA_GHCR_BUNDLE_LOCK_VALIDATOR_FD:-}" != "15" || \
      "${OMEGA_GHCR_BUNDLE_PUBLISHER_FD:-}" != "16" || \
      "${OMEGA_GHCR_BUNDLE_RELEASE_IMAGES_FD:-}" != "17" || \
      "${OMEGA_GHCR_BUNDLE_MANIFEST_FD:-}" != "18" || \
      "${BASH_SOURCE[0]}" != "/proc/self/fd/13" ]]; then
  echo "ERROR: preflight did not inherit the sealed helper bundle." >&2
  exit 2
fi
session_helper=/proc/self/fd/12
authority_validator=/proc/self/fd/14
lock_path_validator=/proc/self/fd/15
publisher=/proc/self/fd/16
release_helper=/proc/self/fd/17

# BEGIN_CANONICAL_GHCR_LOCK_AND_CONTEXT
auth_parent_fd="${OMEGA_GHCR_AUTH_PARENT_FD:-}"
auth_root_fd="${OMEGA_GHCR_AUTH_ROOT_FD:-}"
auth_root_name="${OMEGA_GHCR_AUTH_ROOT_NAME:-}"
auth_context_fd="${OMEGA_GHCR_AUTH_CONTEXT_FD:-}"
auth_config_fd="${OMEGA_GHCR_AUTH_CONFIG_FD:-}"
auth_directory_fd="${OMEGA_GHCR_AUTH_DIRECTORY_FD:-}"
auth_lock_fd="${OMEGA_GHCR_AUTH_LOCK_FD:-}"
auth_directory_name="${OMEGA_GHCR_AUTH_DIRECTORY_NAME:-}"
if [[ "$auth_parent_fd:$auth_root_fd:$auth_context_fd:$auth_config_fd:$auth_directory_fd:$auth_lock_fd" != "4:5:6:7:8:9" || \
      "$auth_root_name" != "omega-gcp-ghcr-auth" || \
      ! "$auth_directory_name" =~ ^omega-gcp-ghcr-auth\.[A-Za-z0-9]{6}$ || \
      "${DOCKER_CONFIG:-}" != "/proc/self/fd/8" ]] || \
   ! "$PYTHON_BIN" -I - 4 5 6 7 8 9 "$auth_root_name" \
       "$auth_directory_name" <<'PY'
import fcntl
import hashlib
import json
import os
import re
import stat
import sys

parent_fd, root_fd, context_fd, config_fd, directory_fd, lock_fd = (
    int(value) for value in sys.argv[1:7]
)
root_name, directory_name = sys.argv[7:]
lock_name = ".omega-gcp-ghcr-release.lock"

def exact_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value

def same(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)

def read_exact(descriptor, mode, maximum):
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or stat.S_IMODE(info.st_mode) != mode
        or info.st_nlink != 1
        or not 2 <= info.st_size <= maximum
    ):
        raise ValueError
    raw = os.pread(descriptor, maximum + 1, 0)
    if len(raw) != info.st_size:
        raise ValueError
    return raw

try:
    parent = os.fstat(parent_fd)
    root = os.fstat(root_fd)
    directory = os.fstat(directory_fd)
    lock = os.fstat(lock_fd)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or parent.st_gid != os.getegid()
        or stat.S_IMODE(parent.st_mode) & 0o022
        or not same(
            root,
            os.stat(root_name, dir_fd=parent_fd, follow_symlinks=False),
        )
        or not stat.S_ISDIR(root.st_mode)
        or root.st_uid != os.geteuid()
        or root.st_gid != os.getegid()
        or stat.S_IMODE(root.st_mode) != 0o700
        or not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.geteuid()
        or directory.st_gid != os.getegid()
        or stat.S_IMODE(directory.st_mode) != 0o700
        or not same(
            directory,
            os.stat(directory_name, dir_fd=root_fd, follow_symlinks=False),
        )
        or not stat.S_ISREG(lock.st_mode)
        or lock.st_uid != os.geteuid()
        or lock.st_gid != os.getegid()
        or stat.S_IMODE(lock.st_mode) != 0o600
        or lock.st_nlink != 1
        or not same(
            lock, os.stat(lock_name, dir_fd=parent_fd, follow_symlinks=False)
        )
    ):
        raise ValueError
    config = read_exact(config_fd, 0o600, 65536)
    raw_context = read_exact(context_fd, 0o400, 4096)
    if not same(
        os.fstat(config_fd),
        os.stat("config.json", dir_fd=directory_fd, follow_symlinks=False),
    ) or not same(
        os.fstat(context_fd),
        os.stat("auth-context.json", dir_fd=directory_fd, follow_symlinks=False),
    ):
        raise ValueError
    context = json.loads(raw_context, object_pairs_hook=exact_object)
    secret_version_resource = context.get("secret_version_resource")
    if (
        not isinstance(secret_version_resource, str)
        or re.fullmatch(
            r"projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/secrets/"
            r"omega-staging-ghcr_pull_credentials/versions/[1-9][0-9]*",
            secret_version_resource,
        )
        is None
    ):
        raise ValueError
    if raw_context != (
        json.dumps(context, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode() or context != {
        "config_sha256": hashlib.sha256(config).hexdigest(),
        "owner": "emmanuelnavaromero02-commits",
        "private_packages": ["banxico", "inegi", "sec_edgar"],
        "registry": "ghcr.io",
        "schema_version": 1,
        "secret_version_resource": secret_version_resource,
    }:
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
except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
    raise SystemExit(1)
PY
then
  echo "ERROR: inherited GHCR lock/auth descriptors are invalid or unbound." >&2
  exit 3
fi
# END_CANONICAL_GHCR_LOCK_AND_CONTEXT

if [[ "${OMEGA_GHCR_AUTH_ACTIVE:-0}" != "1" || \
      "${OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED:-0}" != "1" ]]; then
  echo "ERROR: run this preflight through ghcr-auth-run.sh." >&2
  exit 3
fi
context_file="${DOCKER_CONFIG}/auth-context.json"
config_file="${DOCKER_CONFIG}/config.json"

without_auth_fds() (
  unset DOCKER_CONFIG OMEGA_GHCR_AUTH_ACTIVE OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED
  unset OMEGA_GHCR_AUTH_PARENT_FD OMEGA_GHCR_AUTH_ROOT_FD
  unset OMEGA_GHCR_AUTH_ROOT_NAME OMEGA_GHCR_AUTH_CONTEXT_FD
  unset OMEGA_GHCR_AUTH_CONFIG_FD OMEGA_GHCR_AUTH_DIRECTORY_FD
  unset OMEGA_GHCR_AUTH_DIRECTORY_NAME OMEGA_GHCR_AUTH_LOCK_FD
  exec 4>&- 5>&- 6>&- 7>&- 8>&- 9>&-
  exec "$@"
)
with_docker_config_fd() (
  unset OMEGA_GHCR_AUTH_ACTIVE OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED
  unset OMEGA_GHCR_AUTH_PARENT_FD OMEGA_GHCR_AUTH_ROOT_FD
  unset OMEGA_GHCR_AUTH_ROOT_NAME OMEGA_GHCR_AUTH_CONTEXT_FD
  unset OMEGA_GHCR_AUTH_CONFIG_FD OMEGA_GHCR_AUTH_DIRECTORY_FD
  unset OMEGA_GHCR_AUTH_DIRECTORY_NAME OMEGA_GHCR_AUTH_LOCK_FD
  exec 4>&- 5>&- 6>&- 7>&- 9>&-
  exec "$@"
)
with_auth_verifier_fds() (
  exec "$@"
)
bounded_docker() (
  local operation="$1"
  shift
  exec 4>&- 5>&- 6>&- 7>&- 9>&-
  exec "$PYTHON_BIN" -I "$session_helper" docker-exec \
    --docker "$DOCKER_BIN" --operation "$operation" "$@"
)

owner="$1"
image_tag="$2"
target_revision="$3"
target_version="$4"
lock_file="$5"
authority_file="${lock_file}.authority.json"
commit_file="${lock_file}.commit.json"
authority_mode="${OMEGA_GCP_IMAGE_AUTHORITY_MODE:-}"
canonical_source="https://github.com/emmanuelnavaromero02-commits/CONSOLA-V1"
if [[ "$owner" != "emmanuelnavaromero02-commits" ]]; then
  echo "ERROR: GHCR owner differs from the canonical private namespace." >&2
  exit 4
fi
if [[ ! "$target_revision" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: image revision must be one exact lowercase commit SHA." >&2
  exit 5
fi
if [[ ! "$target_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]]; then
  echo "ERROR: expected image version is invalid." >&2
  exit 5
fi
if [[ "$image_tag" != "candidate-${target_revision}" && "$image_tag" != "v${target_version}" ]]; then
  echo "ERROR: image tag must be the exact candidate SHA or immutable release version." >&2
  exit 5
fi
if [[ "${OMEGA_GHCR_PRIVATE_PACKAGES_VERIFIED:-0}" != "1" ]]; then
  echo "ERROR: private GHCR package metadata was not verified." >&2
  exit 3
fi
if ! without_auth_fds "$PYTHON_BIN" -I "$lock_path_validator" --lock-file "$lock_file" \
     --target-revision "$target_revision" --authority-mode "$authority_mode" \
     --image-tag "$image_tag" --version "$target_version"; then
  echo "ERROR: lock output is outside the canonical root-owned release hierarchy." >&2
  exit 6
fi
lock_dir="$(without_auth_fds "$DIRNAME_BIN" -- "$lock_file")"
if [[ "$image_tag" == "candidate-${target_revision}" ]]; then
  [[ "$authority_mode" == "candidate" ]] || {
    echo "ERROR: candidate pulls require workflow-bound sealed authority." >&2
    exit 5
  }
elif [[ "$authority_mode" != "published" && "$authority_mode" != "legacy-rollback" ]]; then
  echo "ERROR: published pulls require tag-bound or backup-bound authority." >&2
  exit 5
fi
if [[ "$authority_mode" == "published" ]]; then
  expected_tag_proof="/opt/modecissions/shared/release-authority/${target_revision}/annotated-tag.object"
  if [[ "${OMEGA_RELEASE_ANNOTATED_TAG_OBJECT_FILE:-}" != "$expected_tag_proof" ]]; then
    echo "ERROR: published pulls require the exact server-owned annotated-tag object path." >&2
    exit 5
  fi
elif [[ -n "${OMEGA_RELEASE_ANNOTATED_TAG_OBJECT_FILE:-}" ]]; then
  echo "ERROR: annotated-tag authority is forbidden outside published pulls." >&2
  exit 5
fi

docker_root="/var/lib/docker"
if [[ "${DOCKER_HOST:-}" != "unix:///run/docker.sock" ]]; then
  echo "ERROR: Docker client is not pinned to the canonical local socket." >&2
  exit 6
fi
docker_identity() {
  bounded_docker info
}
if ! docker_identity_before="$(docker_identity)"; then
  echo "ERROR: Docker daemon identity or storage root differs from the canonical host." >&2
  exit 6
fi
docker_root_before="${docker_identity_before%%|*}"
docker_daemon_id_before="${docker_identity_before#*|}"
if [[ "$docker_root_before" != "$docker_root" || \
      "$docker_daemon_id_before" == "$docker_identity_before" || \
      ! "$docker_daemon_id_before" =~ ^[A-Za-z0-9:._-]+$ || \
      "${#docker_daemon_id_before}" -gt 512 ]]; then
  echo "ERROR: Docker daemon identity or storage root differs from the canonical host." >&2
  exit 6
fi
if [[ ! -d "$docker_root" || -L "$docker_root" ]]; then
  echo "ERROR: canonical Docker storage root is unavailable." >&2
  exit 6
fi
disk_available() {
  without_auth_fds "$DF_BIN" --output=avail -B1 "$docker_root" | \
    without_auth_fds "$AWK_BIN" 'NR == 2 && $1 ~ /^[0-9]+$/ {print $1}'
}
initial_free="$(disk_available)"
minimum_initial_free=$((30 * 1024 * 1024 * 1024))
minimum_reserve=$((10 * 1024 * 1024 * 1024))
if [[ ! "$initial_free" =~ ^[0-9]+$ || "$initial_free" -lt "$minimum_initial_free" ]]; then
  echo "ERROR: Docker storage headroom is below the 30 GiB pre-pull gate." >&2
  exit 6
fi

image_names=(
  airflow
  banxico
  console
  hubspot
  inegi
  mcp-infra
  refinement
  replicon
  salesforce
  sap_hcm
  sap_s4hana
  sap_successfactors
  sec_edgar
  vault
  workspace
)
lock_names=(
  OMEGA_GCP_IMAGE_AIRFLOW
  OMEGA_GCP_IMAGE_BANXICO
  OMEGA_GCP_IMAGE_CONSOLE
  OMEGA_GCP_IMAGE_HUBSPOT
  OMEGA_GCP_IMAGE_INEGI
  OMEGA_GCP_IMAGE_MCP_INFRA
  OMEGA_GCP_IMAGE_REFINEMENT
  OMEGA_GCP_IMAGE_REPLICON
  OMEGA_GCP_IMAGE_SALESFORCE
  OMEGA_GCP_IMAGE_SAP_HCM
  OMEGA_GCP_IMAGE_SAP_S4HANA
  OMEGA_GCP_IMAGE_SAP_SUCCESSFACTORS
  OMEGA_GCP_IMAGE_SEC_EDGAR
  OMEGA_GCP_IMAGE_VAULT
  OMEGA_GCP_IMAGE_WORKSPACE
)
if [[ "${#image_names[@]}" -ne 15 || "${#lock_names[@]}" -ne 15 ]]; then
  echo "ERROR: release image inventory must contain exactly 15 images." >&2
  exit 7
fi

# Volatile construction files stay in the already validated /run credential
# directory. A SIGKILL therefore cannot leave unpublished temp names in the
# persistent release staging directory; only the three recoverable outputs
# below may survive there.
temp_lock="$(with_docker_config_fd "$MKTEMP_BIN" "${DOCKER_CONFIG%/}/omega-gcp-image-lock.XXXXXX")"
temp_authority="$(with_docker_config_fd "$MKTEMP_BIN" "${DOCKER_CONFIG%/}/omega-gcp-image-authority.XXXXXX")"
expected_digests="$(with_docker_config_fd "$MKTEMP_BIN" "${DOCKER_CONFIG%/}/omega-gcp-image-digests.XXXXXX")"
with_docker_config_fd "$CHMOD_BIN" 600 "$temp_lock" "$temp_authority" "$expected_digests"
cleanup() {
  local status=$?
  trap - EXIT
  [[ -z "$temp_lock" ]] || with_docker_config_fd "$RM_BIN" -f -- "$temp_lock"
  [[ -z "$temp_authority" ]] || with_docker_config_fd "$RM_BIN" -f -- "$temp_authority"
  [[ -z "$expected_digests" ]] || with_docker_config_fd "$RM_BIN" -f -- "$expected_digests"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

authority_args=(
  verify-bound-sealed-lock
  --authority-mode "$authority_mode"
  --source-sha "$target_revision"
  --release-tag "v${target_version}"
  --image-tag "$image_tag"
  --docker-auth-file "${DOCKER_CONFIG}/config.json"
  --output "$temp_authority"
)
case "$authority_mode" in
  candidate)
    authority_args+=(
      --bound-manifest-digest "${OMEGA_RELEASE_CANDIDATE_MANIFEST_SHA256:-}"
      --github-run-id "${OMEGA_RELEASE_CANDIDATE_RUN_ID:-}"
      --github-run-attempt "${OMEGA_RELEASE_CANDIDATE_RUN_ATTEMPT:-}"
    )
    ;;
  published)
    authority_args+=(
      --bound-manifest-digest "${OMEGA_RELEASE_TAG_MANIFEST_SHA256:-}"
      --tag-object-sha "${OMEGA_RELEASE_TAG_OBJECT_SHA:-}"
      --annotated-tag-object "${OMEGA_RELEASE_ANNOTATED_TAG_OBJECT_FILE:-}"
    )
    ;;
  legacy-rollback)
    authority_args+=(
      --runtime-images "${OMEGA_GCP_ROLLBACK_RUNTIME_IMAGES:-}"
      --legacy-tag-commit "${OMEGA_GCP_LEGACY_TAG_COMMIT:-}"
    )
    ;;
esac
if ! with_auth_verifier_fds "$PYTHON_BIN" -I "$release_helper" "${authority_args[@]}" >/dev/null; then
  echo "ERROR: immutable release image authority could not be verified." >&2
  exit 7
fi

with_docker_config_fd "$PYTHON_BIN" -I "$authority_validator" --authority "$temp_authority" \
  --digests "$expected_digests" --mode "$authority_mode" \
  --source-sha "$target_revision" --version "$target_version" \
  --image-tag "$image_tag"

lock_payload='# Generated by preflight-release-images.sh; contains image references only.'$'\n'
for index in "${!image_names[@]}"; do
  image_name="${image_names[$index]}"
  lock_name="${lock_names[$index]}"
  repository="ghcr.io/${owner}/${image_name}"
  expected_digest="$(with_docker_config_fd "$AWK_BIN" -F '\t' -v service="$image_name" '$1 == service {print $2}' "$expected_digests")"
  expected_image_id="$(with_docker_config_fd "$AWK_BIN" -F '\t' -v service="$image_name" '$1 == service {print $3}' "$expected_digests")"
  if [[ ! "$expected_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "ERROR: sealed authority lacks an exact digest for ${image_name}." >&2
    exit 8
  fi
  reference="${repository}@${expected_digest}"
  if ! bounded_docker pull --reference "$reference" >/dev/null 2>&1; then
    echo "ERROR: release image pull failed for ${image_name}; inventory is less than 15/15." >&2
    exit 8
  fi
  if ! repo_digests="$(bounded_docker inspect-repo-digests --reference "$reference" 2>/dev/null)"; then
    echo "ERROR: pulled image has no inspectable digest for ${image_name}." >&2
    exit 9
  fi
  oci_identity="$(bounded_docker inspect-oci-identity --reference "$reference" 2>/dev/null || true)"
  revision="$(without_auth_fds "$SED_BIN" -n '1p' <<<"$oci_identity")"
  version="$(without_auth_fds "$SED_BIN" -n '2p' <<<"$oci_identity")"
  source="$(without_auth_fds "$SED_BIN" -n '3p' <<<"$oci_identity")"
  # Labels are defense-in-depth for newly sealed images. Historical rollback
  # images predate these labels; their RepoDigest+ImageID backup pair is the
  # sole authority and null labels must not invalidate that restore point.
  if [[ "$authority_mode" != "legacy-rollback" && \
        ( "$revision" != "$target_revision" || "$version" != "$target_version" || \
          "$source" != "$canonical_source" ) ]]; then
    echo "ERROR: secondary OCI source/version/revision identity differs for ${image_name}." >&2
    exit 9
  fi
  if ! digest="$(
    printf '%s' "$repo_digests" | without_auth_fds "$PYTHON_BIN" -I -c '
import json
import re
import sys

repository = sys.argv[1]
try:
    values = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(1)
matches = []
for value in values if isinstance(values, list) else []:
    if not isinstance(value, str) or "@" not in value:
        continue
    name, digest = value.rsplit("@", 1)
    if name == repository and re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        matches.append(digest)
if len(set(matches)) != 1:
    raise SystemExit(1)
sys.stdout.write(matches[0])
' "$repository"
  )"; then
    echo "ERROR: pulled image digest is missing or ambiguous for ${image_name}." >&2
    exit 9
  fi
  if [[ "$digest" != "$expected_digest" ]]; then
    echo "ERROR: pulled digest differs from immutable authority for ${image_name}." >&2
    exit 9
  fi
  if [[ "$authority_mode" == "legacy-rollback" ]]; then
    actual_image_id="$(bounded_docker inspect-image-id --reference "$reference" 2>/dev/null || true)"
    if [[ "$actual_image_id" != "$expected_image_id" ]]; then
      echo "ERROR: pulled ImageID differs from rollback backup for ${image_name}." >&2
      exit 9
    fi
  fi
  printf -v lock_line '%s=%s:%s@%s\n' "$lock_name" "$repository" "$image_tag" "$digest"
  lock_payload+="$lock_line"
  printf 'GCP_RELEASE_IMAGE\t%s\tPASS\t%s@%s\n' "$image_name" "$image_tag" "$digest"
done

printf '%s' "$lock_payload" | with_docker_config_fd "$PYTHON_BIN" -I -c '
import os
import stat
import sys

path = sys.argv[1]
payload = sys.stdin.buffer.read(131073)
if not payload or len(payload) > 131072:
    raise SystemExit(1)
descriptor = os.open(
    path,
    os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
)
try:
    info = os.fstat(descriptor)
    named = os.lstat(path)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise ValueError
    os.ftruncate(descriptor, 0)
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError
        offset += written
    os.fsync(descriptor)
finally:
    os.close(descriptor)
' "$temp_lock"
unset lock_payload lock_line

if ! docker_identity_after="$(docker_identity)" || \
   [[ "$docker_identity_after" != "$docker_identity_before" ]]; then
  echo "ERROR: Docker daemon identity or storage root changed during authenticated pulls." >&2
  exit 9
fi

final_free="$(disk_available)"
if [[ ! "$final_free" =~ ^[0-9]+$ || "$final_free" -lt "$minimum_reserve" ]]; then
  echo "ERROR: Docker storage reserve fell below 10 GiB after authenticated pulls." >&2
  exit 9
fi

with_docker_config_fd "$CHMOD_BIN" 600 "$temp_lock"
with_docker_config_fd "$CHMOD_BIN" 600 "$temp_authority"
if ! with_docker_config_fd "$PYTHON_BIN" -I "$publisher" \
    --source-lock "$temp_lock" \
    --source-authority "$temp_authority" \
    --destination-lock "$lock_file" \
    --source-sha "$target_revision" \
    --version "$target_version" \
    --image-tag "$image_tag" \
    --authority-mode "$authority_mode"; then
  echo "ERROR: durable recoverable image-lock publication failed." >&2
  exit 9
fi
temp_lock=""
temp_authority=""
printf 'GCP_RELEASE_IMAGES\tPASS\t15/15\tprivate=3/3\ttag=%s\tlock=%s\n' "$image_tag" "$lock_file"
