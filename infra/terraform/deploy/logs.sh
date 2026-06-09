#!/bin/bash
set -euo pipefail
cd /opt/modecissions/infra/terraform/deploy
docker compose -f docker-compose.aws.yml logs -f ${1:-}
# Uso: ./logs.sh          → todos los logs
#      ./logs.sh console  → solo console
