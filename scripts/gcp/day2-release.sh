#!/usr/bin/env bash
# Checkpoint 5.5 canonical day-two release controller (runs ON the canonical VM).
#
# One comprehensible, fail-closed sequence. Nothing is promoted until every gate
# is green; any failure after the databases are captured triggers rollback to the
# exact previous digest set and, if migrations ran, a restore from the verified
# restore-point captured in step 2 — never a blind N-1.
#
#   1. preflight  — verify-host-identity + verify-secret-access (grants)
#   2. backup     — verifiable restore-point of BOTH databases (before mutation)
#   3. images     — server-owned 15/15 pull + digest lock (reuses main's scripts)
#   4. migrations — forward-only ledger with drift + advisory lock
#   5. deploy     — compose up by pinned digest (pull_policy: never)
#   6. health     — /healthz version+app_env and /readyz?require_data=1 == 200
#   7. regression — beta smoke
#   8. promote    — record the new lock as current (atomic pointer); else rollback
#
# Invocation (from an operator workstation with IAP access):
#   gcloud compute ssh <instance> --project <p> --zone <z> --tunnel-through-iap \
#     -- sudo TARGET_TAG=vX.Y.Z-beta DEPLOY_REF=<40-hex> GHCR_OWNER=<owner> \
#        EXPECTED_PROJECT_ID=<p> EXPECTED_INSTANCE_NAME=<name> EXPECTED_ZONE=<z> \
#        EXPECTED_SERVICE_ACCOUNT=<sa> OMEGA_GCP_ENVIRONMENT=<env> \
#        OMEGA_GHCR_PULL_SECRET_VERSION=<n> bash scripts/gcp/day2-release.sh
set -Eeuo pipefail
set +x
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RELEASE_DIR="${ROOT_DIR}/infra/terraform-gcp/release"

# ── Required release identity ───────────────────────────────────────────────
TARGET_TAG="${TARGET_TAG:-}"
DEPLOY_REF="${DEPLOY_REF:-}"
GHCR_OWNER="${GHCR_OWNER:-}"
EXPECTED_PROJECT_ID="${EXPECTED_PROJECT_ID:-}"
EXPECTED_INSTANCE_NAME="${EXPECTED_INSTANCE_NAME:-}"
EXPECTED_ZONE="${EXPECTED_ZONE:-}"
EXPECTED_SERVICE_ACCOUNT="${EXPECTED_SERVICE_ACCOUNT:-}"

# ── Tunables (safe defaults) ────────────────────────────────────────────────
STATE_DIR="${OMEGA_RELEASE_STATE_DIR:-/var/lib/omega/release}"
BACKUP_ROOT="${OMEGA_BACKUP_ROOT:-/var/lib/omega/backups}"
CONSOLE_HEALTH_URL="${OMEGA_CONSOLE_HEALTH_URL:-http://127.0.0.1:8000}"
HEALTH_RETRIES="${OMEGA_HEALTH_RETRIES:-60}"
HEALTH_INTERVAL="${OMEGA_HEALTH_INTERVAL:-5}"
COMPOSE_FILE="${OMEGA_COMPOSE_FILE:-${ROOT_DIR}/infra/docker-compose.yml}"
RELEASE_OVERLAY="${OMEGA_RELEASE_OVERLAY:-${RELEASE_DIR}/docker-compose.release.yml}"

log() { printf '[day2] %s\n' "$*"; }
die() { printf '[day2] ERROR: %s\n' "$*" >&2; exit 1; }

require() {
  local name="$1" value="$2"
  [[ -n "${value}" ]] || die "${name} is required; refusing to run without an explicit release identity."
}
require TARGET_TAG "${TARGET_TAG}"
require DEPLOY_REF "${DEPLOY_REF}"
require GHCR_OWNER "${GHCR_OWNER}"
require EXPECTED_PROJECT_ID "${EXPECTED_PROJECT_ID}"
require EXPECTED_INSTANCE_NAME "${EXPECTED_INSTANCE_NAME}"
require EXPECTED_ZONE "${EXPECTED_ZONE}"
require EXPECTED_SERVICE_ACCOUNT "${EXPECTED_SERVICE_ACCOUNT}"

