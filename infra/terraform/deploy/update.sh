#!/bin/bash
set -euo pipefail

DEPLOY_DIR="/opt/modecissions/infra/terraform/deploy"
cd /opt/modecissions
git pull
cd "${DEPLOY_DIR}"

if [ ! -f .env ]; then
  echo "ERROR: .env no existe en ${DEPLOY_DIR}. Copia .env.example y complétalo."
  exit 1
fi

set -a
source .env
set +a

if [ -n "${1:-}" ]; then
  docker compose -f docker-compose.aws.yml pull "$1"
  docker compose -f docker-compose.aws.yml up -d --force-recreate "$1"
else
  bash build.sh
  docker compose -f docker-compose.aws.yml up -d --force-recreate
fi

echo "✓ Deploy completado"
