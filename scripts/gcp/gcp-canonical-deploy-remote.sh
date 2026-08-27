#!/usr/bin/env bash
# On-VM half of the canonical GCP deploy. Runs as root, invoked by
# gcp-canonical-deploy.sh over IAP SSH. Fail-closed: a verified backup of BOTH
# databases is taken BEFORE any mutation, and any failure after that point rolls
# back to the previous release and restores the databases.
#
# It matches the live deploy mechanism exactly:
#   /opt/modecissions/releases/<ref>/  +  current symlink
#   compose = docker-compose.yml + docker-compose.gcp.yml + docker-compose.aws-images.gcp.yml
#   env     = /opt/modecissions/shared/infra.env
#
# First-run sensitive: it discovers the live postgres containers dynamically
# rather than assuming names, and never assumes an empty backup is healthy.
set -Eeuo pipefail
set +x
umask 077

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "must run as root (via sudo)" >&2; exit 10; }
for v in TARGET_TAG DEPLOY_REF OMEGA_PROJECT_ID ENVIRONMENT GHCR_OWNER GHCR_SECRET_VERSION SOURCE_BUCKET SOURCE_OBJECT IMAGES_OVERLAY; do
  [[ -n "${!v:-}" ]] || { echo "missing required env ${v}" >&2; exit 11; }
done

APP_ROOT="/opt/modecissions"
RELEASE_DIR="${APP_ROOT}/releases/${DEPLOY_REF}"
CURRENT="${APP_ROOT}/current"
SHARED_ENV="${APP_ROOT}/shared/infra.env"
BACKUP_ROOT="/var/lib/docker/omega-deploy-backups"   # persistent data disk
PREV_TARGET="$(readlink -f "${CURRENT}" 2>/dev/null || true)"

log() { printf '[remote-deploy] %s\n' "$*"; }
die() { printf '[remote-deploy] ERROR: %s\n' "$*" >&2; exit 1; }

# Resolve the live postgres containers by EXACT name, and require exactly one
# match each — a wrong or ambiguous match would produce a backup that is trusted
# as the safety net but is not the real database. Fail closed on 0 or >1.
find_one_container() {
  local name matches count
  name="$1"
  matches="$(docker ps --format '{{.Names}}' | grep -xE "${name}" || true)"
  count="$(printf '%s' "${matches}" | grep -c . || true)"
  [[ "${count}" -eq 1 ]] || return 1
  printf '%s' "${matches}"
}
main_pg="$(find_one_container 'mode_postgres')" \
  || die "expected exactly one running 'mode_postgres' container for the main-DB backup."
gold_pg="$(find_one_container 'mode_postgres_gold')" \
  || die "expected exactly one running 'mode_postgres_gold' container for the gold-DB backup."

sha256_of() { sha256sum -- "$1" | awk '{print $1}'; }

BACKUP_DIR="${BACKUP_ROOT}/${TARGET_TAG}-${DEPLOY_REF}-$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DONE=0
MUTATED=0
PROMOTED=0

# Atomic, isolated restore of one database from a checksum-verified dump.
# --single-transaction makes it all-or-nothing: a mid-restore error rolls the
# whole thing back, so the database is NEVER left partially dropped (no data
# loss). Backends are terminated first so the DROPs are not blocked by the app.
restore_db() {
  local container="$1" db="$2" port="$3" dump="$4" want_sha="$5"
  local pflag=(); [[ -n "${port}" ]] && pflag=(-p "${port}")
  if [[ "$(sha256_of "${dump}")" != "${want_sha}" ]]; then
    log "rollback: ${db} backup checksum mismatch — NOT restoring; investigate ${BACKUP_DIR}"; return 1
  fi
  docker exec "${container}" psql "${pflag[@]}" -U postgres -d "${db}" \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${db}' AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true
  if docker exec -i "${container}" psql --single-transaction -v ON_ERROR_STOP=1 "${pflag[@]}" \
      -U postgres -d "${db}" < "${dump}" >/dev/null; then
    log "rollback: ${db} restored"
  else
    log "rollback: ${db} restore FAILED (rolled back atomically; DB unchanged) — investigate ${BACKUP_DIR}"
  fi
}

