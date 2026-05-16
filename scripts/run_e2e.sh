#!/usr/bin/env bash
# Sprint v1.41.1 — opt-in E2E runner.
#
# Hits a live OMEGA stack. Skips cleanly if the console isn't up. Wire
# the admin password through the environment, never as a CLI arg, so
# it doesn't leak into shell history.
#
# Usage:
#   E2E_ADMIN_PASSWORD=xxx ./scripts/run_e2e.sh
#   E2E_CONSOLE_URL=http://localhost:8000 E2E_ADMIN_EMAIL=admin@example.com \
#     E2E_ADMIN_PASSWORD=xxx ./scripts/run_e2e.sh -vv
set -euo pipefail

cd "$(dirname "$0")/.."

PYBIN="${PYBIN:-.venv/bin/pytest}"
if [ ! -x "$PYBIN" ]; then
  # Fall back to the system pytest when a venv isn't present (CI).
  PYBIN="pytest"
fi

exec "$PYBIN" tests/test_e2e_full_flow.py -v "$@"
