#!/usr/bin/env bash
# Prove authenticated 15/15 pullability for one immutable release/rollback tag.
set -Eeuo pipefail
set +x
umask 077

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: image-preflight.sh must run through sudo" >&2
  exit 10
fi
if [[ "$#" -ne 13 ]]; then
  echo "ERROR: image-preflight.sh requires one exact 13-field contract" >&2
  exit 10
fi

HELPER_REF="${1:-}"
HELPER_ARTIFACT_URI="${2:-}"
HELPER_ARTIFACT_GENERATION="${3:-}"
HELPER_ARTIFACT_SIZE_BYTES="${4:-}"
HELPER_ARTIFACT_SHA256="${5:-}"
TARGET_TAG="${6:-}"
TARGET_REF="${7:-}"
TARGET_VERSION="${8:-}"
TARGET_KIND="${9:-}"
GHCR_OWNER="${10:-}"
GCP_ENVIRONMENT="${11:-}"
GHCR_SECRET_VERSION="${12:-}"
PURPOSE="${13:-}"
APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
SHARED_ROOT="${APP_ROOT}/shared"
WORKDIR=""
PREFLIGHT_TMP=""
SAFE_IO="${OMEGA_GCP_SAFE_IO:-}"

emit() {
  local name="$1" status="$2" evidence="${3:-}"
  evidence="${evidence//$'\t'/ }"
  evidence="${evidence//$'\r'/ }"
  evidence="${evidence//$'\n'/ }"
  printf 'OMEGA_GCP_IMAGE_PREFLIGHT_CHECK\t%s\t%s\t%s\n' "$name" "$status" "$evidence"
}

fail() {
  emit "$1" "FAIL" "${2:-}"
  exit "${3:-20}"
}

