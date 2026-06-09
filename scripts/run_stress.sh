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
  smoke)
    DEFAULT_USERS=25
    DEFAULT_SPAWN_RATE=5
    DEFAULT_RUN_TIME="5m"
    DEFAULT_INTERNAL_PROBES="${OMEGA_STRESS_ENABLE_INTERNAL_PROBES:-0}"
    DEFAULT_WRITES="${OMEGA_STRESS_ENABLE_WRITES:-0}"
    ;;
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
    DEFAULT_RUN_TIME="15m"
    DEFAULT_INTERNAL_PROBES="${OMEGA_STRESS_ENABLE_INTERNAL_PROBES:-1}"
    DEFAULT_WRITES="${OMEGA_STRESS_ENABLE_WRITES:-1}"
    ;;
  spike)
    DEFAULT_USERS=1000
    DEFAULT_SPAWN_RATE=1000
    DEFAULT_RUN_TIME="5m"
    DEFAULT_INTERNAL_PROBES="${OMEGA_STRESS_ENABLE_INTERNAL_PROBES:-1}"
    DEFAULT_WRITES="${OMEGA_STRESS_ENABLE_WRITES:-0}"
    ;;
  breakpoint)
    DEFAULT_USERS=2000
    DEFAULT_SPAWN_RATE=100
    DEFAULT_RUN_TIME="10m"
    DEFAULT_INTERNAL_PROBES="${OMEGA_STRESS_ENABLE_INTERNAL_PROBES:-1}"
    DEFAULT_WRITES="${OMEGA_STRESS_ENABLE_WRITES:-0}"
    ;;
  soak-24h)
    DEFAULT_USERS=250
    DEFAULT_SPAWN_RATE=10
    DEFAULT_RUN_TIME="24h"
    DEFAULT_INTERNAL_PROBES="${OMEGA_STRESS_ENABLE_INTERNAL_PROBES:-1}"
    DEFAULT_WRITES="${OMEGA_STRESS_ENABLE_WRITES:-0}"
    ;;
  write-heavy)
    DEFAULT_USERS=100
    DEFAULT_SPAWN_RATE=10
    DEFAULT_RUN_TIME="30m"
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
    echo "[stress] unsupported OMEGA_STRESS_PROFILE=${STRESS_PROFILE}; expected smoke|local|beta|spike|breakpoint|soak-24h|write-heavy|production"
    exit 2
    ;;
esac

STRESS_HOST="${OMEGA_STRESS_HOST:-http://127.0.0.1:8000}"
STRESS_WORKLOAD="${OMEGA_STRESS_WORKLOAD:-hubspot}"
STRESS_USERS="${OMEGA_STRESS_USERS:-$DEFAULT_USERS}"
STRESS_SPAWN_RATE="${OMEGA_STRESS_SPAWN_RATE:-$DEFAULT_SPAWN_RATE}"
STRESS_RUN_TIME="${OMEGA_STRESS_RUN_TIME:-$DEFAULT_RUN_TIME}"
STRESS_STATS_INTERVAL="${OMEGA_STRESS_STATS_INTERVAL:-5}"
STRESS_ARTIFACT_DIR="${OMEGA_STRESS_ARTIFACT_DIR:-artifacts/stress/$(date -u +%Y%m%dT%H%M%SZ)}"
FAKE_PORT="${FAKE_HUBSPOT_PORT:-18030}"
FAKE_TOKEN="${FAKE_HUBSPOT_TOKEN:-pat-na1-stress-token}"
FAKE_BASE_URL="http://host.docker.internal:${FAKE_PORT}"
ENABLE_WRITES="${OMEGA_STRESS_ENABLE_WRITES:-$DEFAULT_WRITES}"
REMOTE_STRESS_HOST=1
case "$STRESS_HOST" in
  http://127.0.0.1*|https://127.0.0.1*|http://localhost*|https://localhost*|http://0.0.0.0*|https://0.0.0.0*)
    REMOTE_STRESS_HOST=0
    ;;
esac
EXPLICIT_ADMIN_SECRET=0
if [[ -n "${E2E_ADMIN_PASSWORD:-}" || -n "${TEST_PASSWORD:-}" ]]; then
  EXPLICIT_ADMIN_SECRET=1
