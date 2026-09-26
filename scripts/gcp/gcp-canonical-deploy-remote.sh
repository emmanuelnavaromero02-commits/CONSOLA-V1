#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "must run as root (via sudo)" >&2; exit 10; }
for v in TARGET_TAG DEPLOY_REF OMEGA_PROJECT_ID ENVIRONMENT GHCR_OWNER GHCR_SECRET_VERSION SOURCE_BUCKET SOURCE_OBJECT SOURCE_SHA256 SOURCE_GENERATION IMAGES_OVERLAY IMAGES_OVERLAY_SHA256; do
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

[[ "${TARGET_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] \
  || die "target release tag is invalid."
[[ "${DEPLOY_REF}" =~ ^[0-9a-f]{40}$ ]] || die "source commit identity is invalid."
[[ "${OMEGA_PROJECT_ID}" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] \
  || die "GCP project identity is invalid."
[[ "${GHCR_OWNER}" =~ ^[a-z0-9]([a-z0-9-]{0,37}[a-z0-9])?$ ]] \
  || die "GHCR owner is invalid."
[[ "${GHCR_SECRET_VERSION}" =~ ^[1-9][0-9]*$ ]] || die "GHCR secret version is invalid."
[[ "${SOURCE_BUCKET}" =~ ^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$ ]] \
  || die "source bucket is invalid."
[[ "${SOURCE_OBJECT}" == "CONSOLA-V1-${DEPLOY_REF}.tar.gz" ]] \
  || die "source object is not bound to the requested commit."
[[ "${SOURCE_SHA256}" =~ ^[0-9a-f]{64}$ ]] || die "source checksum is invalid."
[[ "${SOURCE_GENERATION}" =~ ^[1-9][0-9]*$ ]] || die "source generation is invalid."
[[ "${IMAGES_OVERLAY_SHA256}" =~ ^[0-9a-f]{64}$ ]] || die "image overlay checksum is invalid."
[[ "${ENVIRONMENT}" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || die "environment is invalid."
DEPLOY_MODE="${DEPLOY_MODE:-apply}"
[[ "${DEPLOY_MODE}" == "apply" || "${DEPLOY_MODE}" == "dryrun" ]] \
  || die "deploy mode is invalid."
IMAGE_PULL_MIN_FREE_GIB="${IMAGE_PULL_MIN_FREE_GIB:-20}"
[[ "${IMAGE_PULL_MIN_FREE_GIB}" =~ ^[1-9][0-9]*$ \
      && "${IMAGE_PULL_MIN_FREE_GIB}" -ge 5 \
      && "${IMAGE_PULL_MIN_FREE_GIB}" -le 1024 ]] \
  || die "image pull free-space margin must be an integer from 5 through 1024 GiB."
[[ "${IMAGES_OVERLAY}" =~ ^/tmp/omega-images-${DEPLOY_REF}-[0-9a-f]{32}\.yml$ ]] \
  || die "image overlay path is invalid."
[[ -f "${IMAGES_OVERLAY}" && ! -L "${IMAGES_OVERLAY}" ]] \
  || die "image overlay is missing or is not a regular staged file."

command -v flock >/dev/null 2>&1 || die "flock is required for the canonical deploy lock."
exec 8>"${APP_ROOT}/.gcp-canonical-deploy.lock"
flock -n 8 || die "another canonical GCP deploy is already running."

sha256_of() { sha256sum -- "$1" | awk '{print $1}'; }

TRUSTED_IMAGES_OVERLAY=""
trap '[[ -z "${TRUSTED_IMAGES_OVERLAY:-}" ]] || rm -f -- "${TRUSTED_IMAGES_OVERLAY}"' EXIT
TRUSTED_IMAGES_OVERLAY="$(mktemp "${APP_ROOT}/.omega-images-${DEPLOY_REF}.XXXXXX.yml")"
cp -- "${IMAGES_OVERLAY}" "${TRUSTED_IMAGES_OVERLAY}"
chmod 0400 "${TRUSTED_IMAGES_OVERLAY}"
[[ "$(sha256_of "${TRUSTED_IMAGES_OVERLAY}")" == "${IMAGES_OVERLAY_SHA256}" ]] \
  || die "image overlay checksum differs from the operator-verified digest lock."

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

BACKUP_DIR="${BACKUP_ROOT}/${TARGET_TAG}-${DEPLOY_REF}-$(date -u +%Y%m%dT%H%M%SZ)"
main_db="modecissions"; gold_db="modecissions_gold"
BACKUP_DONE=0
MUTATED=0
PROMOTED=0
QUIESCED=0
MAIN_DB_FENCED=0
GOLD_DB_FENCED=0
MAIN_DB_CONNECTION_LIMIT=""
GOLD_DB_CONNECTION_LIMIT=""
CANDIDATE_ENV_CHANGED=0
SHARED_ENV_CHANGED=0
CANDIDATE_ENV="${RELEASE_DIR}/infra/.env"
CANDIDATE_ENV_BACKUP=""
SHARED_ENV_BACKUP=""
SOURCE_ARCHIVE_TMP=""
RELEASE_STAGE_TMP=""
RENDERED_GCP_OVERLAY_TMP=""

cleanup_preflight_artifacts() {
  if [[ -n "${TRUSTED_IMAGES_OVERLAY}" \
        && "${TRUSTED_IMAGES_OVERLAY}" == "${APP_ROOT}/.omega-images-${DEPLOY_REF}."* ]]; then
    rm -f -- "${TRUSTED_IMAGES_OVERLAY}"
    TRUSTED_IMAGES_OVERLAY=""
  fi
  if [[ -n "${SOURCE_ARCHIVE_TMP}" \
        && "${SOURCE_ARCHIVE_TMP}" == "${APP_ROOT}/releases/.omega-source-"* ]]; then
    rm -f -- "${SOURCE_ARCHIVE_TMP}"
    SOURCE_ARCHIVE_TMP=""
  fi
  if [[ -n "${RELEASE_STAGE_TMP}" \
        && "${RELEASE_STAGE_TMP}" == "${APP_ROOT}/releases/.${DEPLOY_REF}.stage."* ]]; then
    rm -rf -- "${RELEASE_STAGE_TMP}"
    RELEASE_STAGE_TMP=""
  fi
  if [[ -n "${RENDERED_GCP_OVERLAY_TMP}" \
        && "${RENDERED_GCP_OVERLAY_TMP}" == "${APP_ROOT}/.docker-compose.gcp.${DEPLOY_REF}."* ]]; then
    rm -f -- "${RENDERED_GCP_OVERLAY_TMP}"
    RENDERED_GCP_OVERLAY_TMP=""
  fi
}

replace_file_atomic() {
  local source="$1" target="$2" target_dir temporary
  target_dir="$(dirname -- "${target}")"
  mkdir -p -- "${target_dir}"
  temporary="$(mktemp "${target_dir}/.omega-env-restore.XXXXXX")"
  cp -- "${source}" "${temporary}"
  chmod 600 "${temporary}"
  mv -f -- "${temporary}" "${target}"
}

replace_file_verified() {
  local source="$1" target="$2" expected_sha="$3" target_dir temporary
  target_dir="$(dirname -- "${target}")"
  temporary="$(mktemp "${target_dir}/.omega-overlay.XXXXXX")"
  if ! cp -- "${source}" "${temporary}" \
      || ! chmod 0600 "${temporary}" \
      || [[ "$(sha256_of "${temporary}")" != "${expected_sha}" ]]; then
    rm -f -- "${temporary}"
    return 1
  fi
  if ! mv -f -- "${temporary}" "${target}"; then
    rm -f -- "${temporary}"
    return 1
  fi
  [[ -f "${target}" && ! -L "${target}" \
        && "$(sha256_of "${target}")" == "${expected_sha}" ]]
}

restore_environment() {
  local failed=0
  if [[ "${SHARED_ENV_CHANGED}" -eq 1 && -n "${SHARED_ENV_BACKUP}" && -f "${SHARED_ENV_BACKUP}" ]]; then
    if replace_file_atomic "${SHARED_ENV_BACKUP}" "${SHARED_ENV}"; then
      log "rollback: shared runtime environment restored"
    else
      failed=1
    fi
  fi
  if [[ "${CANDIDATE_ENV_CHANGED}" -eq 1 && -n "${CANDIDATE_ENV_BACKUP}" && -f "${CANDIDATE_ENV_BACKUP}" ]]; then
    if replace_file_atomic "${CANDIDATE_ENV_BACKUP}" "${CANDIDATE_ENV}"; then
      log "rollback: candidate runtime environment restored"
    else
      failed=1
    fi
  fi
  [[ "${failed}" -eq 0 ]]
}

compose_up() {
  docker compose --env-file infra/.env \
    -f infra/docker-compose.yml \
    -f infra/docker-compose.gcp.yml \
    -f infra/docker-compose.aws-images.gcp.yml \
    --profile sap up -d
}

compose_databases_up() {
  docker compose --env-file infra/.env \
    -f infra/docker-compose.yml \
    -f infra/docker-compose.gcp.yml \
    -f infra/docker-compose.aws-images.gcp.yml \
    --profile sap up -d --no-deps postgres postgres_gold
}

# Interpolating render of the candidate release against a private copy of its
# env (plus placeholders for names hydration will supply). Reports variable
# names only; returns 1 when required variables are missing, 2 on other errors.
required_env_preflight() {
  local release_dir="$1" env_file="$2" probe probe_err name missing="" _attempt
  shift 2
  REQUIRED_ENV_MISSING=""
  probe="$(mktemp "${BACKUP_DIR}/.required-env-probe.XXXXXX")" || return 2
  probe_err="${probe}.err"
  if ! cp -- "${env_file}" "${probe}"; then
    rm -f -- "${probe}"
    return 2
  fi
  chmod 600 "${probe}"
  for name in "$@"; do
    printf '\n%s=omega-preflight-placeholder\n' "${name}" >> "${probe}"
  done
  for _attempt in $(seq 1 64); do
    if ( cd "${release_dir}" && docker compose --env-file "${probe}" \
          -f infra/docker-compose.yml \
          -f infra/docker-compose.gcp.yml \
          -f infra/docker-compose.aws-images.gcp.yml \
          --profile sap config --quiet ) >/dev/null 2>"${probe_err}"; then
      rm -f -- "${probe}" "${probe_err}"
      REQUIRED_ENV_MISSING="${missing}"
      [[ -z "${missing}" ]]
      return
    fi
    name="$(sed -n 's/.*required variable \([A-Za-z_][A-Za-z0-9_]*\) is missing a value.*/\1/p' "${probe_err}" | head -n 1)"
    if [[ -z "${name}" || ",${missing}," == *",${name},"* ]]; then
      rm -f -- "${probe}" "${probe_err}"
      REQUIRED_ENV_MISSING="${missing}"
      return 2
    fi
    missing="${missing:+${missing},}${name}"
    printf '\n%s=omega-preflight-placeholder\n' "${name}" >> "${probe}"
  done
  rm -f -- "${probe}" "${probe_err}"
  REQUIRED_ENV_MISSING="${missing}"
  return 1
}

running_writer_containers() {
  docker ps --format '{{.Names}}' \
    | awk '/^(mode_|omega_)/ && $0 != "mode_postgres" && $0 != "mode_postgres_gold"'
}

stop_all_writers() {
  local discovered remaining name
  local writers=()
  discovered="$(running_writer_containers)" || return 1
  while IFS= read -r name; do
    [[ -n "${name}" ]] && writers+=("${name}")
  done <<< "${discovered}"

  QUIESCED=1
  if [[ "${#writers[@]}" -gt 0 ]]; then
    log "quiesce: stopping ${#writers[@]} Omega writer containers"
    docker stop --time 45 "${writers[@]}" >/dev/null || return 1
  fi
  remaining="$(running_writer_containers)" || return 1
  [[ -z "${remaining}" ]] || {
    log "quiesce: writer containers still running: ${remaining//$'\n'/, }"
    return 1
  }
  log "quiesce: all application, scheduler, connector and object-store writers are stopped"
}

database_connection_limit() {
  local container="$1" db="$2" port="$3"
  local pflag=(); [[ -n "${port}" ]] && pflag=(-p "${port}")
  docker exec "${container}" psql "${pflag[@]}" -U postgres -d postgres -Atqc \
    "SELECT datconnlimit FROM pg_database WHERE datname='${db}';"
}

assert_zero_competing_sessions() {
  local main_sessions gold_sessions
  main_sessions="$(docker exec "${main_pg}" psql -U postgres -d postgres -Atqc \
    "SELECT count(*) FROM pg_stat_activity WHERE datname='${main_db}';")"
  gold_sessions="$(docker exec "${gold_pg}" psql -p 5433 -U postgres -d postgres -Atqc \
    "SELECT count(*) FROM pg_stat_activity WHERE datname='${gold_db}';")"
  [[ "${main_sessions}" == "0" && "${gold_sessions}" == "0" ]] || {
    log "quiesce: competing DB sessions detected (main=${main_sessions}, gold=${gold_sessions})"
    return 1
  }
}

fence_databases() {
  if [[ "${MAIN_DB_FENCED}" -eq 0 ]]; then
    MAIN_DB_CONNECTION_LIMIT="$(database_connection_limit "${main_pg}" "${main_db}" "")"
    [[ "${MAIN_DB_CONNECTION_LIMIT}" =~ ^-?[0-9]+$ ]] \
      || { log "quiesce: invalid main database connection limit"; return 1; }
    docker exec "${main_pg}" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
      -c "ALTER DATABASE ${main_db} CONNECTION LIMIT 0;" >/dev/null || return 1
    MAIN_DB_FENCED=1
  fi
  if [[ "${GOLD_DB_FENCED}" -eq 0 ]]; then
    GOLD_DB_CONNECTION_LIMIT="$(database_connection_limit "${gold_pg}" "${gold_db}" "5433")"
    [[ "${GOLD_DB_CONNECTION_LIMIT}" =~ ^-?[0-9]+$ ]] \
      || { log "quiesce: invalid gold database connection limit"; return 1; }
    docker exec "${gold_pg}" psql -p 5433 -v ON_ERROR_STOP=1 -U postgres -d postgres \
      -c "ALTER DATABASE ${gold_db} CONNECTION LIMIT 0;" >/dev/null || return 1
    GOLD_DB_FENCED=1
  fi

  docker exec "${main_pg}" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${main_db}';" >/dev/null \
    || return 1
  docker exec "${gold_pg}" psql -p 5433 -v ON_ERROR_STOP=1 -U postgres -d postgres \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${gold_db}';" >/dev/null \
    || return 1
  assert_zero_competing_sessions || return 1
  log "quiesce: database connection fences active and competing sessions=0"
}

restore_database_access() {
  local failed=0
  if [[ "${MAIN_DB_FENCED}" -eq 1 ]]; then
    if docker exec "${main_pg}" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
        -c "ALTER DATABASE ${main_db} CONNECTION LIMIT ${MAIN_DB_CONNECTION_LIMIT};" >/dev/null; then
      MAIN_DB_FENCED=0
    else
      failed=1
    fi
  fi
  if [[ "${GOLD_DB_FENCED}" -eq 1 ]]; then
    if docker exec "${gold_pg}" psql -p 5433 -v ON_ERROR_STOP=1 -U postgres -d postgres \
        -c "ALTER DATABASE ${gold_db} CONNECTION LIMIT ${GOLD_DB_CONNECTION_LIMIT};" >/dev/null; then
      GOLD_DB_FENCED=0
    else
      failed=1
    fi
  fi
  [[ "${failed}" -eq 0 ]]
}

wait_db_healthy() {
  local c="$1" i
  for i in $(seq 1 60); do
    [[ "$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || true)" == "healthy" ]] && return 0
    sleep 3
  done
  return 1
}

resume_previous_release() {
  [[ -n "${PREV_TARGET}" && -d "${PREV_TARGET}" ]] || return 1
  ln -sfn "${PREV_TARGET}" "${CURRENT}"
  ( cd "${PREV_TARGET}" && compose_databases_up ) || return 1
  wait_db_healthy "${main_pg}" || return 1
  wait_db_healthy "${gold_pg}" || return 1
  restore_database_access || return 1
  ( cd "${PREV_TARGET}" && compose_up )
}

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
    return 1
  fi
}

contain_blocked_recovery() {
  local reason="$1" containment_ok=1
  log "rollback: ${reason}; re-establishing fail-closed containment"
  stop_all_writers \
    || { log "rollback BLOCKED: unable to confirm every writer stopped"; containment_ok=0; }
  fence_databases \
    || { log "rollback BLOCKED: unable to confirm database fences"; containment_ok=0; }
  if [[ "${containment_ok}" -eq 1 ]]; then
    log "rollback BLOCKED: writers stopped and database fences active; manual recovery required"
  else
    log "rollback BLOCKED: containment could not be fully verified; emergency manual recovery required"
  fi
  return 0
}

rollback() {
  local exclusive_ok=1 restore_ok=1 environment_ok=1
  log "ROLLBACK: stopping the candidate, restoring databases, then starting the previous release"
  stop_all_writers || { log "rollback: failed to stop every candidate writer"; exclusive_ok=0; }
  if [[ "${MUTATED}" -eq 1 ]]; then
    if [[ "${BACKUP_DONE}" -eq 1 ]]; then
      if [[ "${exclusive_ok}" -eq 1 ]]; then
        fence_databases \
          || { log "rollback: could not prove an exclusive database restore window"; exclusive_ok=0; }
      fi
      if [[ "${exclusive_ok}" -eq 1 ]]; then
        restore_db "${main_pg}" "${main_db}" ""     "${BACKUP_DIR}/${main_db}.sql" "${main_sha}" \
          || restore_ok=0
        restore_db "${gold_pg}" "${gold_db}" "5433" "${BACKUP_DIR}/${gold_db}.sql" "${gold_sha}" \
          || restore_ok=0
      else
        restore_ok=0
      fi
    else
      restore_ok=0
    fi
  fi
  restore_environment || environment_ok=0

  if [[ "${exclusive_ok}" -eq 1 && "${restore_ok}" -eq 1 && "${environment_ok}" -eq 1 \
        && -n "${PREV_TARGET}" && -d "${PREV_TARGET}" ]]; then
    if ! resume_previous_release; then
      contain_blocked_recovery "previous release resume failed or was partial"
    fi
  else
    contain_blocked_recovery "recovery prerequisites were not verified"
  fi
  return 0
}
on_err() {
  local rc=$?; trap - ERR EXIT
  cleanup_preflight_artifacts
  [[ "${PROMOTED}" -eq 1 ]] && exit "${rc}"
  if [[ "${MUTATED:-0}" -eq 1 ]]; then
    log "diagnostics: airflow healthcheck log + container state before rollback"
    docker inspect -f 'airflow health={{.State.Health.Status}} running={{.State.Running}} restarts={{.RestartCount}} exit={{.State.ExitCode}}' mode_airflow 2>&1 | sed 's/^/[diag] /' || true
    docker inspect --format '{{json .State.Health}}' mode_airflow 2>/dev/null | python3 -m json.tool 2>/dev/null | sed 's/^/[health-log] /' | tail -30 || true
    docker exec mode_airflow sh -lc 'curl -s -o /dev/null -w "in-container /airflow/health = %{http_code}\n" --max-time 5 http://127.0.0.1:8080/airflow/health' 2>&1 | sed 's/^/[probe] /' || true
    docker logs --tail 15 mode_airflow 2>&1 | sed 's/^/[airflow] /' || true
  fi
  if [[ "${MUTATED:-0}" -eq 1 || "${QUIESCED:-0}" -eq 1 ]]; then
    rollback
  elif [[ "${CANDIDATE_ENV_CHANGED:-0}" -eq 1 || "${SHARED_ENV_CHANGED:-0}" -eq 1 ]]; then
    restore_environment
    log "aborted before service/database mutation; runtime left running"
  else
    log "aborted before any mutation; nothing to roll back"
  fi
  exit "${rc}"
}
trap on_err ERR EXIT

mkdir -p "${BACKUP_DIR}"; chmod 700 "${BACKUP_DIR}"
if [[ -f "${SHARED_ENV}" ]]; then
  SHARED_ENV_BACKUP="${BACKUP_DIR}/infra.env.shared.before"
  cp -- "${SHARED_ENV}" "${SHARED_ENV_BACKUP}"
  chmod 600 "${SHARED_ENV_BACKUP}"
fi
log "preflight stage: releases/${DEPLOY_REF}"
[[ ! -L "${RELEASE_DIR}" ]] || die "release path must not be a symbolic link."
[[ ! -e "${RELEASE_DIR}" || -d "${RELEASE_DIR}" ]] \
  || die "release path exists but is not a directory."
SOURCE_RECEIPT="${RELEASE_DIR}/.omega-source-artifact-v1"
source_receipt_matches() {
  [[ -f "${SOURCE_RECEIPT}" && ! -L "${SOURCE_RECEIPT}" ]] || return 1
  printf 'omega-source-artifact-v1\t%s\t%s\t%s\n' \
    "${SOURCE_OBJECT}" "${SOURCE_GENERATION}" "${SOURCE_SHA256}" \
    | cmp -s - "${SOURCE_RECEIPT}"
}

if [[ -d "${RELEASE_DIR}" ]]; then
  source_receipt_matches \
    || die "staged release is not bound to this source object generation and checksum."
else
  token="$(curl -fsS -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' \
    | python3 -I -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')"
  obj="$(python3 -I - "${SOURCE_OBJECT}" <<'PY'
import sys
import urllib.parse

print(urllib.parse.quote(sys.argv[1], safe=""))
PY
)"
  mkdir -p "${APP_ROOT}/releases"
  SOURCE_ARCHIVE_TMP="$(mktemp "${APP_ROOT}/releases/.omega-source-XXXXXX.tar.gz")"
  RELEASE_STAGE_TMP="$(mktemp -d "${APP_ROOT}/releases/.${DEPLOY_REF}.stage.XXXXXX")"
  curl -fL -H "Authorization: Bearer ${token}" \
    "https://storage.googleapis.com/storage/v1/b/${SOURCE_BUCKET}/o/${obj}?alt=media&generation=${SOURCE_GENERATION}" \
    -o "${SOURCE_ARCHIVE_TMP}"
  [[ "$(sha256_of "${SOURCE_ARCHIVE_TMP}")" == "${SOURCE_SHA256}" ]] \
    || die "downloaded source archive checksum does not match the operator-verified artifact."
  tar -xzf "${SOURCE_ARCHIVE_TMP}" --strip-components=1 -C "${RELEASE_STAGE_TMP}"
  [[ ! -e "${RELEASE_STAGE_TMP}/.omega-source-artifact-v1" \
        && ! -L "${RELEASE_STAGE_TMP}/.omega-source-artifact-v1" ]] \
    || die "source archive contains a reserved deployment receipt path."
  printf 'omega-source-artifact-v1\t%s\t%s\t%s\n' \
    "${SOURCE_OBJECT}" "${SOURCE_GENERATION}" "${SOURCE_SHA256}" \
    > "${RELEASE_STAGE_TMP}/.omega-source-artifact-v1"
  chmod 600 "${RELEASE_STAGE_TMP}/.omega-source-artifact-v1"
  mv -- "${RELEASE_STAGE_TMP}" "${RELEASE_DIR}"
  RELEASE_STAGE_TMP=""
  rm -f -- "${SOURCE_ARCHIVE_TMP}"
  SOURCE_ARCHIVE_TMP=""
  source_receipt_matches || die "source artifact receipt verification failed after stage."
fi
CANDIDATE_IMAGES_OVERLAY="${RELEASE_DIR}/infra/docker-compose.aws-images.gcp.yml"
CANDIDATE_GCP_OVERLAY="${RELEASE_DIR}/infra/docker-compose.gcp.yml"
GCP_OVERLAY_TEMPLATE="${RELEASE_DIR}/infra/terraform-gcp/templates/docker-compose.gcp.yml.tftpl"
GCP_OVERLAY_RENDERER="${RELEASE_DIR}/scripts/gcp/render_gcp_compose_override.py"
[[ -f "${GCP_OVERLAY_RENDERER}" && ! -L "${GCP_OVERLAY_RENDERER}" ]] \
  || die "verified release is missing the GCP overlay renderer."
if [[ "$(readlink -f "${RELEASE_DIR}")" != "${PREV_TARGET}" ]]; then
  cp -f "${SHARED_ENV}" "${RELEASE_DIR}/infra/.env"
  chmod 600 "${RELEASE_DIR}/infra/.env"
  python3 -I "${GCP_OVERLAY_RENDERER}" \
    "${GCP_OVERLAY_TEMPLATE}" "${CANDIDATE_GCP_OVERLAY}" \
    || die "candidate GCP overlay render failed."
  replace_file_verified \
    "${TRUSTED_IMAGES_OVERLAY}" \
    "${CANDIDATE_IMAGES_OVERLAY}" \
    "${IMAGES_OVERLAY_SHA256}" \
    || die "candidate image overlay copy failed checksum verification."
  mkdir -p "${RELEASE_DIR}/data/lakehouse"
  chown -R 10001:10001 "${RELEASE_DIR}/data/lakehouse"
  mkdir -p "${RELEASE_DIR}/airflow/dags" "${RELEASE_DIR}/airflow/logs/scheduler" "${RELEASE_DIR}/airflow/plugins"
  chown -R 50000:0 "${RELEASE_DIR}/airflow/dags" "${RELEASE_DIR}/airflow/logs" "${RELEASE_DIR}/airflow/plugins"
  chmod -R 775 "${RELEASE_DIR}/airflow/dags" "${RELEASE_DIR}/airflow/logs" "${RELEASE_DIR}/airflow/plugins"
else
  RENDERED_GCP_OVERLAY_TMP="$(mktemp "${APP_ROOT}/.docker-compose.gcp.${DEPLOY_REF}.XXXXXX")"
  python3 -I "${GCP_OVERLAY_RENDERER}" \
    "${GCP_OVERLAY_TEMPLATE}" "${RENDERED_GCP_OVERLAY_TMP}" \
    || die "live-ref GCP overlay verification render failed."
  cmp -s "${RENDERED_GCP_OVERLAY_TMP}" "${CANDIDATE_GCP_OVERLAY}" \
    || die "live release GCP overlay differs from its verified source template."
  rm -f -- "${RENDERED_GCP_OVERLAY_TMP}"
  RENDERED_GCP_OVERLAY_TMP=""
  log "preflight stage: target ref is live and its generated GCP overlay is exact"
fi
[[ -f "${CANDIDATE_GCP_OVERLAY}" && ! -L "${CANDIDATE_GCP_OVERLAY}" ]] \
  || die "candidate GCP overlay is missing or is not a regular file."
[[ -f "${CANDIDATE_IMAGES_OVERLAY}" && ! -L "${CANDIDATE_IMAGES_OVERLAY}" \
      && "$(sha256_of "${CANDIDATE_IMAGES_OVERLAY}")" == "${IMAGES_OVERLAY_SHA256}" ]] \
  || die "the image overlay actually used by Compose is not the operator-verified digest lock."
rm -f -- "${TRUSTED_IMAGES_OVERLAY}"
TRUSTED_IMAGES_OVERLAY=""

( cd "${RELEASE_DIR}" && docker compose --env-file infra/.env \
    -f infra/docker-compose.yml \
    -f infra/docker-compose.gcp.yml \
    -f infra/docker-compose.aws-images.gcp.yml \
    --profile sap config --quiet --no-interpolate ) \
  || die "candidate Compose configuration is invalid."

[[ -f "${CANDIDATE_ENV}" ]] || die "candidate infra/.env is missing after stage."
if [[ "${DEPLOY_MODE:-apply}" == "dryrun" ]]; then
  OMEGA_GCP_PROJECT_ID="${OMEGA_PROJECT_ID}" \
  OMEGA_GCP_SECRET_PREFIX="omega-${ENVIRONMENT}-" \
  OMEGA_SECRET_HYDRATION_MODE=check \
    bash "${RELEASE_DIR}/infra/terraform-gcp/release/hydrate-runtime-secrets.sh"
  log "preflight config: runtime secrets validated without changing the environment"
else
  CANDIDATE_ENV_BACKUP="${BACKUP_DIR}/infra.env.candidate.before"
  cp -- "${CANDIDATE_ENV}" "${CANDIDATE_ENV_BACKUP}"
  chmod 600 "${CANDIDATE_ENV_BACKUP}"
  CANDIDATE_ENV_CHANGED=1
  OMEGA_GCP_PROJECT_ID="${OMEGA_PROJECT_ID}" \
  OMEGA_GCP_SECRET_PREFIX="omega-${ENVIRONMENT}-" \
  OMEGA_ENV_FILE="${CANDIDATE_ENV}" \
    bash "${RELEASE_DIR}/infra/terraform-gcp/release/hydrate-runtime-secrets.sh"
  log "preflight config: runtime secrets hydrated atomically"
fi

# Names hydrate-runtime-secrets.sh writes from Secret Manager; a dry run only
# validates them, so the render treats them as supplied.
HYDRATED_ENV_NAMES=(
  CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID
  CONTROL_ROOM_EVIDENCE_SIGNING_KEY
  CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS
  GCS_ACCESS_KEY_ID
  GCS_SECRET_ACCESS_KEY
)
PREFLIGHT_PLACEHOLDERS=()
[[ "${DEPLOY_MODE:-apply}" != "dryrun" ]] || PREFLIGHT_PLACEHOLDERS=("${HYDRATED_ENV_NAMES[@]}")
preflight_rc=0
required_env_preflight "${RELEASE_DIR}" "${CANDIDATE_ENV}" \
  ${PREFLIGHT_PLACEHOLDERS[@]+"${PREFLIGHT_PLACEHOLDERS[@]}"} || preflight_rc=$?
case "${preflight_rc}" in
  0) log "preflight env: candidate Compose renders with every required variable" ;;
  1) die "candidate release needs variables missing from the shared env: ${REQUIRED_ENV_MISSING} (names only); no service or database was touched." ;;
  *) die "candidate Compose configuration does not render with its env (details withheld; missing so far: ${REQUIRED_ENV_MISSING:-none}); no service or database was touched." ;;
