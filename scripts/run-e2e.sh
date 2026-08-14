#!/usr/bin/env bash
# v1.44.3.2 — OMEGA E2E test runner.
#
# Validates preconditions (FastAPI-served console reachable,
# tests-e2e/.env present, Chromium installed), then runs the
# Playwright suite and surfaces the HTML report.
#
# Pre-existing v1.43.x convention: scripts/* are bash, set strict
# mode, idempotent, no surprises.
set -euo pipefail

echo "🧪 OMEGA E2E Test Suite"
echo "════════════════════════"

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$(pwd)"
E2E_DIR="${ROOT}/tests-e2e"

if [ ! -d "${E2E_DIR}" ]; then
    echo "❌ tests-e2e/ not found at ${E2E_DIR}"
    exit 1
fi

cd "${E2E_DIR}"
RELEASE_MODE=0
if [ "${OMEGA_RELEASE_DIGEST_STACK:-0}" = "1" ]; then
    RELEASE_MODE=1
    if [ "${OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT:-}" != "release" ] ||
       [ "${OMEGA_RELEASE_TEST_SKIP_POLICY:-}" != "${ROOT}/.github/release-test-skip-policy.json" ]; then
        echo "❌ Release digest stack requires the exact release skip policy"
        exit 2
    fi
    observed_e2e_sha256="$(sha256sum .env | awk '{print $1}')"
    if [[ ! "${OMEGA_RELEASE_E2E_ENV_SHA256:-}" =~ ^[0-9a-f]{64}$ ]] ||
       [ "${observed_e2e_sha256}" != "${OMEGA_RELEASE_E2E_ENV_SHA256}" ]; then
        echo "❌ tests-e2e/.env differs from the server-owned release bytes"
        exit 2
    fi
fi

# Credentials gate: refuse to run without explicit configuration so
# tests can't silently skip every protected check.
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "⚠️  Created tests-e2e/.env from .env.example"
        echo "    Edit it with your real local-dev credentials"
        echo "    (TEST_EMAIL + TEST_PASSWORD), then re-run."
        exit 1
    else
        echo "❌ tests-e2e/.env.example missing — repo state broken"
        exit 1
    fi
fi

