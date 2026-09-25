#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET_TAG="${1:-}"
DEPLOY_REF="${2:-}"

die() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[deploy] %s\n' "$*"; }

TEMP_FILES=()
REMOTE_STAGE_PATHS=()
SSH=()
cleanup() {
  local path remote_cleanup
  if [[ "${#SSH[@]}" -gt 0 && "${#REMOTE_STAGE_PATHS[@]}" -gt 0 ]]; then
    printf -v remote_cleanup '%q ' rm -f -- "${REMOTE_STAGE_PATHS[@]}"
    "${SSH[@]}" --command "${remote_cleanup% }" >/dev/null 2>&1 || true
  fi
  for path in "${TEMP_FILES[@]}"; do
    [[ -n "${path}" ]] && rm -f -- "${path}"
  done
}
trap cleanup EXIT

new_temp_file() {
  local variable="$1" template="$2" result
  result="$(mktemp -t "${template}")"
  TEMP_FILES+=("${result}")
  printf -v "${variable}" '%s' "${result}"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -- "$1" | awk '{print $1}'
  else
    shasum -a 256 -- "$1" | awk '{print $1}'
  fi
}

IMAGES=(airflow banxico console hubspot inegi mcp-infra refinement replicon
  salesforce sap_b1 sap_hcm sap_s4hana sap_successfactors sec_edgar vault workspace)

[[ -n "${TARGET_TAG}" && -n "${DEPLOY_REF}" ]] \
  || die "usage: gcp-canonical-deploy.sh <target-tag> <deploy-ref-40hex>"
