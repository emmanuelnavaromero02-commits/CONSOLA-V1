#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONSOLE_URL="${PUBLIC_CONSOLE_URL:-${CONSOLE_URL:-}}"
WORKSPACE_URL="${PUBLIC_WORKSPACE_URL:-${WORKSPACE_PUBLIC_URL:-}}"
REQUIRE_LIVE="${V1_REQUIRE_LIVE:-1}"
TEST_EMAIL="${TEST_EMAIL:-emmanuel@local.ai}"
TEST_PASSWORD="${TEST_PASSWORD:-${E2E_ADMIN_PASSWORD:-}}"
COOKIE_JAR="$(mktemp)"
LOGIN_HEADERS="$(mktemp)"
LOGIN_BODY="$(mktemp)"
trap 'rm -f "${COOKIE_JAR}" "${LOGIN_HEADERS}" "${LOGIN_BODY}"' EXIT

fail() {
  echo "[verify-v1-public] ERROR: $*" >&2
  exit 1
}

require_https_url() {
  local name="$1"
  local value="$2"
  if [[ -z "${value}" ]]; then
    fail "${name} is required"
  fi
  if [[ ! "${value}" =~ ^https:// ]]; then
    fail "${name} must be an https:// URL, got: ${value}"
  fi
}

curl_ok() {
  local url="$1"
  curl -fsS --max-time 10 -o /dev/null "${url}" || fail "GET ${url} failed"
  echo "[verify-v1-public] OK ${url}"
}

check_http_redirect() {
  local host="$1"
  local path="$2"
  local label="$3"
  local status
  status="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code} %{redirect_url}' "http://${host}${path}" || true)"
  case "${status}" in
    "301 https://"*|"302 https://"*|"308 https://"*) echo "[verify-v1-public] OK http->https redirect for ${label}" ;;
    *) fail "http://${host}${path} did not redirect to https (got: ${status})" ;;
  esac
}

cookie_value() {
  local name="$1"
  awk -v name="${name}" '$6 == name {print $7}' "${COOKIE_JAR}" | tail -1
}

assert_cookie_attr() {
  local cookie_name="$1"
  local attr="$2"
  if ! grep -i "^set-cookie: ${cookie_name}=" "${LOGIN_HEADERS}" | grep -iq "${attr}"; then
    fail "login cookie ${cookie_name} is missing ${attr}"
  fi
}

login_and_verify_cookies() {
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

  for cookie_name in mod_session refresh_token; do
    assert_cookie_attr "${cookie_name}" HttpOnly
    assert_cookie_attr "${cookie_name}" SameSite
    assert_cookie_attr "${cookie_name}" Secure
  done
  echo "[verify-v1-public] OK login cookies are HttpOnly, Secure and SameSite"
}

verify_live_cartridges() {
  local csrf_token
  csrf_token="$(cookie_value csrf_token)"
  [[ -n "${csrf_token}" ]] || fail "csrf_token cookie missing after login"

  for cartridge in hubspot salesforce replicon sap_hcm sap_s4hana sap_successfactors; do
    local body status
    body="$(curl -fsS --max-time 45 \
      -b "${COOKIE_JAR}" \
      -H "X-CSRF-Token: ${csrf_token}" \
      -X POST \
      "${CONSOLE_URL}/api/cartridges/${cartridge}/test_connection")" \
      || fail "${cartridge} test_connection request failed"
    status="$(BODY="${body}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["BODY"])
result = payload.get("result", payload)
if result.get("ok") is True:
    print("ok")
else:
    print(result.get("status", ""))
PY
)"
    [[ "${status}" == "ok" ]] || fail "${cartridge} is not live verified; status=${status}; response=${body}"
    echo "[verify-v1-public] OK ${cartridge} live test_connection"
  done
}

require_https_url PUBLIC_CONSOLE_URL "${CONSOLE_URL}"
require_https_url PUBLIC_WORKSPACE_URL "${WORKSPACE_URL}"

CONSOLE_HOST="$(CONSOLE_URL="${CONSOLE_URL}" python3 -c 'from urllib.parse import urlparse; import os; print(urlparse(os.environ["CONSOLE_URL"]).hostname or "")')"
WORKSPACE_HOST="$(WORKSPACE_URL="${WORKSPACE_URL}" python3 -c 'from urllib.parse import urlparse; import os; print(urlparse(os.environ["WORKSPACE_URL"]).hostname or "")')"

curl_ok "${CONSOLE_URL}/healthz"
curl_ok "${CONSOLE_URL}/readyz"
curl_ok "${WORKSPACE_URL}/healthz"

check_http_redirect "${CONSOLE_HOST}" "/healthz" "${CONSOLE_HOST}"
check_http_redirect "${WORKSPACE_HOST}" "/healthz" "${WORKSPACE_HOST}"

for host in "${CONSOLE_HOST}" "${WORKSPACE_HOST}"; do
  for port in 8000 8001 8082 8088 9000 15432 8201 8202 8203 8204; do
    if curl -sS --max-time 3 -o /dev/null "http://${host}:${port}/" 2>/dev/null; then
      fail "internal port ${port} is reachable on ${host}"
    fi
  done
done
echo "[verify-v1-public] OK internal ports are not directly reachable on public hosts"

if [[ "${REQUIRE_LIVE}" == "1" ]]; then
  [[ "${E2E_LIVE_LLM:-}" == "1" ]] || fail "E2E_LIVE_LLM=1 is required for v1 public verification"
  [[ -n "${ANTHROPIC_API_KEY:-}" ]] || fail "ANTHROPIC_API_KEY is required for live LLM probes"
  [[ -n "${TEST_PASSWORD}" ]] || fail "TEST_PASSWORD or E2E_ADMIN_PASSWORD is required for public login verification"
fi

if [[ -n "${TEST_PASSWORD}" ]]; then
  login_and_verify_cookies
  if [[ "${REQUIRE_LIVE}" == "1" ]]; then
    verify_live_cartridges
  fi
else
  echo "[verify-v1-public] SKIP login/cookie checks because TEST_PASSWORD is empty"
fi

cd "${ROOT}/tests-e2e"
if [[ ! -d node_modules ]]; then
  npm ci
fi

BASE_URL="${CONSOLE_URL}" \
BACKEND_URL="${CONSOLE_URL}" \
LEGACY_URL="${CONSOLE_URL}" \
CONTROL_ROOM_URL="${CONSOLE_URL}/control-room" \
E2E_LIVE_LLM="${E2E_LIVE_LLM:-0}" \
npx playwright test

echo "[verify-v1-public] Public v1 verification completed"
