#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYBIN="${PYBIN:-.venv/bin/pytest}"
if [ ! -x "$PYBIN" ]; then
  PYBIN="pytest"
fi

exec "$PYBIN" tests/test_e2e_full_flow.py tests/e2e -v "$@"
