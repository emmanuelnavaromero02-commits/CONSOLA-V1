#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

REPO_DIR="${REPO_DIR:-/opt/modecissions}"
DEPLOY_DIR="${DEPLOY_DIR:-${REPO_DIR}/infra/terraform/deploy}"
cd "${DEPLOY_DIR}"

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

BACKUP_ID="${BACKUP_ID:-$(date -u +%Y%m%dT%H%M%SZ)-${IMAGE_TAG:-unknown}}"
WORKDIR="$(mktemp -d /tmp/modecissions-backup.XXXXXX)"
trap 'rm -rf "${WORKDIR}"' EXIT

echo "[backup] Creating backup ${BACKUP_ID}"

docker compose "${COMPOSE_FILES[@]}" exec -T postgres \
  pg_dumpall -U postgres --clean --if-exists | gzip -9 > "${WORKDIR}/postgres.sql.gz"

docker compose "${COMPOSE_FILES[@]}" exec -T postgres_gold \
  pg_dumpall -U postgres -p 5433 --clean --if-exists | gzip -9 > "${WORKDIR}/postgres_gold.sql.gz"

cat > "${WORKDIR}/manifest.json" <<EOF
{
  "backup_id": "${BACKUP_ID}",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "deploy_ref": "${DEPLOY_REF:-}",
  "image_tag": "${IMAGE_TAG:-}",
  "repo_head": "$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null || true)",
  "s3_bucket": "${S3_BUCKET_NAME}"
}
EOF

aws s3 cp "${WORKDIR}/postgres.sql.gz" "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/postgres.sql.gz" --region "${AWS_REGION}"
aws s3 cp "${WORKDIR}/postgres_gold.sql.gz" "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/postgres_gold.sql.gz" --region "${AWS_REGION}"
aws s3 cp "${WORKDIR}/manifest.json" "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/manifest.json" --region "${AWS_REGION}"

# Snapshot lakehouse objects into the backup prefix without recursively copying prior backups.
aws s3 sync "s3://${S3_BUCKET_NAME}/" "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/lakehouse/" \
  --region "${AWS_REGION}" \
  --exclude "backups/*"

echo "[backup] Complete: s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/"
