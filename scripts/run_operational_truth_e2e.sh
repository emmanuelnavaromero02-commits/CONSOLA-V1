#!/usr/bin/env bash
set -euo pipefail

task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run_token="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-${RANDOM}"
project="omega-ot-${run_token//[^a-zA-Z0-9_-]/-}"
env_file="$(mktemp "${TMPDIR:-/tmp}/omega-ot-env.XXXXXX")"
verifier_secret="$(mktemp "$task_root/.omega-ot-verifier.XXXXXX")"
artifacts="${E2E_ARTIFACTS_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/omega-ot-artifacts.XXXXXX")}"
dag_dir="$(mktemp -d "$task_root/.omega-ot-dags.XXXXXX")"
chmod 0755 "$dag_dir"
wait_timeout="${E2E_WAIT_TIMEOUT_SECONDS:-420}"
if [[ ! "$wait_timeout" =~ ^[0-9]+$ ]] || (( wait_timeout < 60 || wait_timeout > 1800 )); then
  echo "E2E_WAIT_TIMEOUT_SECONDS must be an integer from 60 to 1800" >&2
  exit 2
fi
mkdir -p "$artifacts"
chmod 0777 "$artifacts"

if docker compose version >/dev/null 2>&1; then
  compose_cli=(docker compose)
elif docker-compose version >/dev/null 2>&1; then
  compose_cli=(docker-compose)
else
  echo "Docker Compose is required for operational truth E2E" >&2
  exit 2
fi
compose=(
  "${compose_cli[@]}" --project-name "$project" --env-file "$env_file"
  -f "$task_root/infra/e2e/compose.infrastructure.yml"
  -f "$task_root/infra/e2e/compose.apps.yml"
  -f "$task_root/infra/e2e/compose.airflow.yml"
  -f "$task_root/infra/e2e/compose.test.yml"
)

assert_no_oom() {
  local service container_id oom_events oom_killed
  for service in \
    postgres postgres_gold redis minio refinement mcp-infra console \
    airflow airflow-scheduler; do
    container_id="$("${compose[@]}" ps --quiet "$service")"
    if [[ -z "$container_id" ]]; then
      echo "E2E service missing while checking OOM: $service" >&2
      return 1
    fi
    oom_killed="$(docker inspect --format '{{.State.OOMKilled}}' "$container_id")"
    if [[ "$oom_killed" != "false" ]]; then
      echo "E2E service recorded an OOM kill: $service" >&2
      return 1
    fi
    oom_events="$(
      docker exec "$container_id" sh -c 'cat /sys/fs/cgroup/memory.events' |
        awk '$1 == "oom_kill" {print $2}'
    )"
    if [[ "${oom_events:-0}" != "0" ]]; then
      echo "E2E service cgroup recorded child OOM kills: $service=$oom_events" >&2
      return 1
    fi
  done
}