rollback() {
  log "ROLLBACK: restoring databases, then the previous release"
  # Restore FIRST (with no competing writers) if migrations ran, THEN bring the
  # previous release up — so the restore never races the app.
  if [[ "${MUTATED}" -eq 1 && "${BACKUP_DONE}" -eq 1 ]]; then
    restore_db "${main_pg}" "${main_db}" ""     "${BACKUP_DIR}/${main_db}.sql" "${main_sha}"
    restore_db "${gold_pg}" "${gold_db}" "5433" "${BACKUP_DIR}/${gold_db}.sql" "${gold_sha}"
  fi
  if [[ -n "${PREV_TARGET}" && -d "${PREV_TARGET}" ]]; then
    ln -sfn "${PREV_TARGET}" "${CURRENT}"
    ( cd "${PREV_TARGET}" && compose_up ) || log "rollback: previous compose up reported an error"
  fi
}
on_err() {
  local rc=$?; trap - ERR EXIT
  [[ "${PROMOTED}" -eq 1 ]] && exit "${rc}"
  if [[ "${MUTATED:-0}" -eq 1 ]]; then
    log "diagnostics: airflow healthcheck log + container state before rollback"
    docker inspect -f 'airflow health={{.State.Health.Status}} running={{.State.Running}} restarts={{.RestartCount}} exit={{.State.ExitCode}}' mode_airflow 2>&1 | sed 's/^/[diag] /' || true
    # The actual healthcheck attempts (curl output + exit code) — the definitive reason.
    docker inspect --format '{{json .State.Health}}' mode_airflow 2>/dev/null | python3 -m json.tool 2>/dev/null | sed 's/^/[health-log] /' | tail -30 || true
    # Is /airflow/health actually answering from inside the container right now?
    docker exec mode_airflow sh -lc 'curl -s -o /dev/null -w "in-container /airflow/health = %{http_code}\n" --max-time 5 http://127.0.0.1:8080/airflow/health' 2>&1 | sed 's/^/[probe] /' || true
    docker logs --tail 15 mode_airflow 2>&1 | sed 's/^/[airflow] /' || true
  fi
  [[ "${BACKUP_DONE}" -eq 1 ]] && rollback || log "aborted before any mutation; nothing to roll back"
  exit "${rc}"
}
trap on_err ERR EXIT

main_db="modecissions"; gold_db="modecissions_gold"

compose_up() {
  docker compose --env-file infra/.env \
    -f infra/docker-compose.yml \
    -f infra/docker-compose.gcp.yml \
    -f infra/docker-compose.aws-images.gcp.yml \
    --profile sap up -d
}

# ── 1. Verified backup of BOTH databases (before any mutation) ──────────────
log "step 1 backup: ${main_db} (${main_pg}) + ${gold_db} (${gold_pg}) -> ${BACKUP_DIR}"
mkdir -p "${BACKUP_DIR}"; chmod 700 "${BACKUP_DIR}"
docker exec "${main_pg}" pg_dump --clean --if-exists -U postgres -d "${main_db}" > "${BACKUP_DIR}/${main_db}.sql"
docker exec "${gold_pg}" pg_dump --clean --if-exists -U postgres -p 5433 -d "${gold_db}" > "${BACKUP_DIR}/${gold_db}.sql"
[[ -s "${BACKUP_DIR}/${main_db}.sql" && -s "${BACKUP_DIR}/${gold_db}.sql" ]] || die "a backup dump is empty; refusing to proceed."
main_sha="$(sha256_of "${BACKUP_DIR}/${main_db}.sql")"; gold_sha="$(sha256_of "${BACKUP_DIR}/${gold_db}.sql")"
printf '%s\t%s\n%s\t%s\n' "${main_db}" "${main_sha}" "${gold_db}" "${gold_sha}" > "${BACKUP_DIR}/SHA256SUMS"
BACKUP_DONE=1
log "step 1 backup: OK (main=$(wc -c <"${BACKUP_DIR}/${main_db}.sql")B gold=$(wc -c <"${BACKUP_DIR}/${gold_db}.sql")B)"

# ── 2. Stage the new release dir (exact tarball + overlays + shared env) ─────
log "step 2 stage: releases/${DEPLOY_REF}"
if [[ ! -d "${RELEASE_DIR}" ]]; then
  token="$(curl -fsS -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')"
  obj="$(python3 - "${SOURCE_OBJECT}" <<'PY'
import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))
PY
)"
  curl -fL -H "Authorization: Bearer ${token}" \
    "https://storage.googleapis.com/storage/v1/b/${SOURCE_BUCKET}/o/${obj}?alt=media" -o /tmp/omega-src.tgz
  mkdir -p "${RELEASE_DIR}"
  tar -xzf /tmp/omega-src.tgz --strip-components=1 -C "${RELEASE_DIR}"
  rm -f /tmp/omega-src.tgz
