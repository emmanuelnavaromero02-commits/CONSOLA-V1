#!/usr/bin/env bash
# Heavy full-stack acceptance for the local stack.
#
# This is intentionally not a browser-only E2E. It validates:
# Console auth/admin/settings/control-room/copilot/studio surfaces,
# HubSpot test_connection against a deterministic fake upstream,
# MCP extraction jobs, Bronze Parquet, Silver refresh, Gold materialization,
# catalog/semantic/lineage queryability.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=(docker compose --env-file infra/.env -f infra/docker-compose.yml)
if [[ "${OMEGA_ACCEPTANCE_USE_DEV_OVERRIDE:-1}" == "1" && -f infra/docker-compose.dev.yml ]]; then
  COMPOSE+=(-f infra/docker-compose.dev.yml)
fi
COMPOSE+=(--profile sap)
PYTEST="${PYTEST:-.venv/bin/pytest}"
if [[ ! -x "$PYTEST" ]]; then
  PYTEST="pytest"
fi

FAKE_PORT="${FAKE_HUBSPOT_PORT:-18030}"
FAKE_TOKEN="${FAKE_HUBSPOT_TOKEN:-pat-na1-acceptance-token}"
FAKE_BASE_URL="http://host.docker.internal:${FAKE_PORT}"

if [[ -z "${E2E_ADMIN_PASSWORD:-}" && -f infra/.env ]]; then
  E2E_ADMIN_PASSWORD="$(
    grep -E '^(BOOTSTRAP_ADMIN_PASSWORD|ADMIN_PASSWORD)=' infra/.env \
      | head -1 \
      | cut -d= -f2- \
      | tr -d '"' \
      | tr -d "'" || true
  )"
  export E2E_ADMIN_PASSWORD
fi

if [[ -z "${E2E_ADMIN_PASSWORD:-}" && -f tests-e2e/.env ]]; then
  E2E_ADMIN_EMAIL="${E2E_ADMIN_EMAIL:-$(
    grep -E '^TEST_EMAIL=' tests-e2e/.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true
  )}"
  E2E_ADMIN_PASSWORD="$(
    grep -E '^TEST_PASSWORD=' tests-e2e/.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true
  )"
  export E2E_ADMIN_EMAIL E2E_ADMIN_PASSWORD
fi

if [[ -z "${E2E_ADMIN_PASSWORD:-}" && -f tests-e2e/.env.example ]]; then
  E2E_ADMIN_EMAIL="${E2E_ADMIN_EMAIL:-$(
    grep -E '^TEST_EMAIL=' tests-e2e/.env.example | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true
  )}"
  E2E_ADMIN_PASSWORD="$(
    grep -E '^TEST_PASSWORD=' tests-e2e/.env.example | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true
  )"
  export E2E_ADMIN_EMAIL E2E_ADMIN_PASSWORD
fi

if [[ -z "${E2E_ADMIN_PASSWORD:-}" ]]; then
  echo "[acceptance] E2E_ADMIN_PASSWORD is required."
  echo "[acceptance] Export it with the bootstrap admin password used by this stack."
  exit 2
fi

cleanup() {
  status=$?
  if [[ -n "${FAKE_PID:-}" ]]; then
    kill "$FAKE_PID" >/dev/null 2>&1 || true
    wait "$FAKE_PID" >/dev/null 2>&1 || true
  fi
  echo "[acceptance] restoring HubSpot container without fake upstream env"
  "${COMPOSE[@]}" up -d --force-recreate --no-deps hubspot >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

echo "[acceptance] ensuring patched services are built"
"${COMPOSE[@]}" build refinement hubspot console
"${COMPOSE[@]}" up -d --no-deps refinement hubspot console

echo "[acceptance] applying pending DB migrations"
OMEGA_MIGRATION_BOOTSTRAP_MODE=1 \
OMEGA_MIGRATION_ALLOW_BOOTSTRAP_LEDGER=1 \
OMEGA_MIGRATION_ENVIRONMENT=development \
scripts/run_db_migrations.py

echo "[acceptance] starting fake HubSpot upstream on host port ${FAKE_PORT}"
FAKE_HUBSPOT_PORT="$FAKE_PORT" FAKE_HUBSPOT_TOKEN="$FAKE_TOKEN" \
  python3 tests/fixtures/fake_hubspot_api.py &
FAKE_PID=$!
sleep 1

echo "[acceptance] pointing HubSpot cartridge at ${FAKE_BASE_URL}"
HUBSPOT_BASE_URL="$FAKE_BASE_URL" HUBSPOT_API_TOKEN="$FAKE_TOKEN" \
  "${COMPOSE[@]}" up -d --force-recreate --no-deps hubspot

echo "[acceptance] waiting for HubSpot health"
for _ in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:8210/health >/dev/null; then
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:8210/health >/dev/null

echo "[acceptance] running heavy acceptance pytest"
E2E_REQUIRE_STACK=1 \
OMEGA_FULL_STACK_ACCEPTANCE=1 \
OMEGA_HUBSPOT_BASE=http://127.0.0.1:8210 \
E2E_ADMIN_EMAIL="${E2E_ADMIN_EMAIL:-admin@example.com}" \
E2E_ADMIN_PASSWORD="$E2E_ADMIN_PASSWORD" \
  "$PYTEST" tests/e2e/test_console_full_stack_acceptance.py -v "$@"

echo "[acceptance] full-stack acceptance passed"