[[ "${TARGET_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
  || die "TARGET_TAG must be an explicit immutable release tag (vX.Y.Z[-suffix])."
[[ "${DEPLOY_REF}" =~ ^[0-9a-f]{40}$ ]] \
  || die "DEPLOY_REF must be a full 40-hex git commit SHA."

EXPECTED_VERSION="${TARGET_TAG#v}"       # console reports the bare version
CURRENT_LOCK="${STATE_DIR}/current.lock"
PREVIOUS_LOCK="${STATE_DIR}/previous.lock"
CANDIDATE_LOCK="${STATE_DIR}/candidate-${DEPLOY_REF}.lock"

mkdir -p "${STATE_DIR}" "${BACKUP_ROOT}"
chmod 700 "${STATE_DIR}" "${BACKUP_ROOT}"

BACKUP_DIR="${BACKUP_ROOT}/${TARGET_TAG}-${DEPLOY_REF}"
BACKUP_CAPTURED=0
MIGRATIONS_RAN=0
PROMOTED=0

dc() { docker compose --env-file "${CANDIDATE_LOCK}" -f "${COMPOSE_FILE}" -f "${RELEASE_OVERLAY}" "$@"; }

# ── Fail-closed rollback ────────────────────────────────────────────────────
rollback() {
  log "ROLLBACK: release did not reach a green promote; restoring the previous runtime."
  if [[ -s "${PREVIOUS_LOCK}" ]]; then
    log "rollback: re-pinning the previous digest set and bringing it up"
    docker compose --env-file "${PREVIOUS_LOCK}" -f "${COMPOSE_FILE}" -f "${RELEASE_OVERLAY}" up -d || \
      log "rollback WARNING: compose up on the previous digest set reported an error"
  else
    log "rollback: no previous digest set on record (first release); leaving runtime stopped is safer than promoting an unverified one"
  fi
  if [[ "${MIGRATIONS_RAN}" -eq 1 && "${BACKUP_CAPTURED}" -eq 1 ]]; then
    log "rollback: migrations ran this release — restoring both databases from the verified restore-point"
    bash "${SCRIPT_DIR}/backup-restore.sh" restore "${BACKUP_DIR}" || \
      log "rollback WARNING: database restore reported an error; DO NOT promote — investigate ${BACKUP_DIR}"
  fi
}

on_error() {
  local rc=$?
  trap - ERR EXIT
  [[ "${PROMOTED}" -eq 1 ]] && exit "${rc}"
  if [[ "${BACKUP_CAPTURED}" -eq 1 ]]; then
    rollback
  else
    log "aborted before any mutation; nothing to roll back"
  fi
  exit "${rc}"
}
trap on_error ERR
trap on_error EXIT

# ── 1. Preflight identity + secret access ───────────────────────────────────
log "step 1/8 preflight: host identity + effective secret access"
bash "${SCRIPT_DIR}/verify-host-identity.sh" \
  "${EXPECTED_PROJECT_ID}" "${EXPECTED_INSTANCE_NAME}" "${EXPECTED_ZONE}" "${EXPECTED_SERVICE_ACCOUNT}"
# verify-secret-access.sh requires root; this controller is invoked under sudo.
bash "${SCRIPT_DIR}/verify-secret-access.sh" "${EXPECTED_PROJECT_ID}" "${OMEGA_GCP_ENVIRONMENT:?OMEGA_GCP_ENVIRONMENT is required}" grants

# ── 2. Backup (before any mutation) ─────────────────────────────────────────
log "step 2/8 backup: capturing a verifiable restore-point for both databases"
OMEGA_DEPLOY_REF="${DEPLOY_REF}" bash "${SCRIPT_DIR}/backup-restore.sh" backup "${BACKUP_DIR}"
BACKUP_CAPTURED=1

# ── 3. Images: server-owned 15/15 pull + digest lock ────────────────────────
log "step 3/8 images: authenticated 15/15 pull and digest lock"
bash "${RELEASE_DIR}/ghcr-auth-run.sh" \
  bash "${RELEASE_DIR}/preflight-release-images.sh" "${GHCR_OWNER}" "${TARGET_TAG}" "${CANDIDATE_LOCK}"
[[ -s "${CANDIDATE_LOCK}" ]] || die "image preflight did not produce a digest lock."

# ── 4. Migrations: forward-only ledger with drift + advisory lock ───────────
log "step 4/8 migrations: forward-only ledger (drift detection + advisory lock)"
MIGRATIONS_RAN=1
OMEGA_COMPOSE_FILE="${COMPOSE_FILE}" bash "${ROOT_DIR}/scripts/apply_db_migrations.sh"

# ── 5. Deploy: compose up by pinned digest ──────────────────────────────────
log "step 5/8 deploy: bringing up the candidate by pinned digest (pull_policy: never)"
dc up -d

# ── 6. Health: liveness + version + readiness with data ─────────────────────
log "step 6/8 health: /healthz version+app_env and /readyz?require_data=1"
health_ok=0
for attempt in $(seq 1 "${HEALTH_RETRIES}"); do
  healthz="$(curl --fail --silent --show-error --max-time 5 "${CONSOLE_HEALTH_URL}/healthz" 2>/dev/null || true)"
  readyz_code="$(curl --output /dev/null --silent --write-out '%{http_code}' --max-time 10 \
    "${CONSOLE_HEALTH_URL}/readyz?require_data=1" 2>/dev/null || true)"
  version_ok="$(printf '%s' "${healthz}" | VER="${EXPECTED_VERSION}" python3 -c 'import json,os,sys
try:
    p = json.load(sys.stdin)
except Exception:
    sys.exit(1)
sys.exit(0 if p.get("version") == os.environ["VER"] and p.get("app_env") == "production" else 1)' && echo yes || echo no)"
  if [[ "${version_ok}" == "yes" && "${readyz_code}" == "200" ]]; then
    health_ok=1
    log "health: green on attempt ${attempt} (version=${EXPECTED_VERSION}, app_env=production, readyz?require_data=1=200)"
    break
  fi
  sleep "${HEALTH_INTERVAL}"
done
[[ "${health_ok}" -eq 1 ]] || die "health gate never went green (version/app_env/readyz require_data=1)."

# ── 7. Regression: beta smoke ───────────────────────────────────────────────
log "step 7/8 regression: beta smoke"
if [[ -f "${ROOT_DIR}/scripts/beta_smoke.py" ]]; then
  OMEGA_CONSOLE_BASE_URL="${CONSOLE_HEALTH_URL}" python3 "${ROOT_DIR}/scripts/beta_smoke.py" \
    || die "beta smoke regression failed."
else
  die "scripts/beta_smoke.py is missing; refusing to promote without a regression gate."
fi

# ── 8. Promote: record the candidate lock as current (atomic pointer) ───────
log "step 8/8 promote: recording the verified candidate as the current release"
[[ -s "${CURRENT_LOCK}" ]] && cp -f "${CURRENT_LOCK}" "${PREVIOUS_LOCK}"
# Atomic replace of the durable "which release is live" pointer.
cp -f "${CANDIDATE_LOCK}" "${CURRENT_LOCK}.tmp"
mv -f "${CURRENT_LOCK}.tmp" "${CURRENT_LOCK}"
PROMOTED=1
trap - ERR EXIT
log "PROMOTED ${TARGET_TAG} (${DEPLOY_REF}); previous digest set retained at ${PREVIOUS_LOCK}"
printf 'DAY2_RELEASE\tPASS\ttag=%s\tref=%s\tbackup=%s\n' "${TARGET_TAG}" "${DEPLOY_REF}" "${BACKUP_DIR}"
