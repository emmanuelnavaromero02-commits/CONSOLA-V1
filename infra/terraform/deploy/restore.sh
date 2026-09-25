#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ] && [ "${OMEGA_DR_ALLOW_NON_ROOT:-0}" != "1" ]; then
  exec sudo "$0" "$@"
fi

REPO_DIR="${REPO_DIR:-/opt/modecissions}"
DEPLOY_DIR="${DEPLOY_DIR:-${REPO_DIR}/infra/terraform/deploy}"
cd "${DEPLOY_DIR}"

if [ "${CONFIRM_RESTORE:-}" != "modecissions" ]; then
  echo "ERROR: destructive restore requires CONFIRM_RESTORE=modecissions" >&2
  exit 2
fi

: "${BACKUP_ID:?BACKUP_ID is required, e.g. 20260530T220000Z-v1.0.0-rc3}"

if [ ! -f .env ]; then
  echo "ERROR: .env no existe en ${DEPLOY_DIR}. Ejecuta ${REPO_DIR}/scripts/aws-entrypoint.sh." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

BACKUP_STORAGE_BACKEND="${BACKUP_STORAGE_BACKEND:-}"
if [[ -z "${BACKUP_STORAGE_BACKEND}" ]]; then
  if [[ -n "${OMEGA_BACKUP_LOCAL_DIR:-}" ]]; then
    BACKUP_STORAGE_BACKEND="local"
  elif [[ -n "${GCS_BUCKET:-}" ]]; then
    BACKUP_STORAGE_BACKEND="gcs"
  else
    BACKUP_STORAGE_BACKEND="s3"
  fi
fi

case "${BACKUP_STORAGE_BACKEND}" in
  s3)
    : "${S3_BUCKET_NAME:?S3_BUCKET_NAME is required}"
    : "${AWS_REGION:?AWS_REGION is required}"
    ;;
  gcs)
    : "${GCS_BUCKET:?GCS_BUCKET is required for the gcs backend}"
    command -v gcloud >/dev/null || { echo "ERROR: gcloud is required for the gcs backend" >&2; exit 1; }
    ;;
  local)
    : "${OMEGA_BACKUP_LOCAL_DIR:?OMEGA_BACKUP_LOCAL_DIR is required for the local backend}"
    ;;
  *)
    echo "ERROR: unknown BACKUP_STORAGE_BACKEND=${BACKUP_STORAGE_BACKEND} (s3|gcs|local)" >&2
    exit 1
    ;;
esac
echo "[restore] storage backend: ${BACKUP_STORAGE_BACKEND}"

storage_fetch() {
  local object_key="$1" dest="$2"
  case "${BACKUP_STORAGE_BACKEND}" in
    s3)
      aws s3 cp "s3://${S3_BUCKET_NAME}/${object_key}" "${dest}" --region "${AWS_REGION}"
      ;;
    gcs)
      gcloud storage cp "gs://${GCS_BUCKET}/${object_key}" "${dest}" --no-user-output-enabled
      ;;
    local)
      cp "${OMEGA_BACKUP_LOCAL_DIR}/${object_key}" "${dest}"
      ;;
  esac
}

COMPOSE_FILES=(-f docker-compose.aws.yml)
if [[ "${DEPLOY_CARTRIDGES_SAME_HOST:-true}" == "true" ]]; then
  COMPOSE_FILES+=(-f docker-compose.cartridges.yml)
fi

WORKDIR="$(mktemp -d /tmp/modecissions-restore.XXXXXX)"
trap 'rm -rf "${WORKDIR}"' EXIT

echo "[restore] Restoring backup ${BACKUP_ID}"
storage_fetch "backups/${BACKUP_ID}/manifest.json" "${WORKDIR}/manifest.json"
storage_fetch "backups/${BACKUP_ID}/postgres.sql.gz" "${WORKDIR}/postgres.sql.gz"
storage_fetch "backups/${BACKUP_ID}/postgres_gold.sql.gz" "${WORKDIR}/postgres_gold.sql.gz"

docker compose "${COMPOSE_FILES[@]}" down
docker compose "${COMPOSE_FILES[@]}" up -d postgres postgres_gold
sleep 30

strip_bootstrap_role() {
  awk '
    BEGIN { globals = 1 }
    /^\\connect/ { globals = 0 }
    globals && ($0 == "DROP ROLE IF EXISTS postgres;" || $0 == "DROP ROLE postgres;" || $0 == "CREATE ROLE postgres;") { next }
    { print }
  '
}

gunzip -c "${WORKDIR}/postgres.sql.gz" | strip_bootstrap_role | docker compose "${COMPOSE_FILES[@]}" exec -T postgres \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1

gunzip -c "${WORKDIR}/postgres_gold.sql.gz" | strip_bootstrap_role | docker compose "${COMPOSE_FILES[@]}" exec -T postgres_gold \
  psql -U postgres -p 5433 -d postgres -v ON_ERROR_STOP=1

if [[ "${RESTORE_DELETE_STALE_S3:-0}" == "1" ]]; then
  : "${RESTORE_DELETE_PREFIX:?RESTORE_DELETE_STALE_S3=1 requires RESTORE_DELETE_PREFIX, e.g. data/lakehouse}"
  if [[ "${RESTORE_DELETE_PREFIX}" == "/" || "${RESTORE_DELETE_PREFIX}" == "." || "${RESTORE_DELETE_PREFIX}" == "backups" || "${RESTORE_DELETE_PREFIX}" == backups/* ]]; then
    echo "ERROR: unsafe RESTORE_DELETE_PREFIX=${RESTORE_DELETE_PREFIX}" >&2
    exit 2
  fi
  case "${BACKUP_STORAGE_BACKEND}" in
    s3)
      aws s3 sync \
        "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/lakehouse/${RESTORE_DELETE_PREFIX%/}/" \
        "s3://${S3_BUCKET_NAME}/${RESTORE_DELETE_PREFIX%/}/" \
        --region "${AWS_REGION}" \
        --delete
      ;;
    gcs)
      gcloud storage rsync -r --delete-unmatched-destination-objects \
        "gs://${GCS_BUCKET}/backups/${BACKUP_ID}/lakehouse/${RESTORE_DELETE_PREFIX%/}/" \
        "gs://${GCS_BUCKET}/${RESTORE_DELETE_PREFIX%/}/"
      ;;
    local)
      echo "[restore] local backend: no lakehouse bucket to restore"
      ;;
  esac
else
  case "${BACKUP_STORAGE_BACKEND}" in
    s3)
      aws s3 sync "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/lakehouse/" "s3://${S3_BUCKET_NAME}/" \
        --region "${AWS_REGION}" \
        --exclude "backups/*"
      ;;
    gcs)
      gcloud storage rsync -r \
        "gs://${GCS_BUCKET}/backups/${BACKUP_ID}/lakehouse/" "gs://${GCS_BUCKET}/" \
        -x '^backups/.*'
      ;;
    local)
      echo "[restore] local backend: no lakehouse bucket to restore"
      ;;
  esac
fi

bash start.sh

if [[ "${RUN_SMOKE:-1}" == "1" ]]; then
  bash "${REPO_DIR}/scripts/smoke_test.sh"
fi

echo "[restore] Complete: ${BACKUP_ID}"
