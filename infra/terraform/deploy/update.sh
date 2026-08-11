#!/bin/bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename -- "$0")"
REPO_DIR="/opt/modecissions"
DEPLOY_DIR="${REPO_DIR}/infra/terraform/deploy"
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

MODECISSIONS_BOOTSTRAP_CONTROL_ROOM_EVIDENCE=false \
  bash /opt/modecissions/infra/bootstrap-keys.sh .env
bash /opt/modecissions/infra/terraform/deploy/ensure_evidence_env.sh .env

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
if [[ "${OMEGA_SKIP_WORKTREE_UPDATE:-0}" == "1" ]]; then
  echo "Skipping worktree update because OMEGA_SKIP_WORKTREE_UPDATE=1"
else
  sudo -u ubuntu git fetch --tags origin
  if [[ -n "${DEPLOY_REF:-}" ]]; then
    sudo -u ubuntu git checkout --detach "${DEPLOY_REF}"
  else
    sudo -u ubuntu git pull --ff-only
  fi
fi
cd "${DEPLOY_DIR}"

set -a
source .env
set +a

COMPOSE_FILES=(-f docker-compose.aws.yml)
if [[ "${DEPLOY_CARTRIDGES_SAME_HOST:-true}" == "true" ]]; then
  COMPOSE_FILES+=(-f docker-compose.cartridges.yml)
fi
export COMPOSE_PROGRESS="${COMPOSE_PROGRESS:-quiet}"

if [ -n "${1:-}" ]; then
  docker compose "${COMPOSE_FILES[@]}" up -d postgres postgres_gold
  sleep 15
  bash apply_db_migrations.sh
  docker compose "${COMPOSE_FILES[@]}" pull --quiet "$1"
  docker compose "${COMPOSE_FILES[@]}" up -d --force-recreate "$1"
else
  bash build.sh
  docker compose "${COMPOSE_FILES[@]}" up -d postgres postgres_gold
  sleep 15
  bash apply_db_migrations.sh
  docker compose "${COMPOSE_FILES[@]}" up -d --force-recreate
fi

echo "✓ Deploy completado"
