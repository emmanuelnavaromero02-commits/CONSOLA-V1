#!/usr/bin/env bash
# Prove authenticated 15/15 pullability for one immutable release/rollback tag.
set -Eeuo pipefail
set +x
umask 077

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: image-preflight.sh must run through sudo" >&2
  exit 10
fi

HELPER_REF="${1:-}"
HELPER_ARTIFACT_URI="${2:-}"
HELPER_ARTIFACT_SHA256="${3:-}"
TARGET_TAG="${4:-}"
TARGET_REF="${5:-}"
GHCR_OWNER="${6:-}"
GCP_ENVIRONMENT="${7:-}"
GHCR_SECRET_VERSION="${8:-}"
PURPOSE="${9:-}"
APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
SHARED_ROOT="${APP_ROOT}/shared"
WORKDIR=""
PREFLIGHT_TMP=""

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
      ! "$HELPER_ARTIFACT_SHA256" =~ ^[0-9a-f]{64}$ || \
      ! "$HELPER_ARTIFACT_URI" =~ ^gs://[^/]+/deploy-artifacts/${HELPER_REF}/repo\.tar\.gz$ ]]; then
  fail "helper artifact" "exact candidate helper ref/URI/checksum required"
fi
if [[ ! "$TARGET_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ || \
      ! "$TARGET_REF" =~ ^[0-9a-f]{40}$ ]]; then
  fail "target identity" "one immutable published tag/ref is required"
fi
if [[ ! "$GHCR_OWNER" =~ ^[a-z0-9][a-z0-9-]*$ || \
      ! "$GCP_ENVIRONMENT" =~ ^[a-z][a-z0-9-]*$ || \
      ! "$GHCR_SECRET_VERSION" =~ ^[1-9][0-9]*$ ]]; then
  fail "server-owned GHCR identity" "owner/environment/secret version invalid"
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

python3 - "$HELPER_ARTIFACT_URI" "${WORKDIR}/candidate.tar.gz" <<'PY'
import json
import pathlib
import sys
import urllib.parse
import urllib.request

uri, destination = sys.argv[1:]
parsed = urllib.parse.urlparse(uri)
token_request = urllib.request.Request(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
)
with urllib.request.urlopen(token_request, timeout=10) as response:
    token = json.load(response)["access_token"]
url = "https://storage.googleapis.com/download/storage/v1/b/{}/o/{}?alt=media".format(
    urllib.parse.quote(parsed.netloc, safe=""),
    urllib.parse.quote(parsed.path.lstrip("/"), safe=""),
)
request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
with urllib.request.urlopen(request, timeout=120) as response, pathlib.Path(destination).open("wb") as stream:
    while chunk := response.read(1024 * 1024):
        stream.write(chunk)
PY
if [[ "$(sha256sum "${WORKDIR}/candidate.tar.gz" | awk '{print $1}')" != "$HELPER_ARTIFACT_SHA256" ]]; then
  fail "helper artifact" "downloaded candidate checksum differs"
fi
install -d -m 0700 "${WORKDIR}/candidate"
python3 - "${WORKDIR}/candidate.tar.gz" "${WORKDIR}/candidate" <<'PY'
import pathlib
import sys
import tarfile

destination = pathlib.Path(sys.argv[2])
with tarfile.open(sys.argv[1], "r:gz") as archive:
    members = archive.getmembers()
    if not members:
        raise SystemExit("empty candidate archive")
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.isdev():
            raise SystemExit("unsafe candidate archive")
        if member.issym() or member.islnk():
            link = pathlib.PurePosixPath(member.linkname)
            if link.is_absolute() or ".." in link.parts:
                raise SystemExit("unsafe candidate archive link")
    archive.extractall(destination, members=members)
PY
AUTH_RUNNER="${WORKDIR}/candidate/infra/terraform-gcp/release/ghcr-auth-run.sh"
PREFLIGHT="${WORKDIR}/candidate/infra/terraform-gcp/release/preflight-release-images.sh"
if [[ ! -x "$AUTH_RUNNER" || ! -x "$PREFLIGHT" ]]; then
  fail "helper artifact" "audited GHCR helpers are missing from exact candidate"
fi
emit "helper artifact" "PASS" "ref=${HELPER_REF} sha256=${HELPER_ARTIFACT_SHA256}"

LOCK_FILE="${WORKDIR}/release-images.env"
if ! OMEGA_GCP_ENVIRONMENT="$GCP_ENVIRONMENT" \
    OMEGA_GHCR_PULL_SECRET_VERSION="$GHCR_SECRET_VERSION" \
    "$AUTH_RUNNER" "$PREFLIGHT" "$GHCR_OWNER" "$TARGET_TAG" "$LOCK_FILE" \
    >"${WORKDIR}/preflight.out" 2>"${WORKDIR}/preflight.err"; then
  fail "authenticated image pull" "less than 15/15; credential output suppressed"
fi

LOCK_SHA256="$(sha256sum "$LOCK_FILE" | awk '{print $1}')"
python3 - "$LOCK_FILE" "${WORKDIR}/manifest.json" "$HELPER_REF" \
  "$HELPER_ARTIFACT_SHA256" "$TARGET_TAG" "$TARGET_REF" "$PURPOSE" \
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
tag, owner = sys.argv[5], sys.argv[8]
for value in assignments.values():
    if re.fullmatch(
        rf"ghcr\.io/{re.escape(owner)}/[a-z0-9_-]+:{re.escape(tag)}@sha256:[0-9a-f]{{64}}",
        value,
    ) is None:
        raise SystemExit("lock contains an invalid immutable reference")
payload = {
    "schema_version": 1,
    "purpose": sys.argv[7],
    "target_tag": tag,
    "target_ref": sys.argv[6],
    "helper_ref": sys.argv[3],
    "helper_artifact_sha256": sys.argv[4],
    "ghcr_owner": owner,
    "image_count": len(assignments),
    "lock_sha256": sys.argv[9],
    "secrets_included": False,
}
pathlib.Path(sys.argv[2]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY

PREFLIGHT_ROOT="${SHARED_ROOT}/image-preflights/${PURPOSE}-${TARGET_REF}"
PREFLIGHT_TMP="${SHARED_ROOT}/image-preflights/.${PURPOSE}-${TARGET_REF}.$$"
install -d -m 0700 "${SHARED_ROOT}/image-preflights"
install -d -m 0700 "$PREFLIGHT_TMP"
install -m 0400 "$LOCK_FILE" "${PREFLIGHT_TMP}/release-images.env"
install -m 0400 "${WORKDIR}/manifest.json" "${PREFLIGHT_TMP}/manifest.json"
if [[ -d "$PREFLIGHT_ROOT" ]]; then
  cmp "${PREFLIGHT_ROOT}/release-images.env" "${PREFLIGHT_TMP}/release-images.env" >/dev/null || \
    fail "immutable preflight evidence" "existing lock differs from fresh 15/15 pulls"
  cmp "${PREFLIGHT_ROOT}/manifest.json" "${PREFLIGHT_TMP}/manifest.json" >/dev/null || \
    fail "immutable preflight evidence" "existing manifest differs from target identity"
  rm -rf -- "$PREFLIGHT_TMP"
  PREFLIGHT_TMP=""
else
  mv "$PREFLIGHT_TMP" "$PREFLIGHT_ROOT"
  PREFLIGHT_TMP=""
fi
emit "authenticated image pull" "PASS" "purpose=${PURPOSE} tag=${TARGET_TAG} images=15/15 lock_sha256=${LOCK_SHA256}"
printf 'OMEGA_GCP_IMAGE_PREFLIGHT_JSON={"status":"PASS","purpose":"%s","target_tag":"%s","target_ref":"%s","image_count":15,"lock_sha256":"%s"}\n' \
  "$PURPOSE" "$TARGET_TAG" "$TARGET_REF" "$LOCK_SHA256"