[[ "${TARGET_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
  || die "TARGET_TAG must be an immutable release tag (vX.Y.Z[-suffix])."
[[ "${DEPLOY_REF}" =~ ^[0-9a-f]{40}$ ]] || die "DEPLOY_REF must be a full 40-hex commit SHA."

for v in OMEGA_PROJECT_ID OMEGA_ZONE OMEGA_INSTANCE OMEGA_GHCR_OWNER OMEGA_SOURCE_BUCKET; do
  [[ -n "${!v:-}" ]] || die "${v} is required (derive it from the Terraform stack)."
done
[[ "${OMEGA_PROJECT_ID}" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] \
  || die "OMEGA_PROJECT_ID is not a valid GCP project id."
[[ "${OMEGA_ZONE}" =~ ^[a-z][a-z0-9-]{1,30}-[a-z]$ ]] \
  || die "OMEGA_ZONE is not a valid GCP zone."
[[ "${OMEGA_INSTANCE}" =~ ^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$ ]] \
  || die "OMEGA_INSTANCE is not a valid GCE instance name."
[[ "${OMEGA_GHCR_OWNER}" =~ ^[a-z0-9]([a-z0-9-]{0,37}[a-z0-9])?$ ]] \
  || die "OMEGA_GHCR_OWNER must be one lowercase GitHub owner (no shell metacharacters)."
[[ "${OMEGA_SOURCE_BUCKET}" =~ ^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$ ]] \
  || die "OMEGA_SOURCE_BUCKET is not a valid GCS bucket name."
ENVIRONMENT="${OMEGA_GCP_ENVIRONMENT:-staging}"
DEPLOY_MODE="${OMEGA_DEPLOY_MODE:-apply}"   # apply | dryrun
[[ "${DEPLOY_MODE}" == "apply" || "${DEPLOY_MODE}" == "dryrun" ]] || die "OMEGA_DEPLOY_MODE must be apply or dryrun."
[[ "${ENVIRONMENT}" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] \
  || die "OMEGA_GCP_ENVIRONMENT contains unsupported characters."
IMAGE_PULL_MIN_FREE_GIB="${OMEGA_IMAGE_PULL_MIN_FREE_GIB:-20}"
[[ "${IMAGE_PULL_MIN_FREE_GIB}" =~ ^[1-9][0-9]*$ \
      && "${IMAGE_PULL_MIN_FREE_GIB}" -ge 5 \
      && "${IMAGE_PULL_MIN_FREE_GIB}" -le 1024 ]] \
  || die "OMEGA_IMAGE_PULL_MIN_FREE_GIB must be an integer from 5 through 1024."

MANIFEST="${OMEGA_RELEASE_MANIFEST:-}"
[[ -n "${MANIFEST}" && -f "${MANIFEST}" ]] \
  || die "OMEGA_RELEASE_MANIFEST must point at the release manifest json for ${TARGET_TAG}."
MANIFEST_CHECKSUM="${OMEGA_RELEASE_MANIFEST_CHECKSUM:-${MANIFEST}.sha256}"
[[ -f "${MANIFEST_CHECKSUM}" ]] \
  || die "OMEGA_RELEASE_MANIFEST_CHECKSUM must point at the canonical sibling checksum asset."
MANIFEST_VALIDATOR="${SCRIPT_DIR}/../release_digest_env.py"
[[ -f "${MANIFEST_VALIDATOR}" ]] || die "missing canonical release manifest validator."

BUILD_RUN_ID="$(python3 -I - "${MANIFEST}" <<'PY'
import json
import sys

try:
    value = json.loads(open(sys.argv[1], "rb").read())
    run_id = value.get("build_run_id") if isinstance(value, dict) else None
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit(1)
if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
    raise SystemExit(1)
print(run_id)
PY
)" || die "release manifest build identity is invalid."
new_temp_file MANIFEST_LOCK omega-manifest-lock.XXXXXX
python3 -I "${MANIFEST_VALIDATOR}" \
  --manifest "${MANIFEST}" \
  --checksum "${MANIFEST_CHECKSUM}" \
  --repository "${OMEGA_GHCR_OWNER}/CONSOLA-V1" \
  --release-tag "${TARGET_TAG}" \
  --source-sha "${DEPLOY_REF}" \
  --build-run-id "${BUILD_RUN_ID}" \
  --github-env "${MANIFEST_LOCK}" >/dev/null \
  || die "canonical release manifest/checksum/schema/owner/digest validation failed."
[[ "$(wc -l < "${MANIFEST_LOCK}" | tr -d '[:space:]')" == "16" ]] \
  || die "canonical release manifest did not produce an exact 16-image lock."

digest_ref_for() {
  local image="$1" key
  key="OMEGA_GCP_IMAGE_$(printf '%s' "${image}" | tr '[:lower:]-' '[:upper:]_')"
  awk -F= -v key="${key}" '
    $1 == key { if (++count > 1) exit 2; value = substr($0, length(key) + 2) }
    END { if (count != 1) exit 3; print value }
  ' "${MANIFEST_LOCK}"
}

GHCR_SECRET_VERSION="${OMEGA_GHCR_PULL_SECRET_VERSION:-}"
if [[ -z "${GHCR_SECRET_VERSION}" ]]; then
  GHCR_SECRET_VERSION="$(gcloud secrets versions list "omega-${ENVIRONMENT}-ghcr_pull_credentials" \
    --project "${OMEGA_PROJECT_ID}" --filter='state:ENABLED' --format='value(name)' --sort-by=~name --limit=1 2>/dev/null || true)"
fi
[[ "${GHCR_SECRET_VERSION}" =~ ^[1-9][0-9]*$ ]] \
  || die "no enabled numeric version of omega-${ENVIRONMENT}-ghcr_pull_credentials (add the read:packages token first)."

log "step 1/6 provenance: ${TARGET_TAG} -> ${DEPLOY_REF}"
tag_ref="$(git rev-list -n 1 "${TARGET_TAG}" 2>/dev/null || true)"
[[ "${tag_ref}" == "${DEPLOY_REF}" ]] \
  || die "tag ${TARGET_TAG} resolves to '${tag_ref:-<missing>}', not ${DEPLOY_REF}. Cut the tag on the exact ref first."
committed_version="$(git show "${DEPLOY_REF}:VERSION" 2>/dev/null | tr -d '[:space:]' || true)"
[[ "${committed_version}" == "${TARGET_TAG#v}" ]] \
  || die "VERSION at ${DEPLOY_REF} is '${committed_version}', but the tag implies '${TARGET_TAG#v}'. Align the tag with the committed VERSION."

SOURCE_OBJECT="CONSOLA-V1-${DEPLOY_REF}.tar.gz"
SOURCE_URI="gs://${OMEGA_SOURCE_BUCKET}/${SOURCE_OBJECT}"
log "step 2/6 source: ${SOURCE_OBJECT} -> gs://${OMEGA_SOURCE_BUCKET}"
new_temp_file tmp_tar omega-source.XXXXXX.tar.gz
git archive --format=tar.gz --prefix="modecissions/" -o "${tmp_tar}" "${DEPLOY_REF}"
SOURCE_SHA256="$(sha256_file "${tmp_tar}")"
[[ "${SOURCE_SHA256}" =~ ^[0-9a-f]{64}$ ]] || die "could not hash the exact source archive."

SOURCE_GENERATION="$(gcloud storage objects describe "${SOURCE_URI}" \
  --project "${OMEGA_PROJECT_ID}" --format='value(generation)' 2>/dev/null || true)"
if [[ -z "${SOURCE_GENERATION}" ]]; then
  if ! gcloud storage cp "${tmp_tar}" "${SOURCE_URI}" \
      --project "${OMEGA_PROJECT_ID}" --if-generation-match=0; then
    log "source publication raced or failed; verifying the immutable object now present"
  fi
  SOURCE_GENERATION="$(gcloud storage objects describe "${SOURCE_URI}" \
    --project "${OMEGA_PROJECT_ID}" --format='value(generation)')"
else
  log "source tarball already present; verifying its exact generation and bytes"
fi
[[ "${SOURCE_GENERATION}" =~ ^[1-9][0-9]*$ ]] \
  || die "source object has no usable immutable GCS generation."

new_temp_file verified_tar omega-source-verify.XXXXXX.tar.gz
gcloud storage cp "${SOURCE_URI}#${SOURCE_GENERATION}" "${verified_tar}" \
  --project "${OMEGA_PROJECT_ID}"
[[ "$(sha256_file "${verified_tar}")" == "${SOURCE_SHA256}" ]] \
  || die "source object checksum differs from git archive; refusing to reuse or overwrite it."

log "step 3/6 overlay: pin 15 manifest digests for ${TARGET_TAG}"
new_temp_file overlay omega-images.XXXXXX.yml
{
  printf '# Generated by gcp-canonical-deploy.sh — pins immutable release digests.\n'
  printf 'services:\n'
  for image in "${IMAGES[@]}"; do
    svc="${image//_/-}"
    ref="$(digest_ref_for "${image}")" || die "no digest for ${image} in the release manifest."
    printf '  %s:\n    image: %s\n    pull_policy: never\n' "${svc}" "${ref}"
    if [[ "${image}" == "airflow" ]]; then
      printf '    healthcheck:\n      test: ["CMD-SHELL", "curl -f http://127.0.0.1:8080/airflow/health || exit 1"]\n'
      printf '  airflow-init:\n    image: %s\n    pull_policy: never\n' "${ref}"
      printf '  airflow-scheduler:\n    image: %s\n    pull_policy: never\n' "${ref}"
    fi
  done
} > "${overlay}"
IMAGES_OVERLAY_SHA256="$(sha256_file "${overlay}")"
[[ "${IMAGES_OVERLAY_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
  || die "could not hash the canonical image overlay."

log "step 4/6 stage: copy overlay + remote deployer to the VM"
new_temp_file remote_deployer omega-remote-deployer.XXXXXX.sh
git show "${DEPLOY_REF}:scripts/gcp/gcp-canonical-deploy-remote.sh" > "${remote_deployer}" \
  || die "remote deployer is missing from the exact release ref."
REMOTE_DEPLOYER_SHA256="$(sha256_file "${remote_deployer}")"
[[ "${REMOTE_DEPLOYER_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
  || die "could not hash the exact-ref remote deployer."
STAGE_NONCE="$(python3 -I -c 'import secrets; print(secrets.token_hex(16))')"
[[ "${STAGE_NONCE}" =~ ^[0-9a-f]{32}$ ]] || die "could not create a safe staging nonce."
REMOTE_DEPLOYER_STAGED="/tmp/gcp-canonical-deploy-remote-${DEPLOY_REF}-${STAGE_NONCE}.sh"
REMOTE_OVERLAY_STAGED="/tmp/omega-images-${DEPLOY_REF}-${STAGE_NONCE}.yml"

SSH=(gcloud compute ssh "${OMEGA_INSTANCE}" --zone "${OMEGA_ZONE}" --project "${OMEGA_PROJECT_ID}" --tunnel-through-iap)
SCP=(gcloud compute scp --zone "${OMEGA_ZONE}" --project "${OMEGA_PROJECT_ID}" --tunnel-through-iap)

REMOTE_STAGE_PATHS=("${REMOTE_OVERLAY_STAGED}" "${REMOTE_DEPLOYER_STAGED}")
"${SCP[@]}" "${overlay}" "${OMEGA_INSTANCE}:${REMOTE_OVERLAY_STAGED}"
"${SCP[@]}" "${remote_deployer}" "${OMEGA_INSTANCE}:${REMOTE_DEPLOYER_STAGED}"

log "step 5/6 deploy: running fail-closed remote deploy on ${OMEGA_INSTANCE}"
REMOTE_BOOTSTRAP="$(cat <<'OMEGA_REMOTE_BOOTSTRAP'
set -Eeuo pipefail
umask 077
staged_deployer="$1"
expected_sha="$2"
staged_overlay="$3"
shift 3
[[ "${staged_deployer}" =~ ^/tmp/gcp-canonical-deploy-remote-[0-9a-f]{40}-[0-9a-f]{32}\.sh$ ]] || exit 70
stage_identity="${staged_deployer#/tmp/gcp-canonical-deploy-remote-}"
stage_identity="${stage_identity%.sh}"
[[ "${staged_overlay}" == "/tmp/omega-images-${stage_identity}.yml" ]] || exit 71
[[ "${expected_sha}" =~ ^[0-9a-f]{64}$ ]] || exit 72
[[ -f "${staged_deployer}" && ! -L "${staged_deployer}" ]] || exit 73
[[ -f "${staged_overlay}" && ! -L "${staged_overlay}" ]] || exit 74
trusted_deployer="$(mktemp /opt/modecissions/.gcp-canonical-deploy-remote.XXXXXX.sh)"
cleanup_bootstrap() {
  rm -f -- "${trusted_deployer}" "${staged_deployer}" "${staged_overlay}"
}
trap cleanup_bootstrap EXIT
cp -- "${staged_deployer}" "${trusted_deployer}"
chown root:root "${trusted_deployer}"
chmod 0500 "${trusted_deployer}"
rm -f -- "${staged_deployer}"
actual_sha="$(sha256sum -- "${trusted_deployer}" | awk '{print $1}')"
[[ "${actual_sha}" == "${expected_sha}" ]] || exit 75
env "$@" bash "${trusted_deployer}"
OMEGA_REMOTE_BOOTSTRAP
)"
remote_argv=(sudo bash -c "${REMOTE_BOOTSTRAP}" omega-remote-bootstrap
  "${REMOTE_DEPLOYER_STAGED}"
  "${REMOTE_DEPLOYER_SHA256}"
  "${REMOTE_OVERLAY_STAGED}"
  "TARGET_TAG=${TARGET_TAG}"
  "DEPLOY_REF=${DEPLOY_REF}"
  "OMEGA_PROJECT_ID=${OMEGA_PROJECT_ID}"
  "ENVIRONMENT=${ENVIRONMENT}"
  "GHCR_OWNER=${OMEGA_GHCR_OWNER}"
  "GHCR_SECRET_VERSION=${GHCR_SECRET_VERSION}"
  "SOURCE_BUCKET=${OMEGA_SOURCE_BUCKET}"
  "SOURCE_OBJECT=${SOURCE_OBJECT}"
  "SOURCE_SHA256=${SOURCE_SHA256}"
  "SOURCE_GENERATION=${SOURCE_GENERATION}"
  "IMAGES_OVERLAY_SHA256=${IMAGES_OVERLAY_SHA256}"
  "IMAGES_OVERLAY=${REMOTE_OVERLAY_STAGED}"
  "IMAGE_PULL_MIN_FREE_GIB=${IMAGE_PULL_MIN_FREE_GIB}"
  "DEPLOY_MODE=${DEPLOY_MODE}"
)
printf -v remote_command '%q ' "${remote_argv[@]}"
"${SSH[@]}" --command "${remote_command% }"

if [[ "${DEPLOY_MODE}" == "dryrun" ]]; then
  log "DRY-RUN complete for ${TARGET_TAG} (${DEPLOY_REF}); no database or service was mutated."
  exit 0
fi

log "step 6/6 confirm: external readiness of the promoted release"
"${SSH[@]}" --command "curl -fsS --max-time 5 http://127.0.0.1:8000/healthz" \
  || die "post-deploy healthz did not answer 200 from the operator side."

log "DONE: ${TARGET_TAG} (${DEPLOY_REF}) deployed to ${OMEGA_INSTANCE}."
printf 'GCP_CANONICAL_DEPLOY\tPASS\ttag=%s\tref=%s\tinstance=%s\n' "${TARGET_TAG}" "${DEPLOY_REF}" "${OMEGA_INSTANCE}"
