#!/usr/bin/env bash
set -euo pipefail

CONSOLE_URL="${CONSOLE_URL:-http://modecissions-public-255609366.us-east-1.elb.amazonaws.com}"
REQUIRE_DATA="${OMEGA_MONITOR_REQUIRE_DATA:-1}"
TIMEOUT_SECONDS="${OMEGA_MONITOR_TIMEOUT_SECONDS:-15}"

check_json_ok() {
  local name="$1"
  local url="$2"
  local body
  body="$(curl -fsS --max-time "${TIMEOUT_SECONDS}" "${url}")"
  python3 - "$name" "$body" <<'PY'
import json
import sys

name, raw = sys.argv[1], sys.argv[2]
payload = json.loads(raw)
if payload.get("ok") is not True:
    raise SystemExit(f"{name} did not report ok=true: {raw}")
print(f"{name}: ok")
PY
}

check_json_ok "healthz" "${CONSOLE_URL%/}/healthz"
check_json_ok "readyz" "${CONSOLE_URL%/}/readyz"

if [[ "${REQUIRE_DATA}" == "1" || "${REQUIRE_DATA}" == "true" ]]; then
  check_json_ok "readyz_data" "${CONSOLE_URL%/}/readyz?require_data=1"
fi
