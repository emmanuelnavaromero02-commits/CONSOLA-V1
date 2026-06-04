#!/usr/bin/env bash
# Deterministic local stress runner for OMEGA.
#
# Default mode is safe for local development:
#   - uses authenticated console traffic
#   - points HubSpot at the deterministic fake upstream
#   - keeps extract/refresh writes disabled unless explicitly enabled
#   - writes reports under artifacts/stress/<timestamp>/ (gitignored)
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=(docker compose --env-file infra/.env -f infra/docker-compose.yml --profile sap)
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

STRESS_PROFILE="${OMEGA_STRESS_PROFILE:-local}"
case "$STRESS_PROFILE" in
  local)
    DEFAULT_USERS=10
    DEFAULT_SPAWN_RATE=2
    DEFAULT_RUN_TIME="2m"
    DEFAULT_INTERNAL_PROBES="${OMEGA_STRESS_ENABLE_INTERNAL_PROBES:-0}"
    DEFAULT_WRITES="${OMEGA_STRESS_ENABLE_WRITES:-0}"
    ;;
  beta)
    DEFAULT_USERS=100
    DEFAULT_SPAWN_RATE=10
    DEFAULT_RUN_TIME="10m"
    DEFAULT_INTERNAL_PROBES="${OMEGA_STRESS_ENABLE_INTERNAL_PROBES:-1}"
    DEFAULT_WRITES="${OMEGA_STRESS_ENABLE_WRITES:-1}"
    ;;
  production)
    DEFAULT_USERS=500
    DEFAULT_SPAWN_RATE=25
    DEFAULT_RUN_TIME="30m"
    DEFAULT_INTERNAL_PROBES="${OMEGA_STRESS_ENABLE_INTERNAL_PROBES:-1}"
    DEFAULT_WRITES="${OMEGA_STRESS_ENABLE_WRITES:-1}"
    ;;
  *)
    echo "[stress] unsupported OMEGA_STRESS_PROFILE=${STRESS_PROFILE}; expected local|beta|production"
    exit 2
    ;;
esac

STRESS_HOST="${OMEGA_STRESS_HOST:-http://127.0.0.1:8000}"
STRESS_USERS="${OMEGA_STRESS_USERS:-$DEFAULT_USERS}"
STRESS_SPAWN_RATE="${OMEGA_STRESS_SPAWN_RATE:-$DEFAULT_SPAWN_RATE}"
STRESS_RUN_TIME="${OMEGA_STRESS_RUN_TIME:-$DEFAULT_RUN_TIME}"
STRESS_STATS_INTERVAL="${OMEGA_STRESS_STATS_INTERVAL:-5}"
STRESS_ARTIFACT_DIR="${OMEGA_STRESS_ARTIFACT_DIR:-artifacts/stress/$(date -u +%Y%m%dT%H%M%SZ)}"
FAKE_PORT="${FAKE_HUBSPOT_PORT:-18030}"
FAKE_TOKEN="${FAKE_HUBSPOT_TOKEN:-pat-na1-stress-token}"
FAKE_BASE_URL="http://host.docker.internal:${FAKE_PORT}"
ENABLE_WRITES="$DEFAULT_WRITES"
export OMEGA_STRESS_ENABLE_WRITES="$ENABLE_WRITES"
export OMEGA_STRESS_ENABLE_INTERNAL_PROBES="$DEFAULT_INTERNAL_PROBES"

mkdir -p "$STRESS_ARTIFACT_DIR"

if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import locust  # noqa: F401
PY
then
  echo "[stress] Locust is not installed for ${PYTHON_BIN}."
  echo "[stress] Install it with: ${PYTHON_BIN} -m pip install -r tests/stress/requirements.txt"
  exit 2
fi

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
  echo "[stress] E2E_ADMIN_PASSWORD is required."
  echo "[stress] Export it with the bootstrap admin password used by this stack."
  exit 2
fi

if ! curl -fsS "${STRESS_HOST}/healthz" >/dev/null 2>&1; then
  if [[ "${OMEGA_STRESS_START_STACK:-0}" == "1" ]]; then
    echo "[stress] stack not reachable; starting full local stack"
    bash infra/bootstrap.sh
    bash infra/bootstrap-keys.sh infra/.env
    "${COMPOSE[@]}" up -d --build
  else
    echo "[stress] ${STRESS_HOST}/healthz is not reachable."
    echo "[stress] Start the stack first with: make up"
    echo "[stress] Or set OMEGA_STRESS_START_STACK=1 to let this runner start it."
    exit 2
  fi
