#!/bin/bash
set -e
cd /opt/modecissions

echo "=== Building MODecissions images ==="
# RAG was migrated into mcp-infra. Workspace is the new end-user surface.
SERVICES=(console workspace refinement mcp-infra)
for svc in "${SERVICES[@]}"; do
  echo "--- Building $svc ---"
  start=$(date +%s)
  docker build -t modecissions/$svc:latest ./$svc
  end=$(date +%s)
  echo "✓ $svc built in $((end-start))s"
done
echo "=== All images built ==="
docker images | grep modecissions
