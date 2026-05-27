#!/usr/bin/env bash
# Wait until the local OMEGA compose stack is ready for smoke/E2E.
#
# Split architecture (beta): waits for BOTH the Next.js frontend on
# :3000 (official, temporary) AND the FastAPI backend on :8000 (APIs +
# Control Room). This is intentional — the stack is not 8000-only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${ROOT}/infra/docker-compose.yml}"
TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-240}"
SLEEP_SECONDS="${WAIT_SLEEP_SECONDS:-5}"

deadline=$((SECONDS + TIMEOUT_SECONDS))
ready_streak=0
required_ready_streak="${WAIT_READY_STREAK:-2}"
CONSOLE_READY_URL="${CONSOLE_READY_URL:-http://127.0.0.1:8000/readyz}"

echo "[wait_for_health] Waiting up to ${TIMEOUT_SECONDS}s for OMEGA stack..."

while [ "${SECONDS}" -lt "${deadline}" ]; do
    console_status="$(docker inspect --format='{{.State.Health.Status}}' mode_console 2>/dev/null || echo starting)"
    next_status="$(docker inspect --format='{{.State.Health.Status}}' omega_console_next 2>/dev/null || echo starting)"
    postgres_status="$(docker inspect --format='{{.State.Health.Status}}' mode_postgres 2>/dev/null || echo starting)"
    postgres_gold_status="$(docker inspect --format='{{.State.Health.Status}}' mode_postgres_gold 2>/dev/null || echo starting)"

    api_ok=0
    next_ok=0
    restarting="$(docker ps --filter 'status=restarting' --format '{{.Names}}' 2>/dev/null || true)"
    curl -fsS --max-time 5 "${CONSOLE_READY_URL}" >/dev/null 2>&1 && api_ok=1 || true
    curl -fsS --max-time 5 http://127.0.0.1:3000/api/health >/dev/null 2>&1 && next_ok=1 || true

    echo "[wait_for_health] console=${console_status} next=${next_status} postgres=${postgres_status} postgres_gold=${postgres_gold_status} api=${api_ok} next_api=${next_ok} restarting=${restarting:-none}"

    if [ "${console_status}" = "healthy" ] \
        && [ "${next_status}" = "healthy" ] \
        && [ "${postgres_status}" = "healthy" ] \
        && [ "${postgres_gold_status}" = "healthy" ] \
        && [ "${api_ok}" = "1" ] \
        && [ "${next_ok}" = "1" ] \
        && [ -z "${restarting}" ]; then
        ready_streak=$((ready_streak + 1))
        if [ "${ready_streak}" -ge "${required_ready_streak}" ]; then
            echo "[wait_for_health] Stack ready."
            exit 0
        fi
    else
        ready_streak=0
    fi

    sleep "${SLEEP_SECONDS}"
done

echo "[wait_for_health] ERROR: stack did not become healthy in time."
docker compose -f "${COMPOSE_FILE}" ps || true
echo "[wait_for_health] mode_console recent logs:"
docker logs mode_console --tail 80 2>&1 || true
echo "[wait_for_health] omega_console_next recent logs:"
docker logs omega_console_next --tail 80 2>&1 || true
exit 1
