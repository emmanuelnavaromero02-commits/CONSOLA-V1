#!/usr/bin/env bash
# Production-readiness gate for a running local/prod-like OMEGA stack.
#
# This script intentionally does not start or deploy cloud resources. It proves
# the checked-out code can pass the same local gates an operator needs before a
# beta/production candidate: strict readiness, Superset auth, tests, smoke,
# browser E2E, heavy acceptance, and beta stress.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

EXPLICIT_CONSOLE_URL="${CONSOLE_URL:-}"
EXPLICIT_SUPERSET_PUBLIC_URL="${SUPERSET_PUBLIC_URL:-}"
CONSOLE_URL="${EXPLICIT_CONSOLE_URL:-http://127.0.0.1:8000}"
SUPERSET_PUBLIC_URL="${EXPLICIT_SUPERSET_PUBLIC_URL:-http://127.0.0.1:8088}"
REMOTE_MODE="${OMEGA_PRODUCTION_READINESS_REMOTE:-0}"
STRICT_V1_MODE="${OMEGA_PRODUCTION_READINESS_V1:-0}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

log() {
  printf '[production-readiness] %s\n' "$*"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "missing required command: $1"
    exit 2
  fi
}

json_ok_field() {
  "${PYTHON_BIN}" - "$1" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("ok") is not True:
    raise SystemExit(1)
PY
}

source_env() {
  if [[ -f infra/.env ]]; then
    set -a
    # shellcheck disable=SC1091
    source infra/.env
    set +a
  fi
  if [[ -n "${EXPLICIT_CONSOLE_URL}" ]]; then
    CONSOLE_URL="${EXPLICIT_CONSOLE_URL}"
  else
    CONSOLE_URL="${CONSOLE_URL:-http://127.0.0.1:8000}"
  fi
  if [[ -n "${EXPLICIT_SUPERSET_PUBLIC_URL}" ]]; then
    SUPERSET_PUBLIC_URL="${EXPLICIT_SUPERSET_PUBLIC_URL}"
  else
    SUPERSET_PUBLIC_URL="${SUPERSET_PUBLIC_URL:-http://127.0.0.1:8088}"
  fi
}

apply_v1_live_defaults() {
  if [[ "${STRICT_V1_MODE}" != "1" ]]; then
    return
  fi
  if [[ "${OMEGA_PRODUCTION_READINESS_SKIP_STRESS:-0}" == "1" ]]; then
    log "v1 live readiness refuses OMEGA_PRODUCTION_READINESS_SKIP_STRESS=1"
    exit 2
  fi
  export OMEGA_REQUIRE_LIVE_LLM="${OMEGA_REQUIRE_LIVE_LLM:-1}"
  export OMEGA_PRODUCTION_READINESS_RUN_MULTIUSER_SIM="${OMEGA_PRODUCTION_READINESS_RUN_MULTIUSER_SIM:-1}"
  export OMEGA_STRESS_REQUIRE_LIVE_LLM="${OMEGA_STRESS_REQUIRE_LIVE_LLM:-1}"
  log "v1 live readiness enabled: stress, multi-user simulation, and live LLM probes are required"
}

require_v1_live_inputs() {
  if [[ "${STRICT_V1_MODE}" != "1" ]]; then
    return
  fi
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    log "BLOCKED: ANTHROPIC_API_KEY is required for OMEGA_PRODUCTION_READINESS_V1=1"
    exit 2
  fi
  if [[ -z "${E2E_ADMIN_PASSWORD:-${TEST_PASSWORD:-}}" ]]; then
    log "BLOCKED: E2E_ADMIN_PASSWORD or TEST_PASSWORD is required for OMEGA_PRODUCTION_READINESS_V1=1"
    exit 2
  fi
}

check_readyz_data() {
  log "checking strict data readiness"
  local readiness_url="${CONSOLE_URL}/readyz?require_data=1"
  if [[ "${STRICT_V1_MODE}" == "1" ]]; then
    readiness_url="${readiness_url}&require_intelligence=1"
  fi
  body="$(curl -fsS --max-time 10 "${readiness_url}")"
  if ! json_ok_field "${body}"; then
    log "/readyz?require_data=1 did not report ok=true"
    printf '%s\n' "${body}"
    exit 1
  fi
}

check_public_runtime() {
  log "checking public runtime health"
  curl -fsS --max-time 10 "${CONSOLE_URL}/healthz" >/dev/null
  curl -fsS --max-time 10 "${CONSOLE_URL}/readyz" >/dev/null
  check_readyz_data
}

check_superset_login() {
  log "checking Superset service/admin login"
  local user password response
  user="${SUPERSET_SERVICE_USER:-${SUPERSET_ADMIN_USER:-}}"
  password="${SUPERSET_SERVICE_PASSWORD:-${SUPERSET_ADMIN_PASSWORD:-}}"
  if [[ -z "${user}" || -z "${password}" ]]; then
    log "SUPERSET_SERVICE_USER/PASSWORD or SUPERSET_ADMIN_USER/PASSWORD is required"
    exit 2
  fi
  response="$(curl -fsS --max-time 15 \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"${user}\",\"password\":\"${password}\",\"provider\":\"db\",\"refresh\":true}" \
    "${SUPERSET_PUBLIC_URL%/}/api/v1/security/login")"
  "${PYTHON_BIN}" - "${response}" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if not payload.get("access_token"):
    raise SystemExit("Superset login response did not include access_token")
PY
}

