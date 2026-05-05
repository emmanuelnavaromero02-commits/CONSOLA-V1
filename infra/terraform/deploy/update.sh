#!/bin/bash
set -e
cd /opt/modecissions
git pull
if [ -n "$1" ]; then
  docker build -t modecissions/$1:latest ./$1
  docker compose -f infra/terraform/deploy/docker-compose.aws.yml up -d --force-recreate $1
else
  bash infra/terraform/deploy/build.sh
  docker compose -f infra/terraform/deploy/docker-compose.aws.yml up -d --force-recreate
fi
echo "✓ Deploy completado"
