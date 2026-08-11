#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

if [[ "$#" -ne 5 ]]; then
  echo "Usage: preflight-release-images.sh <ghcr-owner> <immutable-tag> <exact-revision> <exact-version> <absolute-lock-file>" >&2
  exit 2
fi
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: image preflight must run as root on the canonical host." >&2
  exit 2
fi
if [[ "${OMEGA_GHCR_AUTH_ACTIVE:-0}" != "1" ]]; then
  echo "ERROR: run this preflight through ghcr-auth-run.sh." >&2
  exit 3
fi
if [[ -z "${DOCKER_CONFIG:-}" ]]; then
  echo "ERROR: GHCR Docker authentication context is missing." >&2
  exit 3
fi

# BEGIN_CANONICAL_GHCR_CONTEXT
auth_root="/run/omega-gcp-ghcr-auth"
context_file="${DOCKER_CONFIG}/auth-context.json"
config_file="${DOCKER_CONFIG}/config.json"
if [[ "$(findmnt -n -o FSTYPE --target /run 2>/dev/null || true)" != "tmpfs" || \
      -L /run || -L "$auth_root" || "$(stat -c '%u:%g' /run)" != "0:0" || \
      ! "$DOCKER_CONFIG" =~ ^/run/omega-gcp-ghcr-auth/omega-gcp-ghcr-auth\.[A-Za-z0-9]{6}$ || \
      -L "$DOCKER_CONFIG" || "$(stat -c '%u:%g:%a' "$auth_root")" != "0:0:700" || \
      "$(stat -c '%u:%g:%a' "$DOCKER_CONFIG")" != "0:0:700" ]]; then
  echo "ERROR: GHCR authentication context is outside the canonical root-owned tmpfs." >&2
  exit 3
fi
if ! python3 - "$config_file" "$context_file" <<'PY'
import hashlib
import json
import os
import stat
import sys

config_path, context_path = sys.argv[1:]

def read_private(path, mode, maximum):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1 or not 2 <= info.st_size <= maximum:
            raise ValueError
        raw = os.read(fd, maximum + 1)
        if len(raw) != info.st_size or len(raw) > maximum:
            raise ValueError
        return raw
    finally:
        os.close(fd)

def exact_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value

try:
    config = read_private(config_path, 0o600, 65536)
    raw_context = read_private(context_path, 0o400, 4096)
    context = json.loads(raw_context, object_pairs_hook=exact_object)
    if raw_context != (json.dumps(context, sort_keys=True, separators=(",", ":")) + "\n").encode():
        raise ValueError
    if not isinstance(context, dict) or set(context) != {"config_sha256", "owner", "private_packages", "registry", "schema_version"}:
        raise ValueError
    if context != {
        "config_sha256": hashlib.sha256(config).hexdigest(),
        "owner": "emmanuelnavaromero02-commits",
        "private_packages": ["banxico", "inegi", "sec_edgar"],
        "registry": "ghcr.io",
        "schema_version": 1,
    }:
        raise ValueError
except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
    raise SystemExit(1)
PY
# END_CANONICAL_GHCR_CONTEXT
then
  echo "ERROR: GHCR authentication context proof is invalid." >&2
  exit 3
fi

owner="$1"
image_tag="$2"
target_revision="$3"
target_version="$4"
lock_file="$5"
authority_file="${lock_file}.authority.json"
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
lock_path_validator="$(dirname -- "${BASH_SOURCE[0]}")/validate-lock-output.py"
if [[ ! -f "$lock_path_validator" ]] || \
   ! python3 "$lock_path_validator" --lock-file "$lock_file" \
     --target-revision "$target_revision"; then
  echo "ERROR: lock output is outside the canonical root-owned release hierarchy." >&2
  exit 6
fi
lock_dir="$(dirname -- "$lock_file")"
if [[ "$image_tag" == "candidate-${target_revision}" ]]; then
  [[ "$authority_mode" == "candidate" ]] || {
    echo "ERROR: candidate pulls require workflow-bound sealed authority." >&2
    exit 5
  }
elif [[ "$authority_mode" != "published" && "$authority_mode" != "legacy-rollback" ]]; then
  echo "ERROR: published pulls require tag-bound or backup-bound authority." >&2
  exit 5
fi

docker_root="/var/lib/docker"
if [[ ! -d "$docker_root" || -L "$docker_root" ]]; then
  echo "ERROR: canonical Docker storage root is unavailable." >&2
  exit 6
fi
disk_available() {
  df --output=avail -B1 "$docker_root" | awk 'NR == 2 && $1 ~ /^[0-9]+$/ {print $1}'
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

temp_lock="$(mktemp "${lock_dir%/}/.omega-gcp-image-lock.XXXXXX")"
pull_error="$(mktemp "${lock_dir%/}/.omega-gcp-image-pull.XXXXXX")"
temp_authority="$(mktemp "${lock_dir%/}/.omega-gcp-image-authority.XXXXXX")"
expected_digests="$(mktemp "${lock_dir%/}/.omega-gcp-image-digests.XXXXXX")"
chmod 600 "$temp_lock" "$pull_error" "$temp_authority" "$expected_digests"
cleanup() {
  local status=$?
  trap - EXIT
  [[ -z "$temp_lock" ]] || rm -f -- "$temp_lock"
  [[ -z "$pull_error" ]] || rm -f -- "$pull_error"
  [[ -z "$temp_authority" ]] || rm -f -- "$temp_authority"
  [[ -z "$expected_digests" ]] || rm -f -- "$expected_digests"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

release_helper="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)/scripts/release_images.py"
if [[ ! -f "$release_helper" ]]; then
  echo "ERROR: canonical sealed-manifest verifier is missing." >&2
  exit 7
fi
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
      --controller-attestation-sha256 "${OMEGA_RELEASE_CANDIDATE_CONTROLLER_ATTESTATION_SHA256:-}"
    )
    ;;
  published)
    authority_args+=(
      --bound-manifest-digest "${OMEGA_RELEASE_TAG_MANIFEST_SHA256:-}"
      --tag-object-sha "${OMEGA_RELEASE_TAG_OBJECT_SHA:-}"
    )
    ;;
  legacy-rollback)
    authority_args+=(
      --runtime-images "${OMEGA_GCP_ROLLBACK_RUNTIME_IMAGES:-}"
      --legacy-tag-commit "${OMEGA_GCP_LEGACY_TAG_COMMIT:-}"
    )
    ;;
