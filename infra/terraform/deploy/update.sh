#!/bin/bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

REPO_DIR="/opt/modecissions"
DEPLOY_DIR="${REPO_DIR}/infra/terraform/deploy"
cd "${DEPLOY_DIR}"

if [ ! -f .env ]; then
  echo "ERROR: .env no existe en ${DEPLOY_DIR}. Ejecuta /opt/modecissions/scripts/aws-entrypoint.sh."
  exit 1
fi

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

APP_ENV_NORMALISED="$(printf '%s' "${APP_ENV:-production}" | tr '[:upper:]' '[:lower:]')"
if [[ "${APP_ENV_NORMALISED}" == "production" || "${APP_ENV_NORMALISED}" == "prod" ]]; then
  if [[ -z "${DEPLOY_REF:-}" ]]; then
    echo "ERROR: DEPLOY_REF is required in production so deploys are reproducible." >&2
    exit 1
  fi
  if [[ -z "${IMAGE_TAG:-}" || "${IMAGE_TAG:-}" == "latest" ]]; then
    echo "ERROR: IMAGE_TAG must be an immutable release tag in production (not empty/latest)." >&2
    exit 1
  fi
  assert_release_refs_coherent
fi

cd "${REPO_DIR}"
sudo -u ubuntu git fetch --tags origin
if [[ -n "${DEPLOY_REF:-}" ]]; then
  sudo -u ubuntu git checkout --detach "${DEPLOY_REF}"
else
  sudo -u ubuntu git pull --ff-only
fi
cd "${DEPLOY_DIR}"

set -a
source .env
set +a

COMPOSE_FILES=(-f docker-compose.aws.yml)
if [[ "${DEPLOY_CARTRIDGES_SAME_HOST:-true}" == "true" ]]; then
  COMPOSE_FILES+=(-f docker-compose.cartridges.yml)
fi

if [ -n "${1:-}" ]; then
  docker compose "${COMPOSE_FILES[@]}" up -d postgres postgres_gold
  sleep 15
  bash apply_db_migrations.sh
  docker compose "${COMPOSE_FILES[@]}" pull "$1"
  docker compose "${COMPOSE_FILES[@]}" up -d --force-recreate "$1"
else
  bash build.sh
  docker compose "${COMPOSE_FILES[@]}" up -d postgres postgres_gold
  sleep 15
  bash apply_db_migrations.sh
  docker compose "${COMPOSE_FILES[@]}" up -d --force-recreate
fi

echo "✓ Deploy completado"
