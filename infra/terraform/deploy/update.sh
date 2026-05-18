#!/bin/bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

DEPLOY_DIR="/opt/modecissions/infra/terraform/deploy"
cd /opt/modecissions
sudo -u ubuntu git pull
cd "${DEPLOY_DIR}"

if [ ! -f .env ]; then
  echo "ERROR: .env no existe en ${DEPLOY_DIR}. Ejecuta /opt/modecissions/scripts/aws-entrypoint.sh."
  exit 1
fi

set -a
source .env
set +a

if [ -n "${1:-}" ]; then
  docker compose -f docker-compose.aws.yml up -d postgres postgres_gold
  sleep 15
  bash apply_db_migrations.sh
  docker compose -f docker-compose.aws.yml pull "$1"
  docker compose -f docker-compose.aws.yml up -d --force-recreate "$1"
else
  bash build.sh
  docker compose -f docker-compose.aws.yml up -d postgres postgres_gold
  sleep 15
  bash apply_db_migrations.sh
  docker compose -f docker-compose.aws.yml up -d --force-recreate
fi

echo "✓ Deploy completado"