if [[ ! "$HELPER_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$HELPER_ARTIFACT_GENERATION" =~ ^[1-9][0-9]*$ || \
      ! "$HELPER_ARTIFACT_SIZE_BYTES" =~ ^[1-9][0-9]*$ || \
      ! "$HELPER_ARTIFACT_SHA256" =~ ^[0-9a-f]{64}$ || \
      ! "$HELPER_ARTIFACT_URI" =~ ^gs://[^/]+/deploy-artifacts/${HELPER_REF}/repo\.tar\.gz$ ]]; then
  fail "helper artifact" "exact candidate helper ref/URI/checksum required"
fi
if [[ "$SAFE_IO" != /* || ! -x "$SAFE_IO" ]]; then
  fail "safe I/O helper" "controller-owned helper is unavailable"
fi
if [[ ! "$TARGET_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$TARGET_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]]; then
  fail "target identity" "one exact image ref/version is required"
fi
if [[ "$TARGET_KIND" == "candidate" ]]; then
  if [[ "$TARGET_TAG" != "candidate-${TARGET_REF}" || "$PURPOSE" != "release" || \
        "$TARGET_REF" != "$HELPER_REF" ]]; then
    fail "target identity" "candidate preflight is not bound to exact current helper/main"
  fi
elif [[ "$TARGET_KIND" == "published" ]]; then
  if [[ "$TARGET_TAG" != "v${TARGET_VERSION}" ]]; then
    fail "target identity" "published preflight tag/version identity differs"
  fi
else
  fail "target identity" "target kind must be candidate or published"
fi
if [[ "$GHCR_OWNER" != "emmanuelnavaromero02-commits" || \
      ! "$GCP_ENVIRONMENT" =~ ^[a-z][a-z0-9-]*$ || \
      ! "$GHCR_SECRET_VERSION" =~ ^[1-9][0-9]*$ ]]; then
  fail "server-owned GHCR identity" "canonical owner/environment/secret version invalid"
fi
if [[ "$PURPOSE" != "release" && "$PURPOSE" != "rollback" ]]; then
  fail "preflight purpose" "expected release or rollback"
fi

exec 9>"/var/lock/omega-gcp-day2.lock"
if ! flock -n 9; then
  fail "exclusive day-2 lock" "another canonical operation is active"
fi
WORKDIR="$(mktemp -d /tmp/omega-gcp-image-preflight.XXXXXX)"
cleanup() {
  local rc=$?
  trap - EXIT
  if [[ -n "$WORKDIR" && "$WORKDIR" == /tmp/omega-gcp-image-preflight.* ]]; then
    rm -rf -- "$WORKDIR"
  fi
  if [[ -n "$PREFLIGHT_TMP" && "$PREFLIGHT_TMP" == "${SHARED_ROOT}/image-preflights/."* ]]; then
    rm -rf -- "$PREFLIGHT_TMP"
  fi
  exit "$rc"
}
trap cleanup EXIT

"$SAFE_IO" gcs-download --uri "$HELPER_ARTIFACT_URI" \
  --generation "$HELPER_ARTIFACT_GENERATION" --size "$HELPER_ARTIFACT_SIZE_BYTES" \
  --sha256 "$HELPER_ARTIFACT_SHA256" --output "${WORKDIR}/candidate.tar.gz"
install -d -m 0700 "${WORKDIR}/candidate"
"$SAFE_IO" safe-extract --archive "${WORKDIR}/candidate.tar.gz" \
  --destination "${WORKDIR}/candidate"
AUTH_RUNNER="${WORKDIR}/candidate/infra/terraform-gcp/release/ghcr-auth-run.sh"
PREFLIGHT="${WORKDIR}/candidate/infra/terraform-gcp/release/preflight-release-images.sh"
METADATA_FIREWALL="${WORKDIR}/candidate/scripts/gcp/metadata-firewall.sh"
if [[ ! -x "$AUTH_RUNNER" || ! -x "$PREFLIGHT" || ! -x "$METADATA_FIREWALL" ]]; then
  fail "helper artifact" "audited GHCR helpers are missing from exact candidate"
fi
emit "helper artifact" "PASS" "ref=${HELPER_REF} sha256=${HELPER_ARTIFACT_SHA256}"
"$METADATA_FIREWALL" install-and-verify-container >/dev/null
emit "container metadata isolation" "PASS" "IPv4/IPv6 metadata endpoints denied from running proprietary container"

LOCK_FILE="${WORKDIR}/release-images.env"
if ! OMEGA_GCP_ENVIRONMENT="$GCP_ENVIRONMENT" \
    OMEGA_GHCR_PULL_SECRET_VERSION="$GHCR_SECRET_VERSION" \
    "$AUTH_RUNNER" "$PREFLIGHT" "$GHCR_OWNER" "$TARGET_TAG" "$TARGET_REF" \
      "$TARGET_VERSION" "$LOCK_FILE" \
    >"${WORKDIR}/preflight.out" 2>"${WORKDIR}/preflight.err"; then
  fail "authenticated image pull" "less than 15/15; credential output suppressed"
fi

LOCK_SHA256="$(sha256sum "$LOCK_FILE" | awk '{print $1}')"
python3 - "$LOCK_FILE" "${WORKDIR}/manifest.json" "$HELPER_REF" \
  "$HELPER_ARTIFACT_URI" "$HELPER_ARTIFACT_GENERATION" \
  "$HELPER_ARTIFACT_SIZE_BYTES" "$HELPER_ARTIFACT_SHA256" \
  "$TARGET_TAG" "$TARGET_REF" "$TARGET_VERSION" "$TARGET_KIND" "$PURPOSE" \
  "$GHCR_OWNER" "$LOCK_SHA256" <<'PY'
import json
import pathlib
import re
import sys

lock_path = pathlib.Path(sys.argv[1])
assignments = {}
for raw in lock_path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    key, value = line.split("=", 1)
    if key in assignments:
        raise SystemExit("duplicate lock key")
    assignments[key] = value
if len(assignments) != 15:
    raise SystemExit("lock is not 15/15")
tag, owner = sys.argv[8], sys.argv[13]
for value in assignments.values():
    if re.fullmatch(
        rf"ghcr\.io/{re.escape(owner)}/[a-z0-9_-]+:{re.escape(tag)}@sha256:[0-9a-f]{{64}}",
        value,
    ) is None:
        raise SystemExit("lock contains an invalid immutable reference")
payload = {
    "schema_version": 1,
    "purpose": sys.argv[12],
    "target_kind": sys.argv[11],
    "target_tag": tag,
    "target_ref": sys.argv[9],
    "target_version": sys.argv[10],
    "helper_ref": sys.argv[3],
    "helper_artifact_uri": sys.argv[4],
    "helper_artifact_generation": sys.argv[5],
    "helper_artifact_size_bytes": int(sys.argv[6]),
    "helper_artifact_sha256": sys.argv[7],
    "ghcr_owner": owner,
    "image_count": len(assignments),
    "lock_sha256": sys.argv[14],
    "secrets_included": False,
}
pathlib.Path(sys.argv[2]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

PREFLIGHT_ROOT="${SHARED_ROOT}/image-preflights/${PURPOSE}-${TARGET_KIND}-${TARGET_TAG}-${TARGET_REF}-by-${HELPER_REF}"
PREFLIGHT_TMP="${SHARED_ROOT}/image-preflights/.${PURPOSE}-${TARGET_KIND}-${TARGET_TAG}-${TARGET_REF}-by-${HELPER_REF}.$$"
install -d -m 0700 "${SHARED_ROOT}/image-preflights"
install -d -m 0700 "$PREFLIGHT_TMP"
install -m 0400 "$LOCK_FILE" "${PREFLIGHT_TMP}/release-images.env"
install -m 0400 "${WORKDIR}/manifest.json" "${PREFLIGHT_TMP}/manifest.json"
"$SAFE_IO" fsync-file "${PREFLIGHT_TMP}/release-images.env" \
  "${PREFLIGHT_TMP}/manifest.json"
"$SAFE_IO" fsync-dir "$PREFLIGHT_TMP"
if [[ -e "$PREFLIGHT_ROOT" || -L "$PREFLIGHT_ROOT" ]]; then
  if [[ -L "$PREFLIGHT_ROOT" || ! -d "$PREFLIGHT_ROOT" ]]; then
    fail "immutable preflight evidence" "existing evidence path is not a regular directory"
  fi
  cmp "${PREFLIGHT_ROOT}/release-images.env" "${PREFLIGHT_TMP}/release-images.env" >/dev/null || \
    fail "immutable preflight evidence" "existing lock differs from fresh 15/15 pulls"
  cmp "${PREFLIGHT_ROOT}/manifest.json" "${PREFLIGHT_TMP}/manifest.json" >/dev/null || \
    fail "immutable preflight evidence" "existing manifest differs from target identity"
  rm -rf -- "$PREFLIGHT_TMP"
  PREFLIGHT_TMP=""
else
  mv "$PREFLIGHT_TMP" "$PREFLIGHT_ROOT"
  "$SAFE_IO" fsync-dir "${SHARED_ROOT}/image-preflights"
  PREFLIGHT_TMP=""
fi
emit "authenticated image pull" "PASS" "purpose=${PURPOSE} tag=${TARGET_TAG} images=15/15 lock_sha256=${LOCK_SHA256}"
printf 'OMEGA_GCP_IMAGE_PREFLIGHT_JSON={"status":"PASS","purpose":"%s","target_tag":"%s","target_ref":"%s","image_count":15,"lock_sha256":"%s"}\n' \
  "$PURPOSE" "$TARGET_TAG" "$TARGET_REF" "$LOCK_SHA256"
