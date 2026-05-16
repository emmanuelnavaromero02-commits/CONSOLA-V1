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

BASE_URL="${BASE_URL:-http://localhost:3000}"
LEGACY_URL="${LEGACY_URL:-http://localhost:8000}"

echo ""
echo "Preconditions:"

# Next.js healthcheck
if curl -sS --max-time 5 -o /dev/null "${BASE_URL}/api/health" 2>/dev/null; then
    echo "  ✅ Next.js console reachable at ${BASE_URL}"
else
    echo "  ❌ Next.js console NOT reachable at ${BASE_URL}/api/health"
    echo "     Bring up the stack with: make up"
    exit 1
fi

# FastAPI healthcheck
if curl -sS --max-time 5 -o /dev/null "${LEGACY_URL}/healthz" 2>/dev/null; then
    echo "  ✅ Legacy console reachable at ${LEGACY_URL}"
else
    echo "  ❌ Legacy console NOT reachable at ${LEGACY_URL}/healthz"
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

echo ""
echo "📊 Report: ${E2E_DIR}/playwright-report/index.html"
echo ""

# Only auto-open the report in interactive shells; in CI / headless
# pipelines just print the path.
if [ -t 1 ] && [ "${OPEN_REPORT:-1}" = "1" ]; then
    npx playwright show-report --host localhost 2>/dev/null || true
fi

exit "${EXIT}"