fi
# Carry the environment overlay + shared env + tag-pinned image overlay into the
# NEW release dir. Never overwrite the currently-live release tree, so a dry-run
# that re-validates the live ref stays truly non-destructive.
if [[ "$(readlink -f "${RELEASE_DIR}")" != "${PREV_TARGET}" ]]; then
  cp -f "${CURRENT}/infra/docker-compose.gcp.yml" "${RELEASE_DIR}/infra/docker-compose.gcp.yml"
  cp -f "${SHARED_ENV}" "${RELEASE_DIR}/infra/.env"
  chmod 600 "${RELEASE_DIR}/infra/.env"
  cp -f "${IMAGES_OVERLAY}" "${RELEASE_DIR}/infra/docker-compose.aws-images.gcp.yml"
  # Replicate the host-side dir setup the Terraform startup script performs:
  # airflow runs as uid 50000 and its 'processor' logging handler writes under
  # airflow/logs, so these dirs must be owned by 50000 or airflow crash-loops on
  # startup ("Unable to configure handler 'processor'").
  mkdir -p "${RELEASE_DIR}/data/lakehouse"
  mkdir -p "${RELEASE_DIR}/airflow/dags" "${RELEASE_DIR}/airflow/logs/scheduler" "${RELEASE_DIR}/airflow/plugins"
  chown -R 50000:0 "${RELEASE_DIR}/airflow/dags" "${RELEASE_DIR}/airflow/logs" "${RELEASE_DIR}/airflow/plugins"
  chmod -R 775 "${RELEASE_DIR}/airflow/dags" "${RELEASE_DIR}/airflow/logs" "${RELEASE_DIR}/airflow/plugins"
else
  log "step 2 stage: target ref is the live release; leaving its tree untouched"
fi

# ── 3. Authenticated pull of the 15 release digests (server-owned) ───────────
# Publish-only releases push by immutable digest only, so pull the exact digests
# the operator pinned into the images overlay (single source of truth), not a
# vX.Y.Z tag that GHCR never received.
log "step 3 images: authenticated pull of the 15 release digests"
DIGEST_REFS=()
while IFS= read -r _ref; do
  [[ -n "${_ref}" ]] && DIGEST_REFS+=("${_ref}")
done < <(grep -oE 'ghcr\.io/[^[:space:]"]+@sha256:[0-9a-f]{64}' "${IMAGES_OVERLAY}" | sort -u)
[[ "${#DIGEST_REFS[@]}" -eq 15 ]] || die "expected 15 image digests in the overlay, found ${#DIGEST_REFS[@]}."
pull_all() { for ref in "${DIGEST_REFS[@]}"; do docker pull --quiet "${ref}" >/dev/null || return 1; done; }
OMEGA_GCP_ENVIRONMENT="${ENVIRONMENT}" OMEGA_GHCR_PULL_SECRET_VERSION="${GHCR_SECRET_VERSION}" \
  bash "${RELEASE_DIR}/infra/terraform-gcp/release/ghcr-auth-run.sh" bash -c "$(declare -f pull_all); DIGEST_REFS=(${DIGEST_REFS[*]}); pull_all" \
  || die "authenticated image pull failed (15/15 required)."

# Dry-run stops here: everything so far (backup, stage, pull) is non-destructive
# to the running services and databases. This validates the risky live-specific
# assumptions (container discovery, backup non-empty, tarball fetch, auth pull)
# WITHOUT migrating the database or swapping the running release.
if [[ "${DEPLOY_MODE:-apply}" == "dryrun" ]]; then
  trap - ERR EXIT
  log "DRY-RUN OK: backup verified, release staged, 15/15 images pulled. No DB/service mutation."
  printf 'REMOTE_DEPLOY\tDRYRUN_PASS\ttag=%s\tref=%s\tbackup=%s\n' "${TARGET_TAG}" "${DEPLOY_REF}" "${BACKUP_DIR}"
  exit 0
fi

