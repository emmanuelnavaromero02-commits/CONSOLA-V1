#!/usr/bin/env bash
set -euo pipefail

# OMEGA_DR_ALLOW_NON_ROOT=1 is for isolated DR rehearsals (ephemeral compose
# stacks owned by the invoking user); deploy hosts keep requiring root.
if [ "${EUID:-$(id -u)}" -ne 0 ] && [ "${OMEGA_DR_ALLOW_NON_ROOT:-0}" != "1" ]; then
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

# Storage backend: explicit BACKUP_STORAGE_BACKEND wins; otherwise inferred —
# OMEGA_BACKUP_LOCAL_DIR (isolated rehearsals) > GCS_BUCKET (canonical GCP
# hosts export it, see terraform-gcp templates) > S3_BUCKET_NAME (AWS hosts).
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

case "${BACKUP_STORAGE_BACKEND}" in
  s3)    BACKUP_DESTINATION="s3://${S3_BUCKET_NAME}/backups/" ;;
  gcs)   BACKUP_DESTINATION="gs://${GCS_BUCKET}/backups/" ;;
  local) BACKUP_DESTINATION="${OMEGA_BACKUP_LOCAL_DIR}/backups/" ;;
esac
echo "[backup] storage backend: ${BACKUP_STORAGE_BACKEND} -> ${BACKUP_DESTINATION}"

storage_cp() {
  local file="$1" object_key="$2"
  case "${BACKUP_STORAGE_BACKEND}" in
    s3)
      aws s3 cp "${file}" "s3://${S3_BUCKET_NAME}/${object_key}" --region "${AWS_REGION}" --only-show-errors
      ;;
    gcs)
      gcloud storage cp "${file}" "gs://${GCS_BUCKET}/${object_key}" --no-user-output-enabled
      ;;
    local)
      mkdir -p "${OMEGA_BACKUP_LOCAL_DIR}/$(dirname "${object_key}")"
      cp "${file}" "${OMEGA_BACKUP_LOCAL_DIR}/${object_key}"
      ;;
  esac
}

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

case "${BACKUP_STORAGE_BACKEND}" in
  s3)
    # The prefix filter runs on the extracted key (column 4), anchored: the
    # old raw-line 'grep -v /backups/' never matched the top-level backups/
    # prefix, so prior backups leaked into the lakehouse inventory.
    aws s3 ls "s3://${S3_BUCKET_NAME}/" --recursive --region "${AWS_REGION}" \
      | awk '{print $4 "\t" $3}' \
      | grep -v '^backups/' \
      | sort > "${WORKDIR}/lakehouse_objects.tsv" || true
    ;;
  gcs)
    # index/substr keeps object names with spaces intact and strips the
    # bucket prefix literally (never as a regex). gcloud errors stay visible
    # on stderr; an empty inventory is tolerated like the s3 branch.
    gcloud storage du "gs://${GCS_BUCKET}/**" \
      | awk -v prefix="gs://${GCS_BUCKET}/" '{
          pos = index($0, prefix); if (pos == 0) next;
          key = substr($0, pos + length(prefix));
          if (key ~ /^backups\//) next;
          size = substr($0, 1, pos - 1); gsub(/[^0-9]/, "", size);
          print key "\t" size
        }' \
      | sort > "${WORKDIR}/lakehouse_objects.tsv" || true
    ;;
  local)
    # Isolated rehearsal: no lakehouse bucket to inventory.
    : > "${WORKDIR}/lakehouse_objects.tsv"
    ;;
esac
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

# Extra manifest fields only for non-s3 backends: S3 manifests (uploaded bytes
# and the OMEGA_BACKUP_MANIFEST stdout line) stay byte-identical to before.
BACKEND_MANIFEST_FIELDS=""
if [[ "${BACKUP_STORAGE_BACKEND}" != "s3" ]]; then
  BACKEND_MANIFEST_FIELDS="\"storage_backend\": \"${BACKUP_STORAGE_BACKEND}\",
  \"gcs_bucket\": \"${GCS_BUCKET:-}\",
  \"local_dir\": \"${OMEGA_BACKUP_LOCAL_DIR:-}\",
  "
fi

cat > "${WORKDIR}/manifest.json" <<EOF
{
  "backup_id": "${BACKUP_ID}",
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "deploy_ref": "${DEPLOY_REF:-}",
  "image_tag": "${IMAGE_TAG:-}",
  "version": "$(tr -d '\r\n' < "${REPO_DIR}/VERSION" 2>/dev/null || true)",
  "repo_head": "$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null || true)",
  ${BACKEND_MANIFEST_FIELDS}"aws_region": "${AWS_REGION:-}",
  "s3_bucket": "${S3_BUCKET_NAME:-}",
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

storage_cp "${WORKDIR}/postgres.sql.gz" "backups/${BACKUP_ID}/postgres.sql.gz"
storage_cp "${WORKDIR}/postgres_gold.sql.gz" "backups/${BACKUP_ID}/postgres_gold.sql.gz"
storage_cp "${WORKDIR}/lakehouse_objects.tsv" "backups/${BACKUP_ID}/lakehouse_objects.tsv"
storage_cp "${WORKDIR}/config_manifest.json" "backups/${BACKUP_ID}/config_manifest.json"
storage_cp "${WORKDIR}/manifest.json" "backups/${BACKUP_ID}/manifest.json"

# Snapshot lakehouse objects into the backup prefix without recursively copying prior backups.
case "${BACKUP_STORAGE_BACKEND}" in
  s3)
    aws s3 sync "s3://${S3_BUCKET_NAME}/" "s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/lakehouse/" \
      --region "${AWS_REGION}" \
      --only-show-errors \
      --exclude "backups/*"
    ;;
  gcs)
    gcloud storage rsync -r "gs://${GCS_BUCKET}/" "gs://${GCS_BUCKET}/backups/${BACKUP_ID}/lakehouse/" \
      -x '^backups/.*' --no-user-output-enabled
    ;;
  local)
    : # isolated rehearsal: no lakehouse bucket to snapshot
    ;;
esac

# Assignment first so set -e catches a manifest-canonicalization failure
# (a command substitution inside echo's arguments would be silently ignored).
OMEGA_BACKUP_MANIFEST_JSON="$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])), sort_keys=True))' "${WORKDIR}/manifest.json")"
echo "OMEGA_BACKUP_MANIFEST=${OMEGA_BACKUP_MANIFEST_JSON}"
case "${BACKUP_STORAGE_BACKEND}" in
  s3)    echo "[backup] Complete: s3://${S3_BUCKET_NAME}/backups/${BACKUP_ID}/" ;;
  gcs)   echo "[backup] Complete: gs://${GCS_BUCKET}/backups/${BACKUP_ID}/" ;;
  local) echo "[backup] Complete: ${OMEGA_BACKUP_LOCAL_DIR}/backups/${BACKUP_ID}/" ;;
esac
