#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
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

: "${S3_BUCKET_NAME:?S3_BUCKET_NAME is required}"
: "${AWS_REGION:?AWS_REGION is required}"

COMPOSE_FILES=(-f docker-compose.aws.yml)
if [[ "${DEPLOY_CARTRIDGES_SAME_HOST:-false}" == "true" ]]; then
  COMPOSE_FILES+=(-f docker-compose.cartridges.yml)
fi

WORKDIR="$(mktemp -d /tmp/modecissions-restore.XXXXXX)"
trap 'rm -rf "${WORKDIR}"' EXIT

echo "[restore] Restoring backup ${BACKUP_ID}"
aws s3 cp "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/manifest.json" "${WORKDIR}/manifest.json" --region "${AWS_REGION}"
aws s3 cp "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/postgres.sql.gz" "${WORKDIR}/postgres.sql.gz" --region "${AWS_REGION}"
aws s3 cp "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/postgres_gold.sql.gz" "${WORKDIR}/postgres_gold.sql.gz" --region "${AWS_REGION}"

docker compose "${COMPOSE_FILES[@]}" down
docker compose "${COMPOSE_FILES[@]}" up -d postgres postgres_gold
sleep 30

gunzip -c "${WORKDIR}/postgres.sql.gz" | docker compose "${COMPOSE_FILES[@]}" exec -T postgres \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1

gunzip -c "${WORKDIR}/postgres_gold.sql.gz" | docker compose "${COMPOSE_FILES[@]}" exec -T postgres_gold \
  psql -U postgres -p 5433 -d postgres -v ON_ERROR_STOP=1

if [[ "${RESTORE_DELETE_STALE_S3:-0}" == "1" ]]; then
  : "${RESTORE_DELETE_PREFIX:?RESTORE_DELETE_STALE_S3=1 requires RESTORE_DELETE_PREFIX, e.g. data/lakehouse}"
  if [[ "${RESTORE_DELETE_PREFIX}" == "/" || "${RESTORE_DELETE_PREFIX}" == "." || "${RESTORE_DELETE_PREFIX}" == "backups" || "${RESTORE_DELETE_PREFIX}" == backups/* ]]; then
    echo "ERROR: unsafe RESTORE_DELETE_PREFIX=${RESTORE_DELETE_PREFIX}" >&2
    exit 2
  fi
  aws s3 sync \
    "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/lakehouse/${RESTORE_DELETE_PREFIX%/}/" \
    "s3://${S3_BUCKET_NAME}/${RESTORE_DELETE_PREFIX%/}/" \
    --region "${AWS_REGION}" \
    --delete
else
  aws s3 sync "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/lakehouse/" "s3://${S3_BUCKET_NAME}/" \
    --region "${AWS_REGION}" \
    --exclude "backups/*"
fi

bash start.sh

if [[ "${RUN_SMOKE:-1}" == "1" ]]; then
  bash "${REPO_DIR}/scripts/smoke_test.sh"
fi

echo "[restore] Complete: ${BACKUP_ID}"
