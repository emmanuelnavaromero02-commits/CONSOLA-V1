#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file_path="${COMPOSE_FILE:-${ROOT}/infra/docker-compose.yml}"
if [[ "${COMPOSE_FILE+x}" == "x" ]] && [[ -z "${COMPOSE_FILE}" ]]; then
    unset COMPOSE_FILE
fi
readonly compose_file_path
TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-240}"
SLEEP_SECONDS="${WAIT_SLEEP_SECONDS:-5}"

deadline=$((SECONDS + TIMEOUT_SECONDS))
ready_streak=0
required_ready_streak="${WAIT_READY_STREAK:-2}"
CONSOLE_READY_URL="${CONSOLE_READY_URL:-http://127.0.0.1:8000/readyz}"
FULL_STACK="${OMEGA_WAIT_FULL_STACK:-1}"

container_health() {
    local name="$1"
    local output
    local rc
    if output="$(
        docker inspect \
            --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
            "$name" 2>&1
    )"; then
        printf '%s\n' "${output}"
        return 0
    else
        rc=$?
    fi
    if [[ "${rc}" -eq 97 ]]; then
        printf '%s\n' "${output}" >&2
        return "${rc}"
    fi
    printf 'starting\n'
}

http_ok() {
    curl -fsS --max-time 5 "$1" >/dev/null 2>&1
}

all_healthy() {
    local status
    for name in "$@"; do
        status="$(container_health "$name")"
        case "$status" in
            healthy|running) ;;
            *) return 1 ;;
        esac
    done
}

all_http_ok() {
    local url
    for url in "$@"; do
        http_ok "$url" || return 1
    done
}

echo "[wait_for_health] Waiting up to ${TIMEOUT_SECONDS}s for OMEGA stack..."

while [ "${SECONDS}" -lt "${deadline}" ]; do
    console_status="$(container_health mode_console)"
    postgres_status="$(container_health mode_postgres)"
    postgres_gold_status="$(container_health mode_postgres_gold)"
    superset_status="$(container_health mode_superset)"
    salesforce_status="$(container_health omega_salesforce)"
    hubspot_status="$(container_health mode_hubspot)"
    sap_hcm_status="$(container_health mode_sap_hcm)"
    sap_s4_status="$(container_health mode_sap_s4hana)"
    sap_sf_status="$(container_health mode_sap_successfactors)"
    sap_b1_status="$(container_health mode_sap_b1)"

    api_ok=0
    services_ok=0
    external_ok=0
    restarting="$(docker ps --filter 'status=restarting' --format '{{.Names}}' 2>/dev/null || true)"
    http_ok "${CONSOLE_READY_URL}" && api_ok=1 || true
    all_healthy mode_console mode_workspace mode_mcp_infra mode_vault mode_refinement mode_postgres mode_postgres_gold mode_minio mode_airflow omega_replicon omega_salesforce mode_hubspot && services_ok=1 || true
    if [ "${FULL_STACK}" = "1" ]; then
        all_healthy mode_superset mode_sap_hcm mode_sap_s4hana mode_sap_successfactors mode_sap_b1 && \
            all_http_ok \
                http://127.0.0.1:8088/health \
                http://127.0.0.1:8201/health \
                http://127.0.0.1:8205/health \
                http://127.0.0.1:8210/health \
                http://127.0.0.1:8202/health \
                http://127.0.0.1:8203/health \
                http://127.0.0.1:8204/health \
                http://127.0.0.1:8206/health && external_ok=1 || true
    else
        external_ok=1
    fi

    echo "[wait_for_health] console=${console_status} postgres=${postgres_status} postgres_gold=${postgres_gold_status} superset=${superset_status} salesforce=${salesforce_status} hubspot=${hubspot_status} sap_hcm=${sap_hcm_status} sap_s4=${sap_s4_status} sap_sf=${sap_sf_status} sap_b1=${sap_b1_status} api=${api_ok} core=${services_ok} external=${external_ok} restarting=${restarting:-none}"

    if [ "${services_ok}" = "1" ] \
        && [ "${api_ok}" = "1" ] \
        && [ "${external_ok}" = "1" ] \
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
docker compose -f "${compose_file_path}" --profile sap ps || true
echo "[wait_for_health] mode_console recent logs:"
docker logs mode_console --tail 80 2>&1 || true
echo "[wait_for_health] mode_superset recent logs:"
docker logs mode_superset --tail 80 2>&1 || true
exit 1
