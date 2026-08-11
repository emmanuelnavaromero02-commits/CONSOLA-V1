#!/bin/bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename -- "$0")"
DEPLOY_DIR="/opt/modecissions/infra/terraform/deploy"
cd "${DEPLOY_DIR}"

AUTH_RUNNER="${DEPLOY_DIR}/ghcr-auth-run.sh"
if [[ "${OMEGA_GHCR_AUTH_ACTIVE:-0}" != "1" ]]; then
  exec bash "${AUTH_RUNNER}" bash "${SCRIPT_PATH}" "$@"
fi
if [[ -z "${DOCKER_CONFIG:-}" || ! -s "${DOCKER_CONFIG}/config.json" ]]; then
  echo "ERROR: authenticated GHCR Docker context is missing." >&2
  exit 1
fi

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

export COMPOSE_PROGRESS="${COMPOSE_PROGRESS:-quiet}"

docker compose -f docker-compose.aws.yml pull --quiet \
  console workspace refinement vault mcp-infra airflow airflow-scheduler

if [[ "${DEPLOY_CARTRIDGES_SAME_HOST:-true}" == "true" ]]; then
  docker compose -f docker-compose.aws.yml -f docker-compose.cartridges.yml pull --quiet \
    replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-successfactors sap-s4hana
fi

echo "=== Release images ready ==="
