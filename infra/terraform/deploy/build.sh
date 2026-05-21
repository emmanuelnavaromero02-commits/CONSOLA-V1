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
if [[ -z "${IMAGE_TAG:-}" || "${IMAGE_TAG:-}" == "latest" ]]; then
  echo "ERROR: IMAGE_TAG must be set to an immutable release tag (not empty/latest)." >&2
  exit 1
fi
echo "IMAGE_TAG=${IMAGE_TAG}"

docker compose -f docker-compose.aws.yml pull \
  console console_next workspace refinement vault mcp-infra airflow airflow-scheduler

if [[ "${DEPLOY_CARTRIDGES_SAME_HOST:-false}" == "true" ]]; then
  docker compose -f docker-compose.aws.yml -f docker-compose.cartridges.yml pull \
    replicon sap-hcm sap-successfactors sap-s4hana
fi

echo "=== Release images ready ==="
