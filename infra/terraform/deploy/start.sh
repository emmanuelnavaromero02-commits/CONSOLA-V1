#!/bin/bash
set -e

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename -- "$0")"
DEPLOY_DIR="/opt/modecissions/infra/terraform/deploy"
cd $DEPLOY_DIR

AUTH_RUNNER="${DEPLOY_DIR}/ghcr-auth-run.sh"
if [[ "${OMEGA_GHCR_AUTH_ACTIVE:-0}" != "1" ]]; then
  exec bash "${AUTH_RUNNER}" bash "${SCRIPT_PATH}" "$@"
fi
if [[ -z "${DOCKER_CONFIG:-}" || ! -s "${DOCKER_CONFIG}/config.json" ]]; then
  echo "ERROR: authenticated GHCR Docker context is missing." >&2
  exit 1
fi

# Verificar que .env existe y tiene variables críticas
if [ ! -f .env ]; then
  echo "ERROR: .env no existe en $DEPLOY_DIR. Ejecuta /opt/modecissions/scripts/aws-entrypoint.sh."
  exit 1
fi

MODECISSIONS_BOOTSTRAP_CONTROL_ROOM_EVIDENCE=false \
  bash /opt/modecissions/infra/bootstrap-keys.sh .env

set -a
source .env
set +a

is_release_tag() {
  [[ "${1:-}" =~ ^v[0-9] ]]
}

assert_release_refs_coherent() {
  if is_release_tag "${DEPLOY_REF:-}" && is_release_tag "${IMAGE_TAG:-}" && [[ "${DEPLOY_REF}" != "${IMAGE_TAG}" ]]; then
    echo "ERROR: DEPLOY_REF (${DEPLOY_REF}) must match IMAGE_TAG (${IMAGE_TAG}) for tag-based production deploys." >&2
    exit 1
  fi
}

check_var() {
  if [ -z "${!1}" ]; then echo "ERROR: $1 no está definida en .env"; exit 1; fi
}

check_var POSTGRES_PASSWORD
check_var S3_BUCKET_NAME
check_var ANTHROPIC_API_KEY
check_var SUPERSET_SECRET_KEY
check_var SUPERSET_SERVICE_PASSWORD
check_var AIRFLOW_SECRET_KEY

APP_ENV_NORMALISED="$(printf '%s' "${APP_ENV:-production}" | tr '[:upper:]' '[:lower:]')"
if [[ "${APP_ENV_NORMALISED}" == "production" || "${APP_ENV_NORMALISED}" == "prod" ]]; then
  check_var DEPLOY_REF
  check_var IMAGE_TAG
  if [[ "${IMAGE_TAG}" == "latest" ]]; then
    echo "ERROR: IMAGE_TAG must be an immutable release tag in production (not latest)." >&2
    exit 1
  fi
  assert_release_refs_coherent
fi

echo "✓ Variables de entorno OK"

COMPOSE_FILES=(-f docker-compose.aws.yml)
if [ "${DEPLOY_CARTRIDGES_SAME_HOST:-true}" = "true" ]; then
  COMPOSE_FILES+=(-f docker-compose.cartridges.yml)
fi

# Postgres primero (necesita estar listo antes de los init containers)
echo "--- Iniciando Postgres ---"
docker compose "${COMPOSE_FILES[@]}" up -d postgres postgres_gold
echo "Esperando Postgres listo (30s)..."
sleep 30

echo "--- Aplicando migraciones pendientes ---"
bash apply_db_migrations.sh

# Init containers (DB superset/airflow ya creadas por init/*.sh del contenedor postgres)
echo "--- Iniciando init containers ---"
docker compose "${COMPOSE_FILES[@]}" up -d superset-init airflow-init
echo "Esperando init containers (60s)..."
sleep 60

# Levantar resto
echo "--- Iniciando todos los servicios ---"
docker compose "${COMPOSE_FILES[@]}" up -d

echo "--- Estado final ---"
docker compose "${COMPOSE_FILES[@]}" ps