esac

log "preflight images: verify/cache the 16 release digests"
EXPECTED_IMAGES=(airflow banxico console hubspot inegi mcp-infra refinement replicon
  salesforce sap_b1 sap_hcm sap_s4hana sap_successfactors sec_edgar vault workspace)
DIGEST_REFS=()
while IFS= read -r _ref; do
  [[ -n "${_ref}" ]] && DIGEST_REFS+=("${_ref}")
done < <(awk '$1 == "image:" && NF == 2 { print $2 }' "${CANDIDATE_IMAGES_OVERLAY}" | sort -u)
[[ "${#DIGEST_REFS[@]}" -eq 16 ]] || die "expected 16 image digests in the overlay, found ${#DIGEST_REFS[@]}."

for image in "${EXPECTED_IMAGES[@]}"; do
  matches=0
  prefix="ghcr.io/${GHCR_OWNER}/${image}@sha256:"
  for ref in "${DIGEST_REFS[@]}"; do
    if [[ "${ref}" == "${prefix}"* && "${ref#${prefix}}" =~ ^[0-9a-f]{64}$ ]]; then
      matches=$((matches + 1))
    fi
  done
  [[ "${matches}" -eq 1 ]] \
    || die "image overlay is not an exact owner-scoped 16-service digest lock."