fi

cleanup() {
  status=$?
  if [[ -n "${STATS_PID:-}" ]]; then
    kill "$STATS_PID" >/dev/null 2>&1 || true
    wait "$STATS_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "${FAKE_PID:-}" ]]; then
    kill "$FAKE_PID" >/dev/null 2>&1 || true
    wait "$FAKE_PID" >/dev/null 2>&1 || true
  fi
  if [[ "${FAKE_WAS_STARTED:-0}" == "1" ]]; then
    echo "[stress] restoring HubSpot container without fake upstream env"
    "${COMPOSE[@]}" up -d --force-recreate --no-deps hubspot >/dev/null 2>&1 || true
  fi
  echo "[stress] artifacts: ${STRESS_ARTIFACT_DIR}"
  exit "$status"
}
trap cleanup EXIT

if [[ "${OMEGA_STRESS_WARM_ACCEPTANCE:-0}" == "1" ]]; then
  echo "[stress] warming data through full-stack acceptance"
  bash scripts/run_full_stack_acceptance.sh
fi

if [[ "$ENABLE_WRITES" =~ ^(1|true|yes)$ && "${OMEGA_STRESS_RESET_HUBSPOT_DERIVED:-1}" =~ ^(1|true|yes)$ ]]; then
  # Local MinIO on Docker Desktop can leave overwritten parquet objects in a
  # deadlocked state. Write stress is intentionally destructive, so reset the
  # specific derived outputs that this run will rewrite; raw Bronze remains
  # intact and Gold is reset only when Gold refresh is enabled for this run.
  if [[ -d data/lakehouse/lakehouse ]]; then
    echo "[stress] resetting local HubSpot derived parquet outputs"
    rm -rf data/lakehouse/lakehouse/silver/hubspot
    if [[ "${OMEGA_STRESS_ENABLE_GOLD_REFRESH:-0}" =~ ^(1|true|yes)$ ]]; then
      rm -rf data/lakehouse/lakehouse/gold/hubspot
    fi
    "${COMPOSE[@]}" restart minio >/dev/null
    for _ in $(seq 1 45); do
      if docker inspect -f '{{.State.Health.Status}}' mode_minio 2>/dev/null | grep -q healthy; then
        break
      fi
      sleep 1
    done
  fi
fi

if [[ "${OMEGA_STRESS_FAKE_HUBSPOT:-1}" == "1" && "${OMEGA_STRESS_LIVE:-0}" != "1" ]]; then
  echo "[stress] starting fake HubSpot upstream on host port ${FAKE_PORT}"
  FAKE_HUBSPOT_PORT="$FAKE_PORT" FAKE_HUBSPOT_TOKEN="$FAKE_TOKEN" \
    python3 tests/fixtures/fake_hubspot_api.py >"${STRESS_ARTIFACT_DIR}/fake_hubspot.log" 2>&1 &
  FAKE_PID=$!
  FAKE_WAS_STARTED=1
  sleep 1

  echo "[stress] pointing HubSpot cartridge at ${FAKE_BASE_URL}"
  HUBSPOT_BASE_URL="$FAKE_BASE_URL" HUBSPOT_API_TOKEN="$FAKE_TOKEN" \
    "${COMPOSE[@]}" up -d --force-recreate --no-deps hubspot

  for _ in $(seq 1 45); do
    if curl -fsS http://127.0.0.1:8210/health >/dev/null; then
      break
    fi
    sleep 1
  done
  curl -fsS http://127.0.0.1:8210/health >/dev/null
  export OMEGA_STRESS_REQUIRE_HUBSPOT_OK="${OMEGA_STRESS_REQUIRE_HUBSPOT_OK:-1}"
else
  export OMEGA_STRESS_REQUIRE_HUBSPOT_OK="${OMEGA_STRESS_REQUIRE_HUBSPOT_OK:-0}"
fi

