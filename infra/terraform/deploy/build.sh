#!/bin/bash
set -euo pipefail

DEPLOY_DIR="/opt/modecissions/infra/terraform/deploy"
cd "${DEPLOY_DIR}"

if [ ! -f .env ]; then
  echo "ERROR: .env no existe en ${DEPLOY_DIR}. Copia .env.example y complétalo."
  exit 1
fi

set -a
source .env
set +a

echo "=== Pulling MODecissions release images ==="
echo "GHCR_OWNER=${GHCR_OWNER:-emmanuelnavaromero02-commits}"
echo "IMAGE_TAG=${IMAGE_TAG:-v1.44.5}"

docker compose -f docker-compose.aws.yml pull \
  console workspace refinement vault mcp-infra

echo "=== Release images ready ==="