# Source the .env so the shell-level reachability checks below see
# the configured BASE_URL / LEGACY_URL overrides.
if [ "${RELEASE_MODE}" = "1" ]; then
    while IFS= read -r dotenv_line || [ -n "${dotenv_line}" ]; do
        [[ "${dotenv_line}" =~ ^[[:space:]]*($|#) ]] && continue
        if [[ ! "${dotenv_line}" =~ ^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*= ]] ||
           [[ "${dotenv_line}" == *'$('* || "${dotenv_line}" == *'`'* ||
              "${dotenv_line}" == *';'* || "${dotenv_line}" == *'&&'* ||
              "${dotenv_line}" == *'||'* || "${dotenv_line}" == *'<('* ||
              "${dotenv_line}" == *'>('* ]]; then
            echo "❌ tests-e2e/.env is not a passive dotenv assignment file"
            exit 2
        fi
    done < .env
    reserved_pattern='^[[:space:]]*(export[[:space:]]+)?(OMEGA_RELEASE_[A-Za-z0-9_]*|OMEGA_STRESS_[A-Za-z0-9_]*|PATH|PYTHONOPTIMIZE|PYTHONPATH|PYTHONHOME|PYTHONSTARTUP|PYTEST_[A-Za-z0-9_]*|NODE_OPTIONS|PLAYWRIGHT_[A-Za-z0-9_]*|DOCKER_[A-Za-z0-9_]*|COMPOSE_[A-Za-z0-9_]*|GITHUB_[A-Za-z0-9_]*|RUNNER_[A-Za-z0-9_]*|NPM_CONFIG_[A-Za-z0-9_]*|npm_config_[A-Za-z0-9_]*|CI|MAKEFLAGS|GNUMAKEFLAGS|MAKEOVERRIDES|MFLAGS|MAKELEVEL|BASH_ENV|BASHOPTS|SHELLOPTS|ENV|SHELL|LD_PRELOAD|LD_LIBRARY_PATH|CDPATH|GLOBIGNORE|IFS)='
    if grep -Eq "${reserved_pattern}" .env; then
        echo "❌ tests-e2e/.env attempts to override a release-gate control"
        exit 2
    fi
    release_skip_environment="${OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT}"
    release_skip_policy="${OMEGA_RELEASE_TEST_SKIP_POLICY}"
    release_path="${PATH}"
    release_pythonpath="${PYTHONPATH-__UNSET__}"
    release_pythonhome="${PYTHONHOME-__UNSET__}"
    release_node_options="${NODE_OPTIONS-__UNSET__}"
    readonly RELEASE_MODE reserved_pattern release_skip_environment \
        release_skip_policy release_path release_pythonpath release_pythonhome \
        release_node_options
fi
release_env_file="$(mktemp)"
python3 "${ROOT}/scripts/load_release_dotenv.py" \
    --input .env --output "${release_env_file}"
while IFS= read -r -d '' release_key && IFS= read -r -d '' release_value; do
    export "${release_key}=${release_value}"
done < "${release_env_file}"
rm -f "${release_env_file}"
if [ "${RELEASE_MODE}" = "1" ] && {
   [ "${OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT-__UNSET__}" != "${release_skip_environment}" ] ||
   [ "${OMEGA_RELEASE_TEST_SKIP_POLICY-__UNSET__}" != "${release_skip_policy}" ] ||
   [ "${PATH-__UNSET__}" != "${release_path}" ] ||
   [ "${PYTHONPATH-__UNSET__}" != "${release_pythonpath}" ] ||
   [ "${PYTHONHOME-__UNSET__}" != "${release_pythonhome}" ] ||
   [ "${NODE_OPTIONS-__UNSET__}" != "${release_node_options}" ];
}; then
    echo "❌ tests-e2e/.env changed a release-gate control"
    exit 2
fi

# Some deep cartridge probes identify as X-Internal-Service: console,
# so they need the console→cartridge pair key. tests-e2e/.env may carry
# an operator-facing legacy INTERNAL_API_KEY, so always hydrate the
# pair key explicitly from infra/.env when the caller did not provide it.
read_infra_env() {
    local key="$1"
    awk -F= -v key="${key}" '$1 == key { print substr($0, index($0, "=") + 1); exit }' \
        "${ROOT}/infra/.env"
}

if [ -f "${ROOT}/infra/.env" ]; then
    if [ -z "${INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE:-}" ]; then
        INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE="$(read_infra_env INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE)"
        export INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE
    fi
    if [ -z "${INTERNAL_API_KEY:-}" ]; then
        INTERNAL_API_KEY="${INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE:-$(read_infra_env INTERNAL_API_KEY)}"
        export INTERNAL_API_KEY
    fi
fi

# Static export architecture: FastAPI on :8000 serves the console, APIs,
# legacy pages, and Control Room from the same origin.
BASE_URL="${BASE_URL:-http://localhost:8000}"
LEGACY_URL="${LEGACY_URL:-http://localhost:8000}"
CONTROL_ROOM_URL="${CONTROL_ROOM_URL:-${LEGACY_URL}/control-room}"

echo ""
echo "Preconditions:"

# FastAPI-served static console healthcheck.
if curl -sS --max-time 5 -o /dev/null "${BASE_URL}/healthz" 2>/dev/null; then
    echo "  ✅ FastAPI-served console reachable at ${BASE_URL}"
else
    echo "  ❌ FastAPI-served console NOT reachable at ${BASE_URL}/healthz"
    echo "     Bring up the stack with: make up"
    exit 1
fi

# FastAPI backend healthcheck (APIs + Control Room, :8000)
if curl -sS --max-time 5 -o /dev/null "${LEGACY_URL}/healthz" 2>/dev/null; then
    echo "  ✅ FastAPI backend reachable at ${LEGACY_URL}"
    echo "     ↳ Control Room expected at ${CONTROL_ROOM_URL}"
else
    echo "  ❌ FastAPI backend NOT reachable at ${LEGACY_URL}/healthz"
    exit 1
fi

PLAYWRIGHT_BIN="${E2E_DIR}/node_modules/.bin/playwright"
if [ "${RELEASE_MODE}" = "1" ]; then
    if [ ! -x "${PLAYWRIGHT_BIN}" ] || ! "${PLAYWRIGHT_BIN}" --version >/dev/null; then
        echo "  ❌ Locked local Playwright is unavailable"
        exit 1
    fi
    "${PLAYWRIGHT_BIN}" install --dry-run chromium >/dev/null
    CHROMIUM_BIN="$(node -e 'process.stdout.write(require("@playwright/test").chromium.executablePath())')"
    if [ ! -x "${CHROMIUM_BIN}" ]; then
        echo "  ❌ Locked Chromium is unavailable; release gates never auto-install"
        exit 1
    fi
elif ! npx --no-install playwright --version > /dev/null 2>&1; then
    echo "  ❌ Playwright not installed. Run: cd tests-e2e && npm install"
    exit 1
elif ! CHROMIUM_BIN="$(node -e 'process.stdout.write(require("@playwright/test").chromium.executablePath())')" || [ ! -x "${CHROMIUM_BIN}" ]; then
    echo "  ⚠️  Chromium browser missing — installing now (one-shot)…"
    npx --no-install playwright install chromium
fi
echo "  ✅ Playwright + Chromium installed"

echo ""
echo "Running suite…"
echo ""

EXIT=0
if [ "${RELEASE_MODE}" = "1" ]; then
    cd "${ROOT}"
    python3 scripts/run_release_playwright.py || EXIT=$?
    cd "${E2E_DIR}"
else
    # Interactive runs retain the HTML/list reporters from the checked-in config.
    npx --no-install playwright test || EXIT=$?
fi

if [ "${EXIT}" -ne 0 ]; then
    echo ""
    echo "E2E failure summary:"
    SUMMARY="$(bash "${ROOT}/scripts/e2e-report-summary.sh" 2>/dev/null || true)"
    if [ -n "${SUMMARY}" ]; then
        echo "${SUMMARY}"
        if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
            ANNOTATION="$(printf '%s\n' "${SUMMARY}" | awk 'NR <= 20 { printf "%s%s", sep, $0; sep=" " }')"
            ANNOTATION="${ANNOTATION//%/%25}"
            ANNOTATION="${ANNOTATION//$'\r'/%0D}"
            ANNOTATION="${ANNOTATION//$'\n'/%0A}"
            echo "::error title=Playwright E2E failed::${ANNOTATION}"
        fi
    else
        echo "  (No JSON summary available; inspect playwright-report artifact.)"
    fi
fi

echo ""
echo "📊 Report: ${E2E_DIR}/playwright-report/index.html"
echo ""

# Only auto-open the report in interactive shells; in CI / headless
# pipelines just print the path.
if [ -t 1 ] && [ "${OPEN_REPORT:-1}" = "1" ]; then
    npx playwright show-report --host localhost 2>/dev/null || true
fi

exit "${EXIT}"