done

MISSING_DIGEST_REFS=()
for ref in "${DIGEST_REFS[@]}"; do
  docker image inspect "${ref}" >/dev/null 2>&1 || MISSING_DIGEST_REFS+=("${ref}")
done
if [[ "${#MISSING_DIGEST_REFS[@]}" -gt 0 ]]; then
  [[ -d /var/lib/containerd ]] || die "/var/lib/containerd is missing."
  command -v findmnt >/dev/null 2>&1 || die "findmnt is required for the image pull disk gate."
  CONTAINERD_MOUNT="$(findmnt -n -T /var/lib/containerd -o TARGET | head -n 1)"
  CONTAINERD_SOURCE="$(findmnt -n -T /var/lib/containerd -o SOURCE | head -n 1)"
  CONTAINERD_FSTYPE="$(findmnt -n -T /var/lib/containerd -o FSTYPE | head -n 1)"
  [[ "${CONTAINERD_MOUNT}" == /* && -n "${CONTAINERD_SOURCE}" && -n "${CONTAINERD_FSTYPE}" ]] \
    || die "could not identify the filesystem backing /var/lib/containerd."
  CONTAINERD_FREE_BYTES="$(df --block-size=1 --output=avail "${CONTAINERD_MOUNT}" \
    | awk 'NR == 2 { gsub(/[[:space:]]/, "", $0); print $0 }')"
  [[ "${CONTAINERD_FREE_BYTES}" =~ ^[0-9]+$ ]] \
    || die "could not measure free bytes on the containerd filesystem."
  IMAGE_PULL_MIN_FREE_BYTES=$((IMAGE_PULL_MIN_FREE_GIB * 1024 * 1024 * 1024))
  log "preflight disk: containerd mount=${CONTAINERD_MOUNT} source=${CONTAINERD_SOURCE} fstype=${CONTAINERD_FSTYPE} missing=${#MISSING_DIGEST_REFS[@]} margin_gib=${IMAGE_PULL_MIN_FREE_GIB}"
  [[ "${CONTAINERD_FREE_BYTES}" -ge "${IMAGE_PULL_MIN_FREE_BYTES}" ]] \
    || die "containerd filesystem lacks the configured pre-pull free-space margin."

  OMEGA_GCP_ENVIRONMENT="${ENVIRONMENT}" OMEGA_GHCR_PULL_SECRET_VERSION="${GHCR_SECRET_VERSION}" \
    bash "${RELEASE_DIR}/infra/terraform-gcp/release/ghcr-auth-run.sh" \
      bash -c 'set -Eeuo pipefail; for ref in "$@"; do docker pull --quiet "$ref" >/dev/null; done' \
      omega-image-pull "${MISSING_DIGEST_REFS[@]}" \
    || die "authenticated image pull failed (all missing digests required)."
else
  log "preflight images: 16/16 immutable digests already cached; disk gate and pull skipped"
fi
for ref in "${DIGEST_REFS[@]}"; do
  docker image inspect "${ref}" >/dev/null 2>&1 \
    || die "image cache verification failed after authenticated pull."
done

if [[ "${DEPLOY_MODE:-apply}" == "dryrun" ]]; then
  trap - ERR EXIT
  log "DRY-RUN OK: release staged, secrets/config validated read-only and 16/16 image digests available locally. No DB/service/env mutation."
  printf 'REMOTE_DEPLOY\tDRYRUN_PASS\ttag=%s\tref=%s\n' "${TARGET_TAG}" "${DEPLOY_REF}"
  exit 0
fi

[[ -n "${PREV_TARGET}" && -d "${PREV_TARGET}" ]] \
  || die "current does not resolve to a previous release; refusing to quiesce without a restart target."
log "step 1 quiesce: stop every writer and fence both databases"
stop_all_writers
fence_databases
log "step 1 backup: ${main_db} (${main_pg}) + ${gold_db} (${gold_pg}) -> ${BACKUP_DIR}"
docker exec "${main_pg}" pg_dump --clean --if-exists -U postgres -d "${main_db}" > "${BACKUP_DIR}/${main_db}.sql"
docker exec "${gold_pg}" pg_dump --clean --if-exists -U postgres -p 5433 -d "${gold_db}" > "${BACKUP_DIR}/${gold_db}.sql"
assert_zero_competing_sessions
[[ -s "${BACKUP_DIR}/${main_db}.sql" && -s "${BACKUP_DIR}/${gold_db}.sql" ]] || die "a backup dump is empty; refusing to proceed."
main_sha="$(sha256_of "${BACKUP_DIR}/${main_db}.sql")"; gold_sha="$(sha256_of "${BACKUP_DIR}/${gold_db}.sql")"
printf '%s\t%s\n%s\t%s\n' "${main_db}" "${main_sha}" "${gold_db}" "${gold_sha}" > "${BACKUP_DIR}/SHA256SUMS"
BACKUP_DONE=1
log "step 1 backup: OK (main=$(wc -c <"${BACKUP_DIR}/${main_db}.sql")B gold=$(wc -c <"${BACKUP_DIR}/${gold_db}.sql")B)"

MUTATED=1   # from here the DB/runtime changes and a failure triggers rollback

log "step 4 databases: recreate postgres with the new init mount"
( cd "${RELEASE_DIR}" && compose_databases_up )
wait_db_healthy "${main_pg}" || die "main postgres did not become healthy after recreate."
wait_db_healthy "${gold_pg}" || die "gold postgres did not become healthy after recreate."
assert_zero_competing_sessions

log "step 5 migrations: forward-only ledger + drift guard"
( cd "${RELEASE_DIR}" && bash scripts/apply_db_migrations.sh )
assert_zero_competing_sessions

restore_database_access || die "could not restore database connection limits after migrations."

log "step 5b deploy: compose up full stack ${TARGET_TAG}"
up_ok=0
for attempt in 1 2 3 4 5; do
  if ( cd "${RELEASE_DIR}" && compose_up ); then up_ok=1; break; fi
  log "step 5b: compose up attempt ${attempt} did not settle (slow-booting service); waiting 30s and retrying"
  sleep 30
done
[[ "${up_ok}" -eq 1 ]] || die "compose up did not settle after retries."

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

log "step 7 promote: current -> releases/${DEPLOY_REF}"
[[ -n "${SHARED_ENV_BACKUP}" ]] || die "shared environment backup is unavailable; refusing promotion."
SHARED_ENV_CHANGED=1
replace_file_atomic "${CANDIDATE_ENV}" "${SHARED_ENV}"
ln -sfn "${RELEASE_DIR}" "${CURRENT}"
date -Iseconds > "${APP_ROOT}/DEPLOYED"
PROMOTED=1
trap - ERR EXIT
log "PROMOTED ${TARGET_TAG} (${DEPLOY_REF}); backup retained at ${BACKUP_DIR}"
printf 'REMOTE_DEPLOY\tPASS\ttag=%s\tref=%s\tbackup=%s\n' "${TARGET_TAG}" "${DEPLOY_REF}" "${BACKUP_DIR}"
