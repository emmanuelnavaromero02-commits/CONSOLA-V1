#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/infra/.env"
COMPOSE=(docker compose --env-file "${ENV_FILE}" -f "${ROOT_DIR}/infra/docker-compose.yml")

scope="${LOCAL_REPAIR_SCOPE:-}"
if [[ "${scope}" != "local-dev" ]]; then
  echo "[local-repair] refusing: set LOCAL_REPAIR_SCOPE=local-dev" >&2
  exit 2
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[local-repair] missing infra/.env; run make bootstrap-env first" >&2
  exit 2
fi

if [[ "${OMEGA_PRODUCTION_HOST:-0}" == "1" || "${APP_ENV:-}" == "production-aws" ]]; then
  echo "[local-repair] refusing: production host marker present" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "[local-repair] Docker daemon unavailable; nothing repaired" >&2
  exit 2
fi

container_running() {
  local name="$1"
  docker inspect -f '{{.State.Running}}' "${name}" 2>/dev/null | grep -qx true
}

echo "[local-repair] checking local Postgres role/password drift"
if container_running mode_postgres && container_running mode_postgres_gold; then
  bash "${ROOT_DIR}/scripts/reconcile_db_passwords.sh"
else
  echo "[local-repair] postgres containers are not both running; skip role reconciliation"
fi

if [[ "${CONFIRM_SUPERSET_METASTORE_REPAIR:-}" != "LOCAL_SUPERSET_REPAIR" ]]; then
  echo "[local-repair] Superset metastore repair not requested"
  echo "[local-repair] To clear local encrypted Superset DB fields after a SECRET_KEY rotation, run:"
  echo "  CONFIRM_SUPERSET_METASTORE_REPAIR=LOCAL_SUPERSET_REPAIR make repair-local-stack"
  exit 0
fi

echo "[local-repair] local Superset metastore repair requested"
if ! container_running mode_postgres; then
  echo "[local-repair] mode_postgres is not running; cannot repair Superset metastore" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

PGOPTIONS_VALUE="-c app.postgres_password=${POSTGRES_PASSWORD}"
PSQL=(docker compose --env-file "${ENV_FILE}" -f "${ROOT_DIR}/infra/docker-compose.yml" exec -T -e "PGOPTIONS=${PGOPTIONS_VALUE}" postgres psql -v ON_ERROR_STOP=1 -U postgres -d superset)

"${PSQL[@]}" <<'SQL'
UPDATE dbs
   SET encrypted_extra = NULL
 WHERE encrypted_extra IS NOT NULL
   AND database_name IN ('modecissions_gold', 'Gold', 'OMEGA Gold');
SQL

echo "[local-repair] Superset metastore encrypted DB fields cleared for local gold connections"