esac
if ! python3 "$release_helper" "${authority_args[@]}" >/dev/null; then
  echo "ERROR: immutable release image authority could not be verified." >&2
  exit 7
fi

authority_validator="$(dirname -- "${BASH_SOURCE[0]}")/validate-image-authority.py"
python3 "$authority_validator" --authority "$temp_authority" \
  --digests "$expected_digests" --mode "$authority_mode" \
  --source-sha "$target_revision" --version "$target_version" \
  --image-tag "$image_tag"

printf '# Generated by preflight-release-images.sh; contains image references only.\n' > "$temp_lock"
for index in "${!image_names[@]}"; do
  image_name="${image_names[$index]}"
  lock_name="${lock_names[$index]}"
  repository="ghcr.io/${owner}/${image_name}"
  expected_digest="$(awk -F '\t' -v service="$image_name" '$1 == service {print $2}' "$expected_digests")"
  expected_image_id="$(awk -F '\t' -v service="$image_name" '$1 == service {print $3}' "$expected_digests")"
  if [[ ! "$expected_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "ERROR: sealed authority lacks an exact digest for ${image_name}." >&2
    exit 8
  fi
  reference="${repository}@${expected_digest}"
  : > "$pull_error"
  if ! docker pull --quiet "$reference" >/dev/null 2>"$pull_error"; then
    echo "ERROR: release image pull failed for ${image_name}; inventory is less than 15/15." >&2
    exit 8
  fi
  if ! repo_digests="$(docker image inspect --format '{{json .RepoDigests}}' "$reference" 2>/dev/null)"; then
    echo "ERROR: pulled image has no inspectable digest for ${image_name}." >&2
    exit 9
  fi
  oci_identity="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}{{println}}{{index .Config.Labels "org.opencontainers.image.version"}}{{println}}{{index .Config.Labels "org.opencontainers.image.source"}}' "$reference" 2>/dev/null || true)"
  revision="$(sed -n '1p' <<<"$oci_identity")"
  version="$(sed -n '2p' <<<"$oci_identity")"
  source="$(sed -n '3p' <<<"$oci_identity")"
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
    printf '%s' "$repo_digests" | python3 -c '
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
    actual_image_id="$(docker image inspect --format '{{.Id}}' "$reference" 2>/dev/null || true)"
    if [[ "$actual_image_id" != "$expected_image_id" ]]; then
      echo "ERROR: pulled ImageID differs from rollback backup for ${image_name}." >&2
      exit 9
    fi
  fi
  printf '%s=%s:%s@%s\n' "$lock_name" "$repository" "$image_tag" "$digest" >> "$temp_lock"
  printf 'GCP_RELEASE_IMAGE\t%s\tPASS\t%s@%s\n' "$image_name" "$image_tag" "$digest"
done

final_free="$(disk_available)"
if [[ ! "$final_free" =~ ^[0-9]+$ || "$final_free" -lt "$minimum_reserve" ]]; then
  echo "ERROR: Docker storage reserve fell below 10 GiB after authenticated pulls." >&2
  exit 9
fi

chmod 600 "$temp_lock"
chmod 600 "$temp_authority"
if ! python3 - "$temp_lock" "$lock_file" "$temp_authority" "$authority_file" "$lock_dir" <<'PY'
import hashlib
import os
import stat
import sys

temp_lock, lock_file, temp_authority, authority_file, directory = sys.argv[1:]
sources = ((temp_authority, authority_file), (temp_lock, lock_file))
source_info = {}
published = []

def open_regular(path):
    return os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))

try:
    for source, _destination in sources:
        descriptor = open_regular(source)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size <= 0:
                raise ValueError("unsafe temporary image-lock output")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            os.fsync(descriptor)
            source_info[source] = (info.st_dev, info.st_ino, info.st_size, digest.digest())
        finally:
            os.close(descriptor)
    for source, destination in sources:
        os.link(source, destination, follow_symlinks=False)
        published.append((source, destination))
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    for source, _destination in sources:
        os.unlink(source)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    for source, destination in sources:
        descriptor = open_regular(destination)
        try:
            info = os.fstat(descriptor)
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            expected = source_info[source]
            if (info.st_dev, info.st_ino, info.st_size, digest.digest()) != expected or info.st_nlink != 1:
                raise ValueError("published image-lock output differs from fsynced bytes")
        finally:
            os.close(descriptor)
except Exception:
    for source, destination in reversed(published):
        try:
            destination_info = os.lstat(destination)
            expected = source_info[source]
            if (destination_info.st_dev, destination_info.st_ino) == expected[:2]:
                os.unlink(destination)
        except FileNotFoundError:
            pass
    try:
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        raise
PY
then
  echo "ERROR: durable no-replace image-lock publication failed." >&2
  exit 9
fi
temp_lock=""
temp_authority=""
printf 'GCP_RELEASE_IMAGES\tPASS\t15/15\tprivate=3/3\ttag=%s\tlock=%s\n' "$image_tag" "$lock_file"