fi
HAS_STRESS_SESSION_AUTH=0
if [[ -n "${OMEGA_STRESS_SESSION_COOKIES:-}" || -n "${OMEGA_STRESS_SESSION_COOKIE_FILE:-}" || -n "${OMEGA_STRESS_BEARER_TOKEN:-}" ]]; then
  HAS_STRESS_SESSION_AUTH=1
  EXPLICIT_ADMIN_SECRET=1
fi
if [[ -z "${E2E_ADMIN_PASSWORD:-}" && -n "${TEST_PASSWORD:-}" ]]; then
  E2E_ADMIN_PASSWORD="$TEST_PASSWORD"
  export E2E_ADMIN_PASSWORD
fi
if [[ -z "${E2E_ADMIN_EMAIL:-}" && -n "${TEST_EMAIL:-}" ]]; then
  E2E_ADMIN_EMAIL="$TEST_EMAIL"
  export E2E_ADMIN_EMAIL
fi
export OMEGA_STRESS_ENABLE_WRITES="$ENABLE_WRITES"
export OMEGA_STRESS_ENABLE_INTERNAL_PROBES="$DEFAULT_INTERNAL_PROBES"
export OMEGA_STRESS_WORKLOAD="$STRESS_WORKLOAD"
export STRESS_HOST STRESS_ARTIFACT_DIR

mkdir -p "$STRESS_ARTIFACT_DIR"

if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import locust  # noqa: F401
PY
then
  echo "[stress] Locust is not installed for ${PYTHON_BIN}."
  echo "[stress] Install it with: ${PYTHON_BIN} -m pip install -r tests/stress/requirements.txt"
  exit 2
fi

if [[ "$REMOTE_STRESS_HOST" == "1" && "$EXPLICIT_ADMIN_SECRET" != "1" ]]; then
  echo "[stress] remote target ${STRESS_HOST} requires explicit E2E_ADMIN_PASSWORD or TEST_PASSWORD."
  echo "[stress] Local .env/.env.example fallbacks are disabled for remote targets to avoid account lockout."
  echo "[stress] Export E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD for this AWS stack, then rerun."
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

if [[ -z "${E2E_ADMIN_PASSWORD:-}" && "$HAS_STRESS_SESSION_AUTH" != "1" ]]; then
  echo "[stress] E2E_ADMIN_PASSWORD is required."
  echo "[stress] Export it with the bootstrap admin password used by this stack."
  echo "[stress] Or provide OMEGA_STRESS_SESSION_COOKIES, OMEGA_STRESS_SESSION_COOKIE_FILE, or OMEGA_STRESS_BEARER_TOKEN to reuse an authenticated session."
  exit 2
fi

if [[ "$HAS_STRESS_SESSION_AUTH" != "1" && "${OMEGA_STRESS_LOGIN_PREFLIGHT:-$REMOTE_STRESS_HOST}" =~ ^(1|true|yes)$ ]]; then
  "$PYTHON_BIN" - <<'PY'
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

base = os.environ["STRESS_HOST"].rstrip("/")
email = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
password = os.environ.get("E2E_ADMIN_PASSWORD", "")
artifact_dir = Path(os.environ["STRESS_ARTIFACT_DIR"])
artifact_dir.mkdir(parents=True, exist_ok=True)
summary_path = artifact_dir / "login_preflight.json"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def request(req: urllib.request.Request):
    try:
        with opener.open(req, timeout=20) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"

status, body = request(urllib.request.Request(f"{base}/login"))
if status != 200:
    summary_path.write_text(json.dumps({"status": "BLOCKED", "step": "GET /login", "http_status": status, "body": body[:200]}, indent=2) + "\n")
    print(f"[stress] login preflight failed GET /login HTTP {status}: {body[:200]}")
    sys.exit(2)

csrf = ""
for cookie in jar:
    if cookie.name in {"csrf_token", "csrftoken"}:
        csrf = cookie.value
headers = {"Content-Type": "application/json"}
if csrf:
    headers["X-CSRF-Token"] = csrf
