#!/usr/bin/env bash
set -euo pipefail

TENANT_ID="${TENANT_ID:?export TENANT_ID=<TENANT_UUID>}"
WORKSPACE_ID="${WORKSPACE_ID:?export WORKSPACE_ID=<WORKSPACE_UUID>}"
COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.yml}"
MONTHS_BACK="${MONTHS_BACK:-24}"
WINDOW_START="${WINDOW_START:-22:00}"     # host local time, low-load window
WINDOW_END="${WINDOW_END:-05:30}"
CHECKPOINT="${CHECKPOINT:-$HOME/sap_b1_initial_load.done}"
POLL_SECONDS="${POLL_SECONDS:-30}"

airflow() { docker compose -f "$COMPOSE_FILE" exec -T airflow airflow "$@"; }

MASTERS="CINF OADM OCRN ORTT OACT OFPR OPRC OCRG OSLP OSPP OWHS OITB OCRD OITM OITT ITT1"
DATED="OINV INV1 ORIN RIN1 ODLN DLN1 ORDN RDN1 ORDR RDR1 OPCH PCH1 ORPC RPC1 OPDN PDN1 OPOR POR1 OJDT JDT1 OINM IBT1 OWTR WTR1 OWOR WOR1"
SNAPSHOTS_LAST="OITW OBTN OBTQ OIBT OSRI SRI1"

touch "$CHECKPOINT"

in_window() {
    local now; now="$(date +%H:%M)"
    if [[ "$WINDOW_START" > "$WINDOW_END" ]]; then      # window crosses midnight
        [[ "$now" > "$WINDOW_START" || "$now" < "$WINDOW_END" ]]
    else
        [[ "$now" > "$WINDOW_START" && "$now" < "$WINDOW_END" ]]
    fi
}

run_state() {   # $1 = run_id -> prints the DAG run state
    airflow dags list-runs -d sap_b1_extract -o json 2>/dev/null \
        | python3 -c 'import json,sys; rid=sys.argv[1]; print(next((r["state"] for r in json.load(sys.stdin) if r["run_id"]==rid), "unknown"))' "$1"
}

trigger_and_wait() {   # $1 = run_id, $2 = conf JSON
    if grep -qx "$1" "$CHECKPOINT"; then echo "skip $1 (done)"; return 0; fi
    if ! in_window; then echo "outside the window; resume tonight from $CHECKPOINT"; exit 0; fi
    echo "trigger $1"
    airflow dags trigger sap_b1_extract --run-id "$1" --conf "$2" >/dev/null
    local state
    while :; do
        sleep "$POLL_SECONDS"
        state="$(run_state "$1")"
        case "$state" in
            success) echo "$1" >> "$CHECKPOINT"; echo "done $1"; return 0 ;;
            failed)  echo "FAILED $1: inspect the run in Airflow before continuing"; exit 1 ;;
            *)       ;;
        esac
    done
}

conf() {   # $1 = entity, $2 = mode, $3 = from_date (optional), $4 = to_date (optional)
    python3 - "$@" <<'PY'
import json, os, sys
entity, mode = sys.argv[1], sys.argv[2]
conf = {"entity": entity, "mode": mode, "cartridge_id": "sap_b1", "triggered_by": "initial_load",
        "tenant_id": os.environ["TENANT_ID"], "workspace_id": os.environ["WORKSPACE_ID"]}
if len(sys.argv) > 3 and sys.argv[3]:
    conf["from_date"], conf["to_date"] = sys.argv[3], sys.argv[4]
print(json.dumps(conf))
PY
}

for entity in $MASTERS; do
    trigger_and_wait "init_${entity}_full" "$(TENANT_ID="$TENANT_ID" WORKSPACE_ID="$WORKSPACE_ID" conf "$entity" full)"
done

for ((i = 0; i < MONTHS_BACK; i++)); do
    from="$(date -d "$(date +%Y-%m-01) -${i} month" +%F)"
    to="$(date -d "$from +1 month -1 day" +%F)"
    for entity in $DATED; do
        trigger_and_wait "init_${entity}_${from:0:7}" \
            "$(TENANT_ID="$TENANT_ID" WORKSPACE_ID="$WORKSPACE_ID" conf "$entity" historical "$from" "$to")"
    done
done

for entity in $SNAPSHOTS_LAST; do
    trigger_and_wait "init_${entity}_full" "$(TENANT_ID="$TENANT_ID" WORKSPACE_ID="$WORKSPACE_ID" conf "$entity" full)"
done
echo "initial load complete"
