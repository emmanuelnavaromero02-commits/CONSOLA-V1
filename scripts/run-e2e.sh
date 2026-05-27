#!/usr/bin/env bash
# v1.44.3.2 — OMEGA E2E test runner.
#
# Validates preconditions (Next.js + legacy console reachable,
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
set -a
# shellcheck disable=SC1091
source .env
set +a

# Some deep cartridge probes need the same internal API key the
# running compose stack uses. Keep tests-e2e/.env operator-facing
# and pull the secret from infra/.env when it was not explicitly
# exported by the caller.
if [ -z "${INTERNAL_API_KEY:-}" ] && [ -f "${ROOT}/infra/.env" ]; then
    INTERNAL_API_KEY="$(
        awk -F= '/^INTERNAL_API_KEY=/ { print substr($0, index($0, "=") + 1); exit }' \
            "${ROOT}/infra/.env"
    )"
    export INTERNAL_API_KEY
fi

# Split architecture (beta): Next.js frontend on :3000 is the official
# temporary frontend; FastAPI backend on :8000 hosts the APIs and the
# Control Room. This is intentional — the suite is NOT forced 8000-only.
BASE_URL="${BASE_URL:-http://localhost:3000}"
LEGACY_URL="${LEGACY_URL:-http://localhost:8000}"
CONTROL_ROOM_URL="${CONTROL_ROOM_URL:-${LEGACY_URL}/control-room}"

echo ""
echo "Preconditions:"

# Next.js frontend healthcheck (official temporary frontend, :3000)
if curl -sS --max-time 5 -o /dev/null "${BASE_URL}/api/health" 2>/dev/null; then
    echo "  ✅ Next.js frontend reachable at ${BASE_URL}"
else
    echo "  ❌ Next.js frontend NOT reachable at ${BASE_URL}/api/health"
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

# Chromium presence — Playwright auto-downloads on first install but
# the binary may have been pruned. A loud check is friendlier than a
# 30s-into-the-run failure.
if ! npx playwright --version > /dev/null 2>&1; then
    echo "  ❌ Playwright not installed. Run: cd tests-e2e && npm install"
    exit 1
fi

if ! npx playwright install --dry-run chromium 2>&1 | grep -q "is already installed"; then
    echo "  ⚠️  Chromium browser missing — installing now (one-shot)…"
    npx playwright install chromium
fi
echo "  ✅ Playwright + Chromium installed"

echo ""
echo "Running suite…"
echo ""

# Run the suite. We intentionally do NOT pass --reporter here —
# playwright.config.ts already declares html + list reporters.
# Exit code is preserved so CI / make e2e fails on red tests.
EXIT=0
npx playwright test || EXIT=$?

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
