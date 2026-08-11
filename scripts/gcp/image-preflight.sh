#!/usr/bin/env bash
# Prove authenticated 15/15 pullability for one immutable release/rollback tag.
set -Eeuo pipefail
set +x
umask 077

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: image-preflight.sh must run through sudo" >&2
  exit 10
fi
if [[ "$#" -ne 15 && "$#" -ne 17 && "$#" -ne 22 ]]; then
  echo "ERROR: image-preflight.sh requires one exact authority-bound contract" >&2
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
CANDIDATE_MANIFEST_SHA256="${14:-}"
CANDIDATE_RUN_ID="${15:-}"
CANDIDATE_RUN_ATTEMPT="${16:-}"
CANDIDATE_CONTROLLER_ATTESTATION_SHA256="${17:-}"
PUBLISHED_MANIFEST_SHA256="${14:-}"
PUBLISHED_TAG_OBJECT_SHA="${15:-}"
ROLLBACK_RUNTIME_IMAGES_URI="${14:-}"
ROLLBACK_RUNTIME_IMAGES_GENERATION="${15:-}"
ROLLBACK_RUNTIME_IMAGES_SIZE_BYTES="${16:-}"
ROLLBACK_RUNTIME_IMAGES_SHA256="${17:-}"
ROLLBACK_BACKUP_MANIFEST_URI="${18:-}"
ROLLBACK_BACKUP_MANIFEST_GENERATION="${19:-}"
ROLLBACK_BACKUP_MANIFEST_SIZE_BYTES="${20:-}"
ROLLBACK_BACKUP_MANIFEST_SHA256="${21:-}"
LEGACY_TAG_COMMIT="${22:-}"
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
if [[ "$TARGET_KIND" == "candidate" ]]; then
  AUTHORITY_MODE="candidate"
  if [[ "$#" -ne 17 || \
        ! "$CANDIDATE_MANIFEST_SHA256" =~ ^sha256:[0-9a-f]{64}$ || \
        ! "$CANDIDATE_RUN_ID" =~ ^[1-9][0-9]*$ || \
        ! "$CANDIDATE_RUN_ATTEMPT" =~ ^[1-9][0-9]*$ || \
        ! "$CANDIDATE_CONTROLLER_ATTESTATION_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    fail "candidate image authority" "controller-attested sealed payload and exact workflow run/head are required"
  fi
elif [[ "$PURPOSE" == "release" ]]; then
  AUTHORITY_MODE="published"
  if [[ "$#" -ne 15 || \
        ! "$PUBLISHED_MANIFEST_SHA256" =~ ^sha256:[0-9a-f]{64}$ || \
        ! "$PUBLISHED_TAG_OBJECT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
    fail "published image authority" "controller-verified annotated tag binding is required"
  fi
else
  AUTHORITY_MODE="legacy-rollback"
  if [[ "$#" -ne 22 || \
        ! "$ROLLBACK_RUNTIME_IMAGES_URI" =~ ^gs://[^/]+/_omega_backups/[^/]+/runtime-images\.json$ || \
        ! "$ROLLBACK_RUNTIME_IMAGES_GENERATION" =~ ^[1-9][0-9]*$ || \
        ! "$ROLLBACK_RUNTIME_IMAGES_SIZE_BYTES" =~ ^[1-9][0-9]*$ || \
        ! "$ROLLBACK_RUNTIME_IMAGES_SHA256" =~ ^[0-9a-f]{64}$ || \
        ! "$ROLLBACK_BACKUP_MANIFEST_URI" =~ ^gs://[^/]+/_omega_backups/[^/]+/manifest\.json$ || \
        ! "$ROLLBACK_BACKUP_MANIFEST_GENERATION" =~ ^[1-9][0-9]*$ || \
        ! "$ROLLBACK_BACKUP_MANIFEST_SIZE_BYTES" =~ ^[1-9][0-9]*$ || \
        ! "$ROLLBACK_BACKUP_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ || \
        ! "$LEGACY_TAG_COMMIT" =~ ^[0-9a-f]{40}$ || \
        "${ROLLBACK_RUNTIME_IMAGES_URI%/runtime-images.json}" != \
          "${ROLLBACK_BACKUP_MANIFEST_URI%/manifest.json}" ]]; then
    fail "rollback image authority" "checksum-bound backup runtime image inventory is required"
  fi
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
ROLLBACK_RUNTIME_IMAGES=""
if [[ "$AUTHORITY_MODE" == "legacy-rollback" ]]; then
  ROLLBACK_RUNTIME_IMAGES="${WORKDIR}/rollback-runtime-images.json"
  ROLLBACK_BACKUP_MANIFEST="${WORKDIR}/rollback-backup-manifest.json"
  "$SAFE_IO" gcs-download --uri "$ROLLBACK_RUNTIME_IMAGES_URI" \
    --generation "$ROLLBACK_RUNTIME_IMAGES_GENERATION" \
    --size "$ROLLBACK_RUNTIME_IMAGES_SIZE_BYTES" \
    --sha256 "$ROLLBACK_RUNTIME_IMAGES_SHA256" \
    --output "$ROLLBACK_RUNTIME_IMAGES"
  "$SAFE_IO" gcs-download --uri "$ROLLBACK_BACKUP_MANIFEST_URI" \
    --generation "$ROLLBACK_BACKUP_MANIFEST_GENERATION" \
    --size "$ROLLBACK_BACKUP_MANIFEST_SIZE_BYTES" \
    --sha256 "$ROLLBACK_BACKUP_MANIFEST_SHA256" \
    --output "$ROLLBACK_BACKUP_MANIFEST"
  python3 - "$ROLLBACK_BACKUP_MANIFEST" "$TARGET_REF" "$TARGET_VERSION" \
    "$ROLLBACK_RUNTIME_IMAGES_URI" "$ROLLBACK_RUNTIME_IMAGES_GENERATION" \
    "$ROLLBACK_RUNTIME_IMAGES_SIZE_BYTES" "$ROLLBACK_RUNTIME_IMAGES_SHA256" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
artifact = manifest.get("artifacts", {}).get("runtime_images", {})
if (
    manifest.get("schema_version") != 4
    or manifest.get("complete") is not True
    or manifest.get("source_release")
       != {"deploy_ref": sys.argv[2], "version": sys.argv[3]}
    or artifact.get("uri") != sys.argv[4]
    or str(artifact.get("generation")) != sys.argv[5]
    or int(artifact.get("size_bytes", -1)) != int(sys.argv[6])
    or artifact.get("sha256") != sys.argv[7]
    or manifest.get("plaintext_runtime_secrets_included") is not False
):
    raise SystemExit("rollback runtime images are not bound to the verified backup")
PY
fi
if ! OMEGA_GCP_ENVIRONMENT="$GCP_ENVIRONMENT" \
    OMEGA_GHCR_PULL_SECRET_VERSION="$GHCR_SECRET_VERSION" \
    OMEGA_GCP_IMAGE_AUTHORITY_MODE="$AUTHORITY_MODE" \
    OMEGA_RELEASE_CANDIDATE_MANIFEST_SHA256="$CANDIDATE_MANIFEST_SHA256" \
    OMEGA_RELEASE_CANDIDATE_RUN_ID="$CANDIDATE_RUN_ID" \
    OMEGA_RELEASE_CANDIDATE_RUN_ATTEMPT="$CANDIDATE_RUN_ATTEMPT" \
    OMEGA_RELEASE_CANDIDATE_CONTROLLER_ATTESTATION_SHA256="$CANDIDATE_CONTROLLER_ATTESTATION_SHA256" \
    OMEGA_RELEASE_TAG_MANIFEST_SHA256="$PUBLISHED_MANIFEST_SHA256" \
    OMEGA_RELEASE_TAG_OBJECT_SHA="$PUBLISHED_TAG_OBJECT_SHA" \
    OMEGA_GCP_ROLLBACK_RUNTIME_IMAGES="$ROLLBACK_RUNTIME_IMAGES" \
    OMEGA_GCP_LEGACY_TAG_COMMIT="$LEGACY_TAG_COMMIT" \
    "$AUTH_RUNNER" "$PREFLIGHT" "$GHCR_OWNER" "$TARGET_TAG" "$TARGET_REF" \
      "$TARGET_VERSION" "$LOCK_FILE" \
    >"${WORKDIR}/preflight.out" 2>"${WORKDIR}/preflight.err"; then
  fail "authenticated image pull" "less than 15/15; credential output suppressed"
fi
AUTHORITY_FILE="${LOCK_FILE}.authority.json"
if [[ ! -s "$AUTHORITY_FILE" ]]; then
  fail "immutable image authority" "verified authority receipt is missing"
fi

LOCK_SHA256="$(sha256sum "$LOCK_FILE" | awk '{print $1}')"
AUTHORITY_SHA256="$(sha256sum "$AUTHORITY_FILE" | awk '{print $1}')"
python3 - "$LOCK_FILE" "${WORKDIR}/manifest.json" "$HELPER_REF" \
  "$HELPER_ARTIFACT_URI" "$HELPER_ARTIFACT_GENERATION" \
  "$HELPER_ARTIFACT_SIZE_BYTES" "$HELPER_ARTIFACT_SHA256" \
  "$TARGET_TAG" "$TARGET_REF" "$TARGET_VERSION" "$TARGET_KIND" "$PURPOSE" \
  "$GHCR_OWNER" "$LOCK_SHA256" "$AUTHORITY_FILE" "$AUTHORITY_SHA256" \
  "$AUTHORITY_MODE" <<'PY'
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
    "schema_version": 2,
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
    "image_authority_sha256": sys.argv[16],
    "image_authority_mode": sys.argv[17],
    "secrets_included": False,
}
authority = json.load(open(sys.argv[15], encoding="utf-8"))
if authority.get("authority_mode") != sys.argv[17]:
    raise SystemExit("image authority mode differs")
payload["release_candidate_manifest_digest"] = authority.get("manifest_digest")
payload["release_tag_object_sha"] = authority.get("tag_object_sha")
payload["candidate_workflow"] = authority.get("candidate_workflow")
payload["legacy_tag_commit"] = authority.get("legacy_tag_commit")
pathlib.Path(sys.argv[2]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
MANIFEST_SHA256="$(sha256sum "${WORKDIR}/manifest.json" | awk '{print $1}')"
python3 - "${WORKDIR}/completion.json" "$MANIFEST_SHA256" "$LOCK_SHA256" \
  "$HELPER_REF" "$TARGET_TAG" "$TARGET_REF" "$TARGET_KIND" "$PURPOSE" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

payload = {
    "schema_version": 1,
    "completed_at": datetime.now(timezone.utc).isoformat(),
    "manifest_sha256": sys.argv[2],
    "lock_sha256": sys.argv[3],
    "helper_ref": sys.argv[4],
    "target_tag": sys.argv[5],
    "target_ref": sys.argv[6],
    "target_kind": sys.argv[7],
    "purpose": sys.argv[8],
    "image_count": 15,
    "secrets_included": False,
}
pathlib.Path(sys.argv[1]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

PREFLIGHT_ROOT="${SHARED_ROOT}/image-preflights/${PURPOSE}-${TARGET_KIND}-${TARGET_TAG}-${TARGET_REF}-by-${HELPER_REF}"
PREFLIGHT_TMP="${SHARED_ROOT}/image-preflights/.${PURPOSE}-${TARGET_KIND}-${TARGET_TAG}-${TARGET_REF}-by-${HELPER_REF}.$$"
install -d -m 0700 "${SHARED_ROOT}/image-preflights"
install -d -m 0700 "$PREFLIGHT_TMP"
install -m 0400 "$LOCK_FILE" "${PREFLIGHT_TMP}/release-images.env"
install -m 0400 "${WORKDIR}/manifest.json" "${PREFLIGHT_TMP}/manifest.json"
install -m 0400 "$AUTHORITY_FILE" "${PREFLIGHT_TMP}/image-authority.json"
install -m 0400 "${WORKDIR}/completion.json" "${PREFLIGHT_TMP}/completion.json"
"$SAFE_IO" fsync-file "${PREFLIGHT_TMP}/release-images.env" \
  "${PREFLIGHT_TMP}/manifest.json" "${PREFLIGHT_TMP}/image-authority.json" \
  "${PREFLIGHT_TMP}/completion.json"
"$SAFE_IO" fsync-dir "$PREFLIGHT_TMP"
if [[ -e "$PREFLIGHT_ROOT" || -L "$PREFLIGHT_ROOT" ]]; then
  if [[ -L "$PREFLIGHT_ROOT" || ! -d "$PREFLIGHT_ROOT" ]]; then
    fail "immutable preflight evidence" "existing evidence path is not a regular directory"
  fi
  cmp "${PREFLIGHT_ROOT}/release-images.env" "${PREFLIGHT_TMP}/release-images.env" >/dev/null || \
    fail "immutable preflight evidence" "existing lock differs from fresh 15/15 pulls"
  cmp "${PREFLIGHT_ROOT}/manifest.json" "${PREFLIGHT_TMP}/manifest.json" >/dev/null || \
    fail "immutable preflight evidence" "existing manifest differs from target identity"
  cmp "${PREFLIGHT_ROOT}/image-authority.json" \
    "${PREFLIGHT_TMP}/image-authority.json" >/dev/null || \
    fail "immutable preflight evidence" "existing immutable image authority differs"
  python3 - "${PREFLIGHT_ROOT}/completion.json" "$MANIFEST_SHA256" \
    "$LOCK_SHA256" "$HELPER_REF" "$TARGET_TAG" "$TARGET_REF" \
    "$TARGET_KIND" "$PURPOSE" <<'PY'
import json
import pathlib
import stat
import sys
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
info = path.lstat()
payload = json.load(open(path, encoding="utf-8"))
completed = datetime.fromisoformat(str(payload.get("completed_at", "")))
if (
    not stat.S_ISREG(info.st_mode)
    or info.st_uid != 0
    or info.st_gid != 0
    or stat.S_IMODE(info.st_mode) != 0o400
    or info.st_nlink != 1
    or set(payload) != {
        "schema_version", "completed_at", "manifest_sha256", "lock_sha256",
        "helper_ref", "target_tag", "target_ref", "target_kind", "purpose",
        "image_count", "secrets_included",
    }
    or payload.get("schema_version") != 1
    or completed.tzinfo is None
    or completed.utcoffset() != timezone.utc.utcoffset(completed)
    or completed > datetime.now(timezone.utc)
    or payload.get("manifest_sha256") != sys.argv[2]
    or payload.get("lock_sha256") != sys.argv[3]
    or payload.get("helper_ref") != sys.argv[4]
    or payload.get("target_tag") != sys.argv[5]
    or payload.get("target_ref") != sys.argv[6]
    or payload.get("target_kind") != sys.argv[7]
    or payload.get("purpose") != sys.argv[8]
    or payload.get("image_count") != 15
    or payload.get("secrets_included") is not False
):
    raise SystemExit("existing preflight completion receipt differs")
PY
  rm -rf -- "$PREFLIGHT_TMP"
  PREFLIGHT_TMP=""
else
  mv "$PREFLIGHT_TMP" "$PREFLIGHT_ROOT"
  "$SAFE_IO" fsync-dir "${SHARED_ROOT}/image-preflights"
  PREFLIGHT_TMP=""
fi
COMPLETION_SHA256="$(sha256sum "${PREFLIGHT_ROOT}/completion.json" | awk '{print $1}')"
emit "authenticated image pull" "PASS" "purpose=${PURPOSE} tag=${TARGET_TAG} images=15/15 lock_sha256=${LOCK_SHA256}"
printf 'OMEGA_GCP_IMAGE_PREFLIGHT_JSON={"status":"PASS","purpose":"%s","target_tag":"%s","target_ref":"%s","image_count":15,"lock_sha256":"%s","completion_sha256":"%s"}\n' \
  "$PURPOSE" "$TARGET_TAG" "$TARGET_REF" "$LOCK_SHA256" "$COMPLETION_SHA256"
