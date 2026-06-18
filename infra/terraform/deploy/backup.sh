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
if [[ "${DEPLOY_CARTRIDGES_SAME_HOST:-true}" == "true" ]]; then
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

POSTGRES_SHA="$(sha256sum "${WORKDIR}/postgres.sql.gz" | awk '{print $1}')"
POSTGRES_GOLD_SHA="$(sha256sum "${WORKDIR}/postgres_gold.sql.gz" | awk '{print $1}')"
POSTGRES_SIZE="$(wc -c < "${WORKDIR}/postgres.sql.gz" | tr -d ' ')"
POSTGRES_GOLD_SIZE="$(wc -c < "${WORKDIR}/postgres_gold.sql.gz" | tr -d ' ')"

aws s3 ls "s3://${S3_BUCKET_NAME}/" --recursive --region "${AWS_REGION}" \
  | grep -v '/backups/' \
  | awk '{print $4 "\t" $3}' \
  | sort > "${WORKDIR}/lakehouse_objects.tsv" || true
LAKEHOUSE_OBJECTS="$(wc -l < "${WORKDIR}/lakehouse_objects.tsv" | tr -d ' ')"
LAKEHOUSE_SHA="$(sha256sum "${WORKDIR}/lakehouse_objects.tsv" | awk '{print $1}')"

python3 - "${WORKDIR}/config_manifest.json" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

path = Path(".env")
secret_re = re.compile(r"(SECRET|PASSWORD|TOKEN|KEY|CREDENTIAL|DSN|DATABASE_URL)", re.I)
keys = []
if path.exists():
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            keys.append({"key": key, "redacted": bool(secret_re.search(key))})
payload = {
    "env_file_present": path.exists(),
    "env_key_count": len(keys),
    "env_keys": keys,
    "secrets_redacted": True,
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
CONFIG_SHA="$(sha256sum "${WORKDIR}/config_manifest.json" | awk '{print $1}')"

cat > "${WORKDIR}/manifest.json" <<EOF
{
  "backup_id": "${BACKUP_ID}",
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "deploy_ref": "${DEPLOY_REF:-}",
  "image_tag": "${IMAGE_TAG:-}",
  "version": "$(tr -d '\r\n' < "${REPO_DIR}/VERSION" 2>/dev/null || true)",
  "repo_head": "$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null || true)",
  "aws_region": "${AWS_REGION}",
  "s3_bucket": "${S3_BUCKET_NAME}",
  "artifacts": {
    "postgres": {
      "s3_key": "backups/${BACKUP_ID}/postgres.sql.gz",
      "sha256": "${POSTGRES_SHA}",
      "size_bytes": ${POSTGRES_SIZE}
    },
    "postgres_gold": {
      "s3_key": "backups/${BACKUP_ID}/postgres_gold.sql.gz",
      "sha256": "${POSTGRES_GOLD_SHA}",
      "size_bytes": ${POSTGRES_GOLD_SIZE}
    },
    "lakehouse_manifest": {
      "s3_key": "backups/${BACKUP_ID}/lakehouse_objects.tsv",
      "sha256": "${LAKEHOUSE_SHA}",
      "object_count": ${LAKEHOUSE_OBJECTS}
    },
    "config_manifest": {
      "s3_key": "backups/${BACKUP_ID}/config_manifest.json",
      "sha256": "${CONFIG_SHA}",
      "secrets_redacted": true
    }
  },
  "excluded": [
    {"scope": "runtime docker image layers", "reason": "reproducible from immutable GHCR IMAGE_TAG"},
    {"scope": "raw secret values", "reason": "never written to backup evidence; config manifest records keys only"}
  ],
  "restore_hint": "Run BACKUP_ID=${BACKUP_ID} make dr-rehearsal-aws for safe isolated restore verification before any destructive restore."
}
EOF

aws s3 cp "${WORKDIR}/postgres.sql.gz" "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/postgres.sql.gz" --region "${AWS_REGION}" --only-show-errors
aws s3 cp "${WORKDIR}/postgres_gold.sql.gz" "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/postgres_gold.sql.gz" --region "${AWS_REGION}" --only-show-errors
aws s3 cp "${WORKDIR}/lakehouse_objects.tsv" "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/lakehouse_objects.tsv" --region "${AWS_REGION}" --only-show-errors
aws s3 cp "${WORKDIR}/config_manifest.json" "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/config_manifest.json" --region "${AWS_REGION}" --only-show-errors
aws s3 cp "${WORKDIR}/manifest.json" "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/manifest.json" --region "${AWS_REGION}" --only-show-errors

# Snapshot lakehouse objects into the backup prefix without recursively copying prior backups.
aws s3 sync "s3://${S3_BUCKET_NAME}/" "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/lakehouse/" \
  --region "${AWS_REGION}" \
  --only-show-errors \
  --exclude "backups/*"

echo "OMEGA_BACKUP_MANIFEST=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])), sort_keys=True))' "${WORKDIR}/manifest.json")"
echo "[backup] Complete: s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/"