(
  while true; do
    date -u +"%Y-%m-%dT%H:%M:%SZ"
    docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}' \
      | grep -E '(^NAME|mode_)' || true
    echo ""
    sleep "$STRESS_STATS_INTERVAL"
  done
) >"${STRESS_ARTIFACT_DIR}/docker_stats.log" 2>&1 &
STATS_PID=$!

cat >"${STRESS_ARTIFACT_DIR}/run.env" <<EOF
OMEGA_STRESS_HOST=${STRESS_HOST}
OMEGA_STRESS_PROFILE=${STRESS_PROFILE}
OMEGA_STRESS_USERS=${STRESS_USERS}
OMEGA_STRESS_SPAWN_RATE=${STRESS_SPAWN_RATE}
OMEGA_STRESS_RUN_TIME=${STRESS_RUN_TIME}
OMEGA_STRESS_ENABLE_WRITES=${OMEGA_STRESS_ENABLE_WRITES:-0}
OMEGA_STRESS_ENABLE_COPILOT_WRITES=${OMEGA_STRESS_ENABLE_COPILOT_WRITES:-0}
OMEGA_STRESS_ENABLE_GOLD_REFRESH=${OMEGA_STRESS_ENABLE_GOLD_REFRESH:-0}
OMEGA_STRESS_ENABLE_INTERNAL_PROBES=${OMEGA_STRESS_ENABLE_INTERNAL_PROBES:-0}
OMEGA_STRESS_CONCURRENT_WRITES=${OMEGA_STRESS_CONCURRENT_WRITES:-0}
OMEGA_STRESS_REQUIRE_LIVE_LLM=${OMEGA_STRESS_REQUIRE_LIVE_LLM:-0}
OMEGA_STRESS_FAKE_HUBSPOT=${OMEGA_STRESS_FAKE_HUBSPOT:-1}
OMEGA_STRESS_LIVE=${OMEGA_STRESS_LIVE:-0}
EOF

echo "[stress] running Locust headless users=${STRESS_USERS} spawn=${STRESS_SPAWN_RATE} runtime=${STRESS_RUN_TIME}"
E2E_ADMIN_EMAIL="${E2E_ADMIN_EMAIL:-admin@example.com}" \
E2E_ADMIN_PASSWORD="$E2E_ADMIN_PASSWORD" \
OMEGA_STRESS_ENABLE_WRITES="${OMEGA_STRESS_ENABLE_WRITES:-0}" \
OMEGA_STRESS_ENABLE_COPILOT_WRITES="${OMEGA_STRESS_ENABLE_COPILOT_WRITES:-0}" \
OMEGA_STRESS_ENABLE_GOLD_REFRESH="${OMEGA_STRESS_ENABLE_GOLD_REFRESH:-0}" \
OMEGA_STRESS_ENABLE_INTERNAL_PROBES="${OMEGA_STRESS_ENABLE_INTERNAL_PROBES:-0}" \
OMEGA_STRESS_CONCURRENT_WRITES="${OMEGA_STRESS_CONCURRENT_WRITES:-0}" \
OMEGA_STRESS_REQUIRE_LIVE_LLM="${OMEGA_STRESS_REQUIRE_LIVE_LLM:-0}" \
OMEGA_STRESS_REQUIRE_HUBSPOT_OK="${OMEGA_STRESS_REQUIRE_HUBSPOT_OK:-1}" \
  "$PYTHON_BIN" -m locust \
    -f tests/stress/locustfile.py \
    --headless \
    --host "$STRESS_HOST" \
    --users "$STRESS_USERS" \
    --spawn-rate "$STRESS_SPAWN_RATE" \
    --run-time "$STRESS_RUN_TIME" \
    --csv "${STRESS_ARTIFACT_DIR}/locust" \
    --html "${STRESS_ARTIFACT_DIR}/report.html" \
    --exit-code-on-error 1 \
    "$@" 2>&1 | tee "${STRESS_ARTIFACT_DIR}/locust.log"

"${COMPOSE[@]}" ps >"${STRESS_ARTIFACT_DIR}/compose_ps.txt" || true
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}' \
  | grep -E '(^NAME|mode_)' >"${STRESS_ARTIFACT_DIR}/docker_stats_final.txt" || true

echo "[stress] completed"
