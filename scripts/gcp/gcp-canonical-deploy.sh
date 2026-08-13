#!/usr/bin/env bash
# Reproducible, fail-closed deploy of a release to the CANONICAL GCP VM.
#
# This codifies how the omega-staging-app VM is actually deployed (source tarball
# in a private GCS bucket + a tag-pinned image overlay + docker compose), and
# wraps it in the Checkpoint 5.5 safety net that the live startup-script lacks:
# a verified backup of BOTH databases BEFORE any mutation, a health gate, and a
# fail-closed rollback to the previous release + database restore.
#
# It does NOT touch AWS (that is the DR standby; scripts/deploy_main_aws.py is the
# separate AWS path and must never run as the canonical writer).
#
# Run from an operator workstation with: gcloud auth (Owner or equivalent) + IAP
# access to the instance. Nothing secret is passed on the wire — the VM reads the
# GHCR pull credential from Secret Manager with its own service account.
#
#   gcp-canonical-deploy.sh <target-tag> <deploy-ref-40hex>
#
# Required env (all non-secret; derived from the Terraform stack):
#   OMEGA_PROJECT_ID      e.g. project-dd5ba7fa-374c-4554-ae6
#   OMEGA_ZONE            e.g. us-central1-a
#   OMEGA_INSTANCE        e.g. omega-staging-app
#   OMEGA_GHCR_OWNER      lowercase GHCR owner hosting the 15 packages
#   OMEGA_SOURCE_BUCKET   e.g. omega-gcp-source-project-dd5ba7fa-374c-4554-ae6
# Optional:
#   OMEGA_GHCR_PULL_SECRET_VERSION (default: latest)
set -Eeuo pipefail
set +x
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET_TAG="${1:-}"
DEPLOY_REF="${2:-}"

die() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[deploy] %s\n' "$*"; }

# The exact 15 proprietary images (single source of truth = release preflight).
IMAGES=(airflow banxico console hubspot inegi mcp-infra refinement replicon
  salesforce sap_hcm sap_s4hana sap_successfactors sec_edgar vault workspace)

# ── Validate inputs (non-secret) ────────────────────────────────────────────
[[ -n "${TARGET_TAG}" && -n "${DEPLOY_REF}" ]] \
  || die "usage: gcp-canonical-deploy.sh <target-tag> <deploy-ref-40hex>"
