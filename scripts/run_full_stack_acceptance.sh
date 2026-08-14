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
RELEASE_MODE=0
UP_LOCK_ARGS=()
if [[ "${OMEGA_RELEASE_DIGEST_STACK:-0}" == "1" ]]; then
  RELEASE_MODE=1
  [[ -f infra/docker-compose.dev.yml ]] || { echo "[acceptance] release dev overlay is missing"; exit 2; }
  [[ -f infra/terraform-gcp/release/docker-compose.release.yml ]] || { echo "[acceptance] release digest overlay is missing"; exit 2; }
  COMPOSE+=(-f infra/docker-compose.dev.yml)
  COMPOSE+=(-f infra/terraform-gcp/release/docker-compose.release.yml)
  UP_LOCK_ARGS=(--no-build --pull never)
elif [[ "${OMEGA_ACCEPTANCE_USE_DEV_OVERRIDE:-1}" == "1" && -f infra/docker-compose.dev.yml ]]; then
  COMPOSE+=(-f infra/docker-compose.dev.yml)
fi
COMPOSE+=(--profile sap)
if [[ "${OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT:-}" == "release" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-python3}"
  PYTEST_CMD=("${PYTHON_BIN}" scripts/run_release_pytest.py)
else
  PYTEST_BIN="${PYTEST:-.venv/bin/pytest}"
  if [[ ! -x "${PYTEST_BIN}" ]]; then
    PYTEST_BIN="pytest"
  fi
  PYTEST_CMD=("${PYTEST_BIN}")
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
  restore_status=0
  "${COMPOSE[@]}" up -d "${UP_LOCK_ARGS[@]}" --force-recreate --no-deps hubspot >/dev/null 2>&1 || restore_status=$?
  if [[ "${status}" -eq 0 && "${restore_status}" -ne 0 ]]; then
    status="${restore_status}"
  fi
  exit "$status"
}
trap cleanup EXIT

if [[ "${RELEASE_MODE}" == "1" ]]; then
  echo "[acceptance] recreating exact digest services without build or pull"
else
  echo "[acceptance] ensuring patched services are built"
  "${COMPOSE[@]}" build refinement hubspot console
fi
"${COMPOSE[@]}" up -d "${UP_LOCK_ARGS[@]}" --no-deps refinement hubspot console

echo "[acceptance] applying pending DB migrations"
bash scripts/apply_db_migrations.sh

echo "[acceptance] starting fake HubSpot upstream on host port ${FAKE_PORT}"
FAKE_HUBSPOT_PORT="$FAKE_PORT" FAKE_HUBSPOT_TOKEN="$FAKE_TOKEN" \
  python3 tests/fixtures/fake_hubspot_api.py &
FAKE_PID=$!
sleep 1

echo "[acceptance] pointing HubSpot cartridge at ${FAKE_BASE_URL}"
HUBSPOT_BASE_URL="$FAKE_BASE_URL" HUBSPOT_API_TOKEN="$FAKE_TOKEN" \
  "${COMPOSE[@]}" up -d "${UP_LOCK_ARGS[@]}" --force-recreate --no-deps hubspot

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
  "${PYTEST_CMD[@]}" tests/e2e/test_console_full_stack_acceptance.py -v "$@"

echo "[acceptance] full-stack acceptance passed"
