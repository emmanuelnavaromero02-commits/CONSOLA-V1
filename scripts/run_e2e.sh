#!/usr/bin/env bash
# v1.43.1 — opt-in E2E runner with strict mode for CI.
#
# Two run modes:
#   * Local dev (default)  → skips silently if the stack isn't up.
#       E2E_ADMIN_PASSWORD=admin ./scripts/run_e2e.sh
#   * CI / pre-merge       → fail fast on any missing piece.
#       E2E_REQUIRE_STACK=1 E2E_ADMIN_PASSWORD=admin ./scripts/run_e2e.sh
#
# The admin password is read from the environment, never as a CLI
# argument, so it doesn't leak into shell history.
set -euo pipefail

cd "$(dirname "$0")/.."

PYBIN="${PYBIN:-.venv/bin/pytest}"
if [ ! -x "$PYBIN" ]; then
  PYBIN="pytest"
fi

# Run the original 8-test surface plus the new deep flow.
exec "$PYBIN" tests/test_e2e_full_flow.py tests/e2e -v "$@"