payload = json.dumps({"email": email, "password": password}).encode("utf-8")
login_path = os.environ.get("OMEGA_STRESS_LOGIN_PATH", "/auth/login")
status, body = request(urllib.request.Request(f"{base}{login_path}", data=payload, headers=headers, method="POST"))
summary_path.write_text(json.dumps({"status": "PASS" if status == 200 else "BLOCKED", "step": f"POST {login_path}", "http_status": status, "body": body[:200]}, indent=2) + "\n")
if status != 200:
    print(f"[stress] login preflight failed POST {login_path} HTTP {status}: {body[:200]}")
    sys.exit(2)
print(f"[stress] login preflight OK for {email}")
PY
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

if [[ "$STRESS_WORKLOAD" == "hubspot" && "$ENABLE_WRITES" =~ ^(1|true|yes)$ && "${OMEGA_STRESS_RESET_HUBSPOT_DERIVED:-1}" =~ ^(1|true|yes)$ ]]; then
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

if [[ "$STRESS_WORKLOAD" == "hubspot" && "${OMEGA_STRESS_FAKE_HUBSPOT:-1}" == "1" && "${OMEGA_STRESS_LIVE:-0}" != "1" ]]; then
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
OMEGA_STRESS_WORKLOAD=${STRESS_WORKLOAD}
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
OMEGA_STRESS_ENABLE_SF_REFRESH=${OMEGA_STRESS_ENABLE_SF_REFRESH:-0}
EOF

echo "[stress] running Locust headless users=${STRESS_USERS} spawn=${STRESS_SPAWN_RATE} runtime=${STRESS_RUN_TIME}"
set +e
E2E_ADMIN_EMAIL="${E2E_ADMIN_EMAIL:-admin@example.com}" \
E2E_ADMIN_PASSWORD="${E2E_ADMIN_PASSWORD:-}" \
OMEGA_STRESS_ENABLE_WRITES="${OMEGA_STRESS_ENABLE_WRITES:-0}" \
OMEGA_STRESS_ENABLE_COPILOT_WRITES="${OMEGA_STRESS_ENABLE_COPILOT_WRITES:-0}" \
OMEGA_STRESS_ENABLE_GOLD_REFRESH="${OMEGA_STRESS_ENABLE_GOLD_REFRESH:-0}" \
OMEGA_STRESS_ENABLE_INTERNAL_PROBES="${OMEGA_STRESS_ENABLE_INTERNAL_PROBES:-0}" \
OMEGA_STRESS_CONCURRENT_WRITES="${OMEGA_STRESS_CONCURRENT_WRITES:-0}" \
OMEGA_STRESS_REQUIRE_LIVE_LLM="${OMEGA_STRESS_REQUIRE_LIVE_LLM:-0}" \
OMEGA_STRESS_REQUIRE_HUBSPOT_OK="${OMEGA_STRESS_REQUIRE_HUBSPOT_OK:-1}" \
OMEGA_STRESS_WORKLOAD="$STRESS_WORKLOAD" \
OMEGA_STRESS_ENABLE_SF_REFRESH="${OMEGA_STRESS_ENABLE_SF_REFRESH:-0}" \
OMEGA_STRESS_SESSION_COOKIES="${OMEGA_STRESS_SESSION_COOKIES:-}" \
OMEGA_STRESS_SESSION_COOKIE_FILE="${OMEGA_STRESS_SESSION_COOKIE_FILE:-}" \
OMEGA_STRESS_BEARER_TOKEN="${OMEGA_STRESS_BEARER_TOKEN:-}" \
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
LOCUST_CODE=$?
set -e

"${COMPOSE[@]}" ps >"${STRESS_ARTIFACT_DIR}/compose_ps.txt" || true
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}' \
  | grep -E '(^NAME|mode_)' >"${STRESS_ARTIFACT_DIR}/docker_stats_final.txt" || true

set +e
"$PYTHON_BIN" scripts/stress_summary.py "$STRESS_ARTIFACT_DIR" --profile "$STRESS_PROFILE" --workload "$STRESS_WORKLOAD"
SUMMARY_CODE=$?
set -e

echo "[stress] completed"
if [[ "$SUMMARY_CODE" -eq 2 ]]; then
  exit 2
fi
if [[ "$LOCUST_CODE" -ne 0 ]]; then
  exit "$LOCUST_CODE"
fi
exit "$SUMMARY_CODE"
