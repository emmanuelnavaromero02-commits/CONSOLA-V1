#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

CONSOLE_URL="${CONSOLE_URL:-http://127.0.0.1:8000}"
TEST_EMAIL="${TEST_EMAIL:-${E2E_ADMIN_EMAIL:-emmanuel@local.ai}}"
TEST_PASSWORD="${TEST_PASSWORD:-${E2E_ADMIN_PASSWORD:-}}"
LIVE_CARTRIDGES="${OMEGA_LIVE_CARTRIDGES:-hubspot salesforce replicon sap_hcm sap_s4hana sap_successfactors}"
RUN_EXTRACTION="${OMEGA_LIVE_CARTRIDGE_RUN_EXTRACTION:-1}"
COOKIE_JAR="$(mktemp)"
LOGIN_HEADERS="$(mktemp)"
LOGIN_BODY="$(mktemp)"
trap 'rm -f "${COOKIE_JAR}" "${LOGIN_HEADERS}" "${LOGIN_BODY}"' EXIT

log() {
  printf '[live-cartridges] %s\n' "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

blocked() {
  log "BLOCKED: $*"
  exit 2
}

minimal_entity_for() {
  case "$1" in
    hubspot) printf '%s' "${OMEGA_LIVE_ENTITY_HUBSPOT:-deals}" ;;
    salesforce) printf '%s' "${OMEGA_LIVE_ENTITY_SALESFORCE:-Account}" ;;
    replicon) printf '%s' "${OMEGA_LIVE_ENTITY_REPLICON:-User}" ;;
    sap_hcm) printf '%s' "${OMEGA_LIVE_ENTITY_SAP_HCM:-EmployeeMaster}" ;;
    sap_s4hana) printf '%s' "${OMEGA_LIVE_ENTITY_SAP_S4HANA:-BusinessPartner}" ;;
    sap_successfactors) printf '%s' "${OMEGA_LIVE_ENTITY_SAP_SUCCESSFACTORS:-User}" ;;
    *) return 1 ;;
  esac
}

if [[ "${OMEGA_ENABLE_LIVE_CARTRIDGE_TESTS:-0}" != "1" ]]; then
  blocked "OMEGA_ENABLE_LIVE_CARTRIDGE_TESTS=1 is required"
fi

if [[ "${OMEGA_LIVE_CARTRIDGE_CREDS_CONFIRMED:-0}" != "1" ]]; then
  blocked "OMEGA_LIVE_CARTRIDGE_CREDS_CONFIRMED=1 is required after loading real sandbox credentials"
fi

if [[ -z "${TEST_PASSWORD}" ]]; then
  blocked "TEST_PASSWORD or E2E_ADMIN_PASSWORD is required"
fi

cookie_value() {
  local name="$1"
  awk -v name="${name}" '$6 == name {print $7}' "${COOKIE_JAR}" | tail -1
}

login() {
  curl -fsS --max-time 10 -c "${COOKIE_JAR}" -o /dev/null "${CONSOLE_URL}/login" \
    || fail "GET ${CONSOLE_URL}/login failed"
  local csrf_token
  csrf_token="$(cookie_value csrf_token)"
  [[ -n "${csrf_token}" ]] || fail "csrf_token cookie missing on GET /login"

  TEST_EMAIL="${TEST_EMAIL}" TEST_PASSWORD="${TEST_PASSWORD}" python3 - <<'PY' > "${LOGIN_BODY}"
import json
import os

print(json.dumps({
    "email": os.environ["TEST_EMAIL"],
    "password": os.environ["TEST_PASSWORD"],
}))
PY

  local login_status
  login_status="$(curl -sS --max-time 20 \
    -b "${COOKIE_JAR}" -c "${COOKIE_JAR}" -D "${LOGIN_HEADERS}" -o /dev/null \
    -w '%{http_code}' \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: ${csrf_token}" \
    --data-binary "@${LOGIN_BODY}" \
    "${CONSOLE_URL}/auth/login" || true)"
  [[ "${login_status}" == "200" ]] || fail "POST ${CONSOLE_URL}/auth/login returned HTTP ${login_status}"
}

csrf_token() {
  local token
  token="$(cookie_value csrf_token)"
  [[ -n "${token}" ]] || fail "csrf_token cookie missing after login"
  printf '%s' "${token}"
}

assert_connection_ok() {
  local cartridge="$1"
  local csrf="$2"
  local body
  body="$(curl -fsS --max-time 60 \
    -b "${COOKIE_JAR}" \
    -H "X-CSRF-Token: ${csrf}" \
    -X POST \
    "${CONSOLE_URL}/api/cartridges/${cartridge}/test_connection")" \
    || fail "${cartridge} test_connection request failed"
  BODY="${body}" CARTRIDGE="${cartridge}" python3 - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["BODY"])
result = payload.get("result", payload)
if result.get("ok") is True or result.get("status") == "ok":
    print(f"[live-cartridges] OK {os.environ['CARTRIDGE']} test_connection")
    raise SystemExit(0)
status = result.get("status") or payload.get("status") or "unknown"
print(f"[live-cartridges] ERROR: {os.environ['CARTRIDGE']} test_connection status={status}")
raise SystemExit(1)
PY
}

assert_minimal_extract_triggers() {
  local cartridge="$1"
  local entity="$2"
  local csrf="$3"
  local body
  body="$(curl -fsS --max-time 90 \
    -b "${COOKIE_JAR}" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: ${csrf}" \
    -X POST \
    --data "{\"mode\":\"full\",\"idempotency_key\":\"live-${cartridge}-${entity}-$(date -u +%Y%m%dT%H%M%SZ)\"}" \
    "${CONSOLE_URL}/api/pipeline/${cartridge}/${entity}/extract")" \
    || fail "${cartridge}/${entity} extraction request failed"
  BODY="${body}" CARTRIDGE="${cartridge}" ENTITY="${entity}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["BODY"])
if payload.get("triggered") or payload.get("job_id") or payload.get("dag_run_id") or payload.get("run_id"):
    print(f"[live-cartridges] OK {os.environ['CARTRIDGE']}/{os.environ['ENTITY']} extraction triggered")
    raise SystemExit(0)
print(f"[live-cartridges] ERROR: {os.environ['CARTRIDGE']}/{os.environ['ENTITY']} extraction did not trigger")
raise SystemExit(1)
PY
}

log "logging into ${CONSOLE_URL} as ${TEST_EMAIL}"
login
csrf="$(csrf_token)"

for cartridge in ${LIVE_CARTRIDGES}; do
  entity="$(minimal_entity_for "${cartridge}" || true)"
  if [[ -z "${entity}" ]]; then
    fail "no minimal entity configured for cartridge ${cartridge}"
  fi
  assert_connection_ok "${cartridge}" "${csrf}"
  if [[ "${RUN_EXTRACTION}" == "1" ]]; then
    assert_minimal_extract_triggers "${cartridge}" "${entity}" "${csrf}"
  else
    log "SKIP ${cartridge} extraction because OMEGA_LIVE_CARTRIDGE_RUN_EXTRACTION=0"
  fi
done

log "PASS"
