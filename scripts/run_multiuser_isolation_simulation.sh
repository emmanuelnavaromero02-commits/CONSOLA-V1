#!/usr/bin/env bash
# Prod-like multi-user isolation simulation.
#
# Seeds one tenant with many workspace admins and employees, exercises API
# visibility with real JWTs, then verifies direct Postgres RLS through service
# roles. It is intentionally explicit/guarded because it writes sandbox rows.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

if [[ -f infra/.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source infra/.env
  set +a
fi

if [[ -n "${OMEGA_MULTIUSER_SIM_DB_ADMIN_URL:-}" ]]; then
  export DATABASE_URL="${OMEGA_MULTIUSER_SIM_DB_ADMIN_URL}"
elif command -v docker >/dev/null 2>&1 && [[ -f infra/docker-compose.yml && -f infra/.env ]]; then
  PG_PORT="$(docker compose --env-file infra/.env -f infra/docker-compose.yml --profile sap port postgres 5432 2>/dev/null | awk -F: 'END {print $NF}')"
  if [[ -n "${PG_PORT}" && -n "${POSTGRES_PASSWORD:-}" ]]; then
    export DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@127.0.0.1:${PG_PORT}/modecissions"
  elif [[ -z "${DATABASE_URL:-}" && -n "${PG_PORT}" && -n "${OMEGA_CONSOLE_PASSWORD:-}" ]]; then
    export DATABASE_URL="postgresql://omega_console:${OMEGA_CONSOLE_PASSWORD}@127.0.0.1:${PG_PORT}/modecissions"
  fi
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[multiuser-sim] DATABASE_URL is required"
  exit 2
fi

export CONSOLE_URL="${CONSOLE_URL:-http://127.0.0.1:8000}"
export OMEGA_MULTIUSER_SIM_ADMINS="${OMEGA_MULTIUSER_SIM_ADMINS:-50}"
export OMEGA_MULTIUSER_SIM_MIN_EMPLOYEES="${OMEGA_MULTIUSER_SIM_MIN_EMPLOYEES:-5}"
export OMEGA_MULTIUSER_SIM_MAX_EMPLOYEES="${OMEGA_MULTIUSER_SIM_MAX_EMPLOYEES:-5}"
export OMEGA_MULTIUSER_SIM_CONCURRENT_OPS="${OMEGA_MULTIUSER_SIM_CONCURRENT_OPS:-300}"
export OMEGA_MULTIUSER_SIM_API_SAMPLE_ADMINS="${OMEGA_MULTIUSER_SIM_API_SAMPLE_ADMINS:-10}"
export OMEGA_MULTIUSER_SIM_API_SAMPLE_EMPLOYEES="${OMEGA_MULTIUSER_SIM_API_SAMPLE_EMPLOYEES:-40}"

PYTHONPATH=console "${PYTHON_BIN}" scripts/run_multiuser_isolation_simulation.py "$@"
