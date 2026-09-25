#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

EXPLICIT_CONSOLE_URL="${CONSOLE_URL:-}"
EXPLICIT_SUPERSET_PUBLIC_URL="${SUPERSET_PUBLIC_URL:-}"
CONSOLE_URL="${EXPLICIT_CONSOLE_URL:-http://127.0.0.1:8000}"
SUPERSET_PUBLIC_URL="${EXPLICIT_SUPERSET_PUBLIC_URL:-http://127.0.0.1:8088}"
REMOTE_MODE="${OMEGA_PRODUCTION_READINESS_REMOTE:-0}"
STRICT_V1_MODE="${OMEGA_PRODUCTION_READINESS_V1:-0}"
PUBLISH_MODE="${OMEGA_RELEASE_PUBLISH_ONLY:-0}"
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

release_pytest() {
  if [[ "${OMEGA_RELEASE_DIGEST_STACK:-0}" == "1" ]]; then
    "${PYTHON_BIN}" -I scripts/run_release_pytest.py "$@"
  else
    "${PYTHON_BIN}" -m pytest "$@"
  fi
}

verify_release_harness() {
  if [[ "${OMEGA_RELEASE_DIGEST_STACK:-0}" == "1" ]]; then
    local observed_verifier_sha256
    observed_verifier_sha256="$(
      "${PYTHON_BIN}" -I -c \
        'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' \
        scripts/verify_release_test_harness.py
    )"
    if [[ ! "${OMEGA_RELEASE_TEST_HARNESS_VERIFIER_SHA256:-}" =~ ^[0-9a-f]{64}$ ||
          "${observed_verifier_sha256}" != "${OMEGA_RELEASE_TEST_HARNESS_VERIFIER_SHA256}" ]]; then
      log "BLOCKED: release harness verifier differs from action-bound authority"
      exit 2
    fi
    "${PYTHON_BIN}" -I scripts/verify_release_test_harness.py
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
    if [[ "${OMEGA_RELEASE_DIGEST_STACK:-0}" == "1" ]]; then
      if [[ "${OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT:-}" != "release" ||
            "${OMEGA_RELEASE_TEST_SKIP_POLICY:-}" != "${PWD}/.github/release-test-skip-policy.json" ]]; then
        log "BLOCKED: release digest stack requires the exact release skip policy"
        exit 2
      fi
      while IFS= read -r dotenv_line || [[ -n "${dotenv_line}" ]]; do
        [[ "${dotenv_line}" =~ ^[[:space:]]*($|#) ]] && continue
        if [[ ! "${dotenv_line}" =~ ^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*= ]] ||
           [[ "${dotenv_line}" == *'$('* || "${dotenv_line}" == *'`'* ||
              "${dotenv_line}" == *';'* || "${dotenv_line}" == *'&&'* ||
              "${dotenv_line}" == *'||'* || "${dotenv_line}" == *'<('* ||
              "${dotenv_line}" == *'>('* ]]; then
          log "BLOCKED: infra/.env is not a passive dotenv assignment file"
          exit 2
        fi
      done < infra/.env
      local reserved_pattern='^[[:space:]]*(export[[:space:]]+)?(OMEGA_RELEASE_[A-Za-z0-9_]*|OMEGA_STRESS_[A-Za-z0-9_]*|OMEGA_PRODUCTION_READINESS_SKIP_STRESS|OMEGA_TEST_GRANTS_DSN|PYTHON_BIN|PYTHONOPTIMIZE|PYTHONPATH|PYTHONHOME|PYTHONSTARTUP|PYTEST_[A-Za-z0-9_]*|PLAYWRIGHT_[A-Za-z0-9_]*|PATH|DOCKER_[A-Za-z0-9_]*|COMPOSE_[A-Za-z0-9_]*|GITHUB_[A-Za-z0-9_]*|GIT_[A-Za-z0-9_]*|RUNNER_[A-Za-z0-9_]*|NPM_CONFIG_[A-Za-z0-9_]*|npm_config_[A-Za-z0-9_]*|CI|MAKEFLAGS|GNUMAKEFLAGS|MAKEOVERRIDES|MFLAGS|MAKELEVEL|BASH_ENV|BASHOPTS|SHELLOPTS|ENV|SHELL|NODE_OPTIONS|NODE_PATH|LD_[A-Za-z0-9_]*|DYLD_[A-Za-z0-9_]*|CDPATH|GLOBIGNORE|IFS)='
      if grep -Eq "${reserved_pattern}" infra/.env; then
        log "BLOCKED: infra/.env attempts to override a release-gate control"
        exit 2
      fi
    fi
    local -r expected_digest_stack="${OMEGA_RELEASE_DIGEST_STACK-__UNSET__}"
    local -r expected_skip_environment="${OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT-__UNSET__}"
    local -r expected_skip_policy="${OMEGA_RELEASE_TEST_SKIP_POLICY-__UNSET__}"
    local -r expected_stress_skip="${OMEGA_PRODUCTION_READINESS_SKIP_STRESS-__UNSET__}"
    local -r expected_python_bin="${PYTHON_BIN-__UNSET__}"
    local -r expected_path="${PATH-__UNSET__}"
    local -r expected_docker_host="${DOCKER_HOST-__UNSET__}"
    local -r expected_docker_context="${DOCKER_CONTEXT-__UNSET__}"
    local release_env_file
    release_env_file="$(mktemp)"
    "${PYTHON_BIN}" -I scripts/load_release_dotenv.py \
      --input infra/.env --output "${release_env_file}"
    while IFS= read -r -d '' release_key && IFS= read -r -d '' release_value; do
      export "${release_key}=${release_value}"
    done < "${release_env_file}"
    rm -f "${release_env_file}"
    if [[ "${expected_digest_stack}" == "1" ]] && {
      [[ "${OMEGA_RELEASE_DIGEST_STACK-__UNSET__}" != "${expected_digest_stack}" ]] ||
      [[ "${OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT-__UNSET__}" != "${expected_skip_environment}" ]] ||
      [[ "${OMEGA_RELEASE_TEST_SKIP_POLICY-__UNSET__}" != "${expected_skip_policy}" ]] ||
      [[ "${OMEGA_PRODUCTION_READINESS_SKIP_STRESS-__UNSET__}" != "${expected_stress_skip}" ]] ||
      [[ "${PYTHON_BIN-__UNSET__}" != "${expected_python_bin}" ]] ||
      [[ "${PATH-__UNSET__}" != "${expected_path}" ]] ||
      [[ "${DOCKER_HOST-__UNSET__}" != "${expected_docker_host}" ]] ||
      [[ "${DOCKER_CONTEXT-__UNSET__}" != "${expected_docker_context}" ]];
    }; then
      log "BLOCKED: infra/.env changed a release-gate control"
      exit 2
    fi
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
  if [[ "${REMOTE_MODE}" == "1" ]] && command -v docker >/dev/null 2>&1 \
    && docker inspect mode_console >/dev/null 2>&1; then
    docker exec mode_console python - <<'PY'
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
    return
  fi
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
  PYTHONPATH=console release_pytest -q \
    console/tests/test_security_context_scope.py \
    tests/test_llm_client_config.py \
    tests/test_intelligence_engine_contract.py \
    tests/test_operational_native_rls.py \
    tests/test_decisions_workspace_isolation.py \
    tests/test_workspace_decisions_workspace_filter.py \
    console/tests/test_vault_reveal_pair_keys.py

  PYTHONPATH=vault release_pytest -q \
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

prepare_local_browser_e2e_env() {
  export AIRFLOW_URL="${OMEGA_E2E_AIRFLOW_URL:-http://127.0.0.1:8082}"
  export SUPERSET_URL="${OMEGA_E2E_SUPERSET_URL:-http://127.0.0.1:8088}"
  export MINIO_CONSOLE_URL="${OMEGA_E2E_MINIO_CONSOLE_URL:-http://127.0.0.1:9001}"
  export MAILHOG_URL="${OMEGA_E2E_MAILHOG_URL:-http://127.0.0.1:8025}"
  export HUBSPOT_URL="${OMEGA_E2E_HUBSPOT_URL:-http://127.0.0.1:8210}"
  export REPLICON_URL="${OMEGA_E2E_REPLICON_URL:-http://127.0.0.1:8201}"
  export SAP_HCM_URL="${OMEGA_E2E_SAP_HCM_URL:-http://127.0.0.1:8202}"
  export SAP_SF_URL="${OMEGA_E2E_SAP_SF_URL:-http://127.0.0.1:8203}"
  export SAP_S4_URL="${OMEGA_E2E_SAP_S4_URL:-http://127.0.0.1:8204}"
}

prepare_local_release_test_env() {
  local encoded_password
  export OMEGA_ENABLE_E2E_SMOKE=1
  export OMEGA_ENABLE_LIVE_STACK_TESTS=1
  export E2E_ADMIN_EMAIL="${E2E_ADMIN_EMAIL:-${TEST_EMAIL:-admin@example.com}}"
  export E2E_ADMIN_PASSWORD="${E2E_ADMIN_PASSWORD:-${TEST_PASSWORD:-${BOOTSTRAP_ADMIN_PASSWORD:-${ADMIN_PASSWORD:-}}}}"
  if [[ -z "${E2E_ADMIN_PASSWORD}" ]]; then
    log "E2E_ADMIN_PASSWORD/TEST_PASSWORD/bootstrap admin password is required"
    exit 2
  fi
  if [[ -z "${OMEGA_TEST_GRANTS_DSN:-}" ]]; then
    if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
      log "POSTGRES_PASSWORD is required for the analytic grant release tests"
      exit 2
    fi
    encoded_password="$(
      POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" "${PYTHON_BIN}" - <<'PY'
import os
from urllib.parse import quote

print(quote(os.environ["POSTGRES_PASSWORD"], safe=""))
PY
    )"
    export OMEGA_TEST_GRANTS_DSN="postgresql://postgres:${encoded_password}@127.0.0.1:15432/modecissions"
  fi
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
  verify_release_harness
  apply_v1_live_defaults
  require_v1_live_inputs

  if [[ "${REMOTE_MODE}" == "1" ]]; then
    run_remote_gate
    return
  fi

  require_command docker

  log "validating docker compose config"
  if [[ "${OMEGA_RELEASE_DIGEST_STACK:-0}" == "1" ]]; then
    docker compose --env-file infra/.env \
      -f infra/docker-compose.yml \
      -f infra/docker-compose.dev.yml \
      -f infra/terraform-gcp/release/docker-compose.release.yml \
      --profile sap config -q
  else
    docker compose --env-file infra/.env -f infra/docker-compose.yml --profile sap config -q
  fi

  log "waiting for healthy stack"
  bash scripts/wait_for_health.sh

  prepare_local_release_test_env

  log "running full-stack acceptance to warm Bronze/Silver/Gold data"
  E2E_REQUIRE_STACK=1 make acceptance

  check_readyz_data
  check_superset_login

  if [[ "${PUBLISH_MODE}" == "1" ]]; then
    log "publication acceptance complete; deferring runtime verification to the post-gate checks"
    log "PASS"
    return
  fi

  check_live_llm_if_required
  run_scope_regression_tests
  run_multiuser_simulation_if_required

  log "running backend, cartridge, RLS, and security tests"
  if [[ "${OMEGA_RELEASE_DIGEST_STACK:-0}" == "1" ]]; then
    make PYTEST="${PYTHON_BIN} -I scripts/run_release_pytest.py" test
  else
    make test
  fi

  log "running smoke"
  make smoke

  log "running browser E2E"
  prepare_local_browser_e2e_env
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