[[ "${TARGET_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
  || die "TARGET_TAG must be an immutable release tag (vX.Y.Z[-suffix])."
[[ "${DEPLOY_REF}" =~ ^[0-9a-f]{40}$ ]] || die "DEPLOY_REF must be a full 40-hex commit SHA."

for v in OMEGA_PROJECT_ID OMEGA_ZONE OMEGA_INSTANCE OMEGA_GHCR_OWNER OMEGA_SOURCE_BUCKET; do
  [[ -n "${!v:-}" ]] || die "${v} is required (derive it from the Terraform stack)."
done
ENVIRONMENT="${OMEGA_GCP_ENVIRONMENT:-staging}"
DEPLOY_MODE="${OMEGA_DEPLOY_MODE:-apply}"   # apply | dryrun
[[ "${DEPLOY_MODE}" == "apply" || "${DEPLOY_MODE}" == "dryrun" ]] || die "OMEGA_DEPLOY_MODE must be apply or dryrun."

# ghcr-auth-run.sh requires an explicit NUMERIC secret version. Resolve the
# highest enabled version of the pull credential unless the operator pinned one.
GHCR_SECRET_VERSION="${OMEGA_GHCR_PULL_SECRET_VERSION:-}"
if [[ -z "${GHCR_SECRET_VERSION}" ]]; then
  GHCR_SECRET_VERSION="$(gcloud secrets versions list "omega-${ENVIRONMENT}-ghcr_pull_credentials" \
    --project "${OMEGA_PROJECT_ID}" --filter='state:ENABLED' --format='value(name)' --sort-by=~name --limit=1 2>/dev/null || true)"
fi
[[ "${GHCR_SECRET_VERSION}" =~ ^[1-9][0-9]*$ ]] \
  || die "no enabled numeric version of omega-${ENVIRONMENT}-ghcr_pull_credentials (add the read:packages token first)."

# ── 1. Provenance: the tag must resolve to exactly this ref ─────────────────
log "step 1/6 provenance: ${TARGET_TAG} -> ${DEPLOY_REF}"
tag_ref="$(git rev-list -n 1 "${TARGET_TAG}" 2>/dev/null || true)"
[[ "${tag_ref}" == "${DEPLOY_REF}" ]] \
  || die "tag ${TARGET_TAG} resolves to '${tag_ref:-<missing>}', not ${DEPLOY_REF}. Cut the tag on the exact ref first."
# The app serves /healthz version from the committed VERSION file; if it does not
# equal the tag, the on-VM health gate can never go green and would force a
# rollback. Catch that here, before anything is built or deployed.
committed_version="$(git show "${DEPLOY_REF}:VERSION" 2>/dev/null | tr -d '[:space:]' || true)"
[[ "${committed_version}" == "${TARGET_TAG#v}" ]] \
  || die "VERSION at ${DEPLOY_REF} is '${committed_version}', but the tag implies '${TARGET_TAG#v}'. Align the tag with the committed VERSION."

# ── 2. Source artifact: build + upload the exact tarball (idempotent) ────────
SOURCE_OBJECT="CONSOLA-V1-${DEPLOY_REF}.tar.gz"
log "step 2/6 source: ${SOURCE_OBJECT} -> gs://${OMEGA_SOURCE_BUCKET}"
if gcloud storage ls "gs://${OMEGA_SOURCE_BUCKET}/${SOURCE_OBJECT}" --project "${OMEGA_PROJECT_ID}" >/dev/null 2>&1; then
  log "source tarball already present; reusing (immutable by ref)"
else
  tmp_tar="$(mktemp -t omega-source.XXXXXX.tar.gz)"
  trap 'rm -f -- "${tmp_tar}"' EXIT
  # Archive the EXACT committed tree at the ref — no working-tree contamination.
  git archive --format=tar.gz --prefix="modecissions/" -o "${tmp_tar}" "${DEPLOY_REF}"
  gcloud storage cp "${tmp_tar}" "gs://${OMEGA_SOURCE_BUCKET}/${SOURCE_OBJECT}" --project "${OMEGA_PROJECT_ID}"
  rm -f -- "${tmp_tar}"; trap - EXIT
fi

# ── 3. Image overlay: pin all 15 services to the tag (tag substitution) ─────
log "step 3/6 overlay: pin 15 images to ${TARGET_TAG}"
overlay="$(mktemp -t omega-images.XXXXXX.yml)"
trap 'rm -f -- "${overlay}"' EXIT
{
  printf '# Generated by gcp-canonical-deploy.sh — pins the release images by tag.\n'
  printf 'services:\n'
  # compose service name uses "-" where the image name uses "_"
  for image in "${IMAGES[@]}"; do
    svc="${image//_/-}"
    printf '  %s:\n    image: ghcr.io/%s/%s:%s\n    pull_policy: never\n' \
      "${svc}" "${OMEGA_GHCR_OWNER}" "${image}" "${TARGET_TAG}"
    # airflow image backs three services
    if [[ "${image}" == "airflow" ]]; then
      # airflow serves under the /airflow base path; some release compose files
      # curl /health (404) in the container healthcheck, so it never goes healthy
      # and `up -d` aborts. Pin the correct /airflow/health path here.
      printf '    healthcheck:\n      test: ["CMD-SHELL", "curl -f http://127.0.0.1:8080/airflow/health || exit 1"]\n'
      printf '  airflow-init:\n    image: ghcr.io/%s/airflow:%s\n    pull_policy: never\n' "${OMEGA_GHCR_OWNER}" "${TARGET_TAG}"
      printf '  airflow-scheduler:\n    image: ghcr.io/%s/airflow:%s\n    pull_policy: never\n' "${OMEGA_GHCR_OWNER}" "${TARGET_TAG}"
    fi
  done
} > "${overlay}"

# ── 4. Ship the remote deployer + overlay to the VM ─────────────────────────
log "step 4/6 stage: copy overlay + remote deployer to the VM"
remote_deployer="${SCRIPT_DIR}/gcp-canonical-deploy-remote.sh"
[[ -f "${remote_deployer}" ]] || die "missing ${remote_deployer}"

SSH=(gcloud compute ssh "${OMEGA_INSTANCE}" --zone "${OMEGA_ZONE}" --project "${OMEGA_PROJECT_ID}" --tunnel-through-iap)
SCP=(gcloud compute scp --zone "${OMEGA_ZONE}" --project "${OMEGA_PROJECT_ID}" --tunnel-through-iap)

"${SCP[@]}" "${overlay}" "${OMEGA_INSTANCE}:/tmp/omega-images-${DEPLOY_REF}.yml"
"${SCP[@]}" "${remote_deployer}" "${OMEGA_INSTANCE}:/tmp/gcp-canonical-deploy-remote.sh"
rm -f -- "${overlay}"; trap - EXIT

# ── 5. Run the fail-closed remote deploy (backup -> migrate -> up -> health) ─
log "step 5/6 deploy: running fail-closed remote deploy on ${OMEGA_INSTANCE}"
"${SSH[@]}" --command "sudo TARGET_TAG='${TARGET_TAG}' DEPLOY_REF='${DEPLOY_REF}' \
  OMEGA_PROJECT_ID='${OMEGA_PROJECT_ID}' ENVIRONMENT='${ENVIRONMENT}' \
  GHCR_OWNER='${OMEGA_GHCR_OWNER}' GHCR_SECRET_VERSION='${GHCR_SECRET_VERSION}' \
  SOURCE_BUCKET='${OMEGA_SOURCE_BUCKET}' SOURCE_OBJECT='${SOURCE_OBJECT}' \
  IMAGES_OVERLAY='/tmp/omega-images-${DEPLOY_REF}.yml' DEPLOY_MODE='${DEPLOY_MODE}' \
  bash /tmp/gcp-canonical-deploy-remote.sh"

if [[ "${DEPLOY_MODE}" == "dryrun" ]]; then
  log "DRY-RUN complete for ${TARGET_TAG} (${DEPLOY_REF}); no database or service was mutated."
  exit 0
fi

# ── 6. External health confirmation ─────────────────────────────────────────
log "step 6/6 confirm: external readiness of the promoted release"
"${SSH[@]}" --command "curl -fsS --max-time 5 http://127.0.0.1:8000/healthz" \
  || die "post-deploy healthz did not answer 200 from the operator side."

log "DONE: ${TARGET_TAG} (${DEPLOY_REF}) deployed to ${OMEGA_INSTANCE}."
printf 'GCP_CANONICAL_DEPLOY\tPASS\ttag=%s\tref=%s\tinstance=%s\n' "${TARGET_TAG}" "${DEPLOY_REF}" "${OMEGA_INSTANCE}"
