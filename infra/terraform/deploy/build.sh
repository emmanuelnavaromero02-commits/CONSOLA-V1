#!/bin/bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

DEPLOY_DIR="/opt/modecissions/infra/terraform/deploy"
cd "${DEPLOY_DIR}"

if [ ! -f .env ]; then
  echo "ERROR: .env no existe en ${DEPLOY_DIR}. Ejecuta /opt/modecissions/scripts/aws-entrypoint.sh."
  exit 1
fi

set -a
source .env
set +a

echo "=== Pulling MODecissions release images ==="
echo "GHCR_OWNER=${GHCR_OWNER:-emmanuelnavaromero02-commits}"
echo "IMAGE_TAG=${IMAGE_TAG:-v1.44.5}"

docker compose -f docker-compose.aws.yml pull \
  console console_next workspace refinement vault mcp-infra airflow airflow-scheduler

echo "=== Release images ready ==="