# ── 4. Deploy the candidate release (compose up by pinned tag) ──────────────
# Bring the NEW release up BEFORE migrating: postgres bind-mounts ./init and
# ./init_gold RELATIVE to the release dir, so a plain up -d from the new release
# recreates the DB containers with the new migration files under
# /docker-entrypoint-initdb.d/. Migrating first (against the old release's mount)
# fails `\i` with "No such file" on any newly-added migration. cwd MUST be the
# release ROOT (compose_up uses infra/-relative -f paths).
MUTATED=1   # from here the DB/runtime changes and a failure triggers rollback

wait_db_healthy() {
  local c="$1" i
  for i in $(seq 1 60); do
    [[ "$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || true)" == "healthy" ]] && return 0
    sleep 3
  done
  return 1
}

# ── 4. Recreate ONLY the databases with the new release mount ───────────────
# Bring up just postgres/postgres_gold first (they bind-mount ./init and
# ./init_gold relative to the release dir, so this recreates them with the new
# migration files). Migrating BEFORE the app/airflow come up means the schema is
# ready when they start — otherwise app containers fail their healthchecks and
# `up -d` aborts on the service_healthy dependency.
log "step 4 databases: recreate postgres with the new init mount"
( cd "${RELEASE_DIR}" && docker compose --env-file infra/.env \
    -f infra/docker-compose.yml -f infra/docker-compose.gcp.yml \
    -f infra/docker-compose.aws-images.gcp.yml --profile sap \
    up -d --no-deps postgres postgres_gold )
wait_db_healthy "${main_pg}" || die "main postgres did not become healthy after recreate."
wait_db_healthy "${gold_pg}" || die "gold postgres did not become healthy after recreate."

# ── 5. Forward-only migrations with drift guard (Checkpoint 5.5) ─────────────
log "step 5 migrations: forward-only ledger + drift guard"
( cd "${RELEASE_DIR}" && bash scripts/apply_db_migrations.sh )

# ── 5b. Deploy the full candidate stack (schema now migrated) ───────────────
# Some 209 images (notably airflow) boot slower than their healthcheck start
# window, so `up -d` can transiently abort on a `depends_on: service_healthy`
# edge even though the service reaches health seconds later. Retry: aborted
# `up -d` leaves the slow service running, so on the next pass it is healthy and
# compose converges. Each failed attempt already waited out a healthcheck cycle.
log "step 5b deploy: compose up full stack ${TARGET_TAG}"
up_ok=0
for attempt in 1 2 3 4 5; do
  if ( cd "${RELEASE_DIR}" && compose_up ); then up_ok=1; break; fi
  log "step 5b: compose up attempt ${attempt} did not settle (slow-booting service); waiting 30s and retrying"
  sleep 30
done
[[ "${up_ok}" -eq 1 ]] || die "compose up did not settle after retries."

# ── 6. Health gate: version + app_env + readiness ───────────────────────────
log "step 6 health: /healthz version+app_env and /readyz"
EXPECTED_VERSION="${TARGET_TAG#v}"
ok=0
for _ in $(seq 1 60); do
  hz="$(curl -fsS --max-time 5 http://127.0.0.1:8000/healthz 2>/dev/null || true)"
  rz="$(curl -o /dev/null -s -w '%{http_code}' --max-time 8 http://127.0.0.1:8000/readyz 2>/dev/null || true)"
  vok="$(printf '%s' "${hz}" | VER="${EXPECTED_VERSION}" python3 -c 'import json,os,sys
try: p=json.load(sys.stdin)
except Exception: sys.exit(1)
sys.exit(0 if p.get("version")==os.environ["VER"] and p.get("app_env")=="production" else 1)' && echo yes || echo no)"
  if [[ "${vok}" == "yes" && "${rz}" == "200" ]]; then ok=1; log "health green (version=${EXPECTED_VERSION}, readyz=200)"; break; fi
  sleep 5
done
[[ "${ok}" -eq 1 ]] || die "health gate never went green for ${EXPECTED_VERSION}."

# ── 7. Promote (atomic pointer) ─────────────────────────────────────────────
log "step 7 promote: current -> releases/${DEPLOY_REF}"
ln -sfn "${RELEASE_DIR}" "${CURRENT}"
date -Iseconds > "${APP_ROOT}/DEPLOYED"
PROMOTED=1
trap - ERR EXIT
log "PROMOTED ${TARGET_TAG} (${DEPLOY_REF}); backup retained at ${BACKUP_DIR}"
printf 'REMOTE_DEPLOY\tPASS\ttag=%s\tref=%s\tbackup=%s\n' "${TARGET_TAG}" "${DEPLOY_REF}" "${BACKUP_DIR}"