check_live_llm_if_required() {
  if [[ "${OMEGA_REQUIRE_LIVE_LLM:-0}" != "1" ]]; then
    log "live LLM probe skipped; set OMEGA_REQUIRE_LIVE_LLM=1 to require Anthropic"
    return
  fi
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    log "ANTHROPIC_API_KEY is required when OMEGA_REQUIRE_LIVE_LLM=1"
    exit 2
  fi
  log "checking live Anthropic chat path"
  PYTHONPATH=console "${PYTHON_BIN}" - <<'PY'
import asyncio

from app.services import llm_client


async def main() -> None:
    reply, _urls, _messages = await llm_client.chat(
        system="Responde solo OK.",
        messages=[{"role": "user", "content": "OK"}],
        tools=[],
        invoke_tool=None,
        tool_server_map={},
        max_tokens=16,
        temperature=0,
    )
    if not str(reply or "").strip():
        raise SystemExit("Anthropic returned an empty reply")


asyncio.run(main())
PY
}

run_scope_regression_tests() {
  log "running scoped Vault, pipeline, RLS, ownership, and API-v1 regression tests"
  PYTHONPATH=console "${PYTHON_BIN}" -m pytest -q \
    console/tests/test_security_context_scope.py \
    tests/test_llm_client_config.py \
    tests/test_intelligence_engine_contract.py \
    tests/test_operational_native_rls.py \
    tests/test_decisions_workspace_isolation.py \
    tests/test_workspace_decisions_workspace_filter.py \
    console/tests/test_vault_reveal_pair_keys.py

  PYTHONPATH=vault "${PYTHON_BIN}" -m pytest -q \
    vault/tests/test_vault_workspace_scope.py \
    vault/tests/test_vault_connections.py \
    vault/tests/test_internal_key_per_pair_vault.py
}

run_multiuser_simulation_if_required() {
  if [[ "${OMEGA_PRODUCTION_READINESS_RUN_MULTIUSER_SIM:-0}" != "1" ]]; then
    log "multi-user isolation simulation skipped; set OMEGA_PRODUCTION_READINESS_RUN_MULTIUSER_SIM=1 to require it"
    return
  fi
  if [[ ! -x "scripts/run_multiuser_isolation_simulation.sh" ]]; then
    log "scripts/run_multiuser_isolation_simulation.sh is required when OMEGA_PRODUCTION_READINESS_RUN_MULTIUSER_SIM=1"
    exit 2
  fi
  log "running multi-user tenant/workspace/employee isolation simulation"
  bash scripts/run_multiuser_isolation_simulation.sh
}

run_remote_e2e_if_required() {
  if [[ "${OMEGA_PRODUCTION_READINESS_REMOTE_RUN_E2E:-0}" != "1" ]]; then
    log "remote Playwright E2E skipped; set OMEGA_PRODUCTION_READINESS_REMOTE_RUN_E2E=1 to require it"
    return
  fi
  if [[ -z "${TEST_PASSWORD:-${E2E_ADMIN_PASSWORD:-}}" ]]; then
    log "TEST_PASSWORD or E2E_ADMIN_PASSWORD is required for remote Playwright E2E"
    exit 2
  fi
  log "running remote browser E2E against ${CONSOLE_URL}"
  (
    cd tests-e2e
    if [[ ! -d node_modules ]]; then
      npm ci
    fi
    BASE_URL="${CONSOLE_URL}" \
    BACKEND_URL="${CONSOLE_URL}" \
    LEGACY_URL="${CONSOLE_URL}" \
    CONTROL_ROOM_URL="${CONSOLE_URL}/control-room" \
    OPEN_REPORT=0 \
      npx playwright test
  )
}

run_remote_gate() {
  require_command curl
  source_env
  apply_v1_live_defaults
  require_v1_live_inputs

  check_public_runtime
  if [[ "${OMEGA_REQUIRE_SUPERSET_LOGIN:-1}" == "1" ]]; then
    check_superset_login
  else
    log "Superset login skipped by OMEGA_REQUIRE_SUPERSET_LOGIN=0"
  fi
  check_live_llm_if_required
  run_remote_e2e_if_required

  log "PASS"
}

run_gate() {
  require_command curl
  source_env
  apply_v1_live_defaults
  require_v1_live_inputs

  if [[ "${REMOTE_MODE}" == "1" ]]; then
    run_remote_gate
    return
  fi

  require_command docker

  log "validating docker compose config"
  docker compose --env-file infra/.env -f infra/docker-compose.yml --profile sap config -q

  log "waiting for healthy stack"
  bash scripts/wait_for_health.sh

  log "running full-stack acceptance to warm Bronze/Silver/Gold data"
  make acceptance

  check_readyz_data
  check_superset_login
  check_live_llm_if_required
  run_scope_regression_tests
  run_multiuser_simulation_if_required

  log "running backend, cartridge, RLS, and security tests"
  make test

  log "running smoke"
  make smoke

  log "running browser E2E"
  OPEN_REPORT=0 make e2e

  if [[ "${OMEGA_PRODUCTION_READINESS_SKIP_STRESS:-0}" == "1" ]]; then
    log "stress skipped by OMEGA_PRODUCTION_READINESS_SKIP_STRESS=1"
  else
    log "running beta stress profile"
    OMEGA_STRESS_PROFILE="${OMEGA_STRESS_PROFILE:-beta}" \
      OMEGA_STRESS_WARM_ACCEPTANCE="${OMEGA_STRESS_WARM_ACCEPTANCE:-0}" \
      make stress
  fi

  log "PASS"
}

run_gate "$@"