cleanup() {
  result=$?
  trap - EXIT INT TERM
  set +e
  "${compose[@]}" ps --all >"$artifacts/compose-ps.txt" 2>&1
  "${compose[@]}" logs --no-color --tail=250 >"$artifacts/compose.log" 2>&1
  scheduler_id="$("${compose[@]}" ps --all --quiet airflow-scheduler)"
  mkdir -p "$artifacts/airflow-logs"
  if [[ -n "$scheduler_id" ]]; then
    docker cp "$scheduler_id:/opt/airflow/logs/." "$artifacts/airflow-logs" \
      >"$artifacts/airflow-logs-copy.txt" 2>&1
  else
    : >"$artifacts/airflow-logs-copy.txt"
  fi
  project_containers=()
  while IFS= read -r container_id; do
    project_containers+=("$container_id")
  done < <("${compose[@]}" ps --all --quiet)
  if (( ${#project_containers[@]} )); then
    docker stats --no-stream --format '{{json .}}' "${project_containers[@]}" \
      >"$artifacts/docker-stats.jsonl" 2>&1
  else
    : >"$artifacts/docker-stats.jsonl"
  fi
  : >"$artifacts/memory-events.txt"
  for service in \
    postgres postgres_gold redis minio refinement mcp-infra console \
    airflow airflow-scheduler; do
    container_id="$("${compose[@]}" ps --all --quiet "$service")"
    if [[ -n "$container_id" ]]; then
      printf '[%s]\n' "$service" >>"$artifacts/memory-events.txt"
      docker exec "$container_id" sh -c 'cat /sys/fs/cgroup/memory.events' \
        >>"$artifacts/memory-events.txt" 2>&1
    fi
  done
  "${compose[@]}" images --format json >"$artifacts/images.jsonl" 2>&1
  "${compose[@]}" down --volumes --remove-orphans --timeout 20
  rm -f "$env_file"
  rm -f "$verifier_secret"
  rm -rf -- "$dag_dir"
  exit "$result"
}
trap cleanup EXIT INT TERM

secret() { openssl rand -hex 32; }
write_secret() { printf '%s=%s\n' "$1" "$(secret)" >>"$env_file"; }

printf '%s\n' \
  "POSTGRES_PASSWORD=$(secret)" \
  "MINIO_ACCESS_KEY=e2e-minio" \
  "MINIO_SECRET_KEY=$(secret)" \
  "INTERNAL_API_KEY=$(secret)" \
  "SECURITY_CONTEXT_SIGNING_KEY=$(secret)" \
  "JWT_SECRET_KEY=$(secret)" \
  "CONTROL_ROOM_EVIDENCE_SIGNING_KEY=$(secret)" \
  "AIRFLOW_SECRET_KEY=$(secret)" \
  "AIRFLOW_ADMIN_USER=e2e-admin" \
  "AIRFLOW_ADMIN_PASSWORD=$(secret)" \
  "SUPERSET_ADMIN_PASSWORD=$(secret)" \
  "E2E_ARTIFACTS_DIR=$artifacts" \
  "OMEGA_E2E_DAGS_DIR=$dag_dir" >"$env_file"

dag_modules=(
  file_ingest.py dataset_refresh_chain.py dataset_refresh_graph.py
  dataset_refresh_admission.py
  dataset_refresh_finalization.py
  dataset_refresh_idempotency.py dataset_refresh_materialize.py
  dataset_refresh_outcome.py runtime_security_context.py
)
for module in "${dag_modules[@]}"; do
  install -m 0444 "$task_root/airflow/dags/$module" "$dag_dir/$module"
done

password_vars=(
  OMEGA_CONSOLE_PASSWORD OMEGA_OUTCOME_BINDER_PASSWORD
  OMEGA_REFINEMENT_PASSWORD
  OMEGA_REFINEMENT_GOLD_PASSWORD OMEGA_GOLD_PUBLISHER_PASSWORD
  OMEGA_GOLD_VERIFIER_PASSWORD
  OMEGA_VAULT_PASSWORD
  OMEGA_WORKSPACE_PASSWORD OMEGA_MCP_INFRA_PASSWORD
  OMEGA_CARTRIDGE_SAP_HCM_PASSWORD OMEGA_CARTRIDGE_SAP_S4_PASSWORD
  OMEGA_CARTRIDGE_SAP_SF_PASSWORD OMEGA_AIRFLOW_DAG_PASSWORD
  OMEGA_AIRFLOW_META_PASSWORD OMEGA_SUPERSET_META_PASSWORD
  OMEGA_CARTRIDGE_REPLICON_PASSWORD OMEGA_CARTRIDGE_SALESFORCE_PASSWORD
  OMEGA_CARTRIDGE_HUBSPOT_PASSWORD OMEGA_CARTRIDGE_BANXICO_PASSWORD
  OMEGA_CARTRIDGE_INEGI_PASSWORD OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD
)
for name in "${password_vars[@]}"; do write_secret "$name"; done
verifier_password="$(awk -F= '$1=="OMEGA_GOLD_VERIFIER_PASSWORD" {print $2}' "$env_file")"
printf 'postgresql://omega_gold_verifier:%s@postgres_gold:5433/modecissions_gold\n' \
  "$verifier_password" >"$verifier_secret"
chmod 0600 "$verifier_secret"
printf 'GOLD_VERIFIER_DATABASE_URL_HOST_FILE=%s\n' "$verifier_secret" >>"$env_file"

pair_vars=(
  INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT
  INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT
  INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT
  INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT
  INTERNAL_API_KEY_REFINEMENT_TO_MCP_INFRA
  INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT
  INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA
  INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA
  INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA
  INTERNAL_API_KEY_MCP_INFRA_TO_CONSOLE
  INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE
  INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE
  INTERNAL_API_KEY_REPLICON_TO_CONSOLE
  INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE
  INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE
  INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE
  INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE
  INTERNAL_API_KEY_SAP_B1_TO_CONSOLE
  INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE
  INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE
)
for name in "${pair_vars[@]}"; do write_secret "$name"; done

pgoptions=""
for name in "${password_vars[@]}"; do
  case "$name" in
    OMEGA_REFINEMENT_GOLD_PASSWORD) continue ;;
  esac
  setting="$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')"
  setting="${setting%_password}"
  value="$(awk -F= -v key="$name" '$1==key {print substr($0,index($0,"=")+1)}' "$env_file")"
  pgoptions+=" -c app.${setting}_password=${value}"
done
printf 'E2E_PGOPTIONS=%s\n' "${pgoptions# }" >>"$env_file"

started="$(date +%s)"
"${compose[@]}" config --quiet
"${compose[@]}" build --pull postgres postgres_gold
"${compose[@]}" build --pull \
  airflow-init airflow airflow-scheduler refinement console e2e-test
"${compose[@]}" build --pull mcp-infra
"${compose[@]}" create e2e-test
built="$(date +%s)"
"${compose[@]}" up -d --wait --wait-timeout "$wait_timeout" \
  postgres postgres_gold redis minio
"${compose[@]}" up -d --wait --wait-timeout "$wait_timeout" refinement
"${compose[@]}" up -d --wait --wait-timeout "$wait_timeout" mcp-infra
"${compose[@]}" up -d --wait --wait-timeout "$wait_timeout" console
"${compose[@]}" up -d --wait --wait-timeout "$wait_timeout" airflow
"${compose[@]}" up --no-deps -d --wait --wait-timeout "$wait_timeout" \
  airflow-scheduler
"${compose[@]}" exec -T airflow-scheduler python - "${dag_modules[@]}" <<'PY'
import os
import sys
from pathlib import Path

root = Path("/opt/airflow/dags")
if os.geteuid() == 0:
    raise SystemExit("Airflow DAG preflight must run as a non-root user")
expected = set(sys.argv[1:])
visible = {path.name for path in root.glob("*.py")}
if visible != expected:
    raise SystemExit(f"Airflow DAG bundle mismatch: expected={expected} visible={visible}")
for name in expected:
    path = root / name
    if not path.is_file() or not os.access(path, os.R_OK):
        raise SystemExit(f"Airflow DAG module is not readable: {name}")
if os.access(root, os.W_OK):
    raise SystemExit("Airflow DAG bundle must remain read-only")
PY
assert_no_oom
healthy="$(date +%s)"
test_id="$("${compose[@]}" ps --all --quiet e2e-test)"
if [[ -z "$test_id" ]]; then
  echo "Pre-created E2E test container is missing" >&2
  exit 1
fi
set +e
docker start --attach "$test_id"
test_start_result=$?
set -e
test_exit="$(docker inspect --format '{{.State.ExitCode}}' "$test_id")"
docker cp "$test_id:/tmp/operational-truth-e2e.xml" \
  "$artifacts/operational-truth-e2e.xml" >/dev/null 2>&1 || true
if [[ "$test_start_result" -ne 0 || "$test_exit" != "0" ]]; then
  echo "Operational truth E2E container failed: start=$test_start_result exit=$test_exit" >&2
  exit 1
fi
assert_no_oom
tested="$(date +%s)"

python3 - "$artifacts/operational-truth-e2e.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
counts = {
    key: sum(int(suite.attrib.get(key, 0)) for suite in suites)
    for key in ("tests", "skipped", "failures", "errors")
}
if counts != {"tests": 1, "skipped": 0, "failures": 0, "errors": 0}:
    raise SystemExit(f"operational truth E2E JUnit is not clean: {counts}")
PY

printf '{"build_seconds":%s,"healthy_seconds":%s,"test_seconds":%s,"total_seconds":%s}\n' \
  "$((built-started))" "$((healthy-built))" "$((tested-healthy))" "$((tested-started))" \
  >"$artifacts/timings.json"
echo "Operational truth E2E passed; artifacts: $artifacts"
