#!/bin/bash
cd /opt/modecissions/deploy
docker compose -f docker-compose.aws.yml logs -f ${1:-}
# Uso: ./logs.sh          → todos los logs
#      ./logs.sh console  → solo console
