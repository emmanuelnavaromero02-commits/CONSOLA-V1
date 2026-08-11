#!/usr/bin/env bash
# Recover only the exact, previously-proven GCP runtime after a host reboot.
set -Eeuo pipefail
set +x
umask 077

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: reboot-runtime.sh must run through sudo" >&2
  exit 10
fi

APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
CURRENT_LINK="${APP_ROOT}/current"
SHARED_ROOT="${APP_ROOT}/shared"
SHARED_ENV="${SHARED_ROOT}/infra.env"
GCP_RUNTIME_COMPOSE="${SHARED_ROOT}/docker-compose.gcp.yml"
OPERATION_MARKER="${SHARED_ROOT}/operation-state.json"
STATE_LINK="${SHARED_ROOT}/runtime-state"
BOOTSTRAP_STATE="${STATE_LINK}/bootstrap-state.json"
RUNTIME_PROVENANCE="${STATE_LINK}/runtime-provenance.json"
COMPOSE_PROJECT="${OMEGA_GCP_COMPOSE_PROJECT:-infra}"
MUTATION_STARTED=0
COMPOSE_READY=0

WRITER_SERVICES=(airflow-scheduler console workspace refinement vault mcp-infra airflow replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-successfactors sap-s4hana superset)
ONE_SHOT_MUTATORS=(airflow-init minio-init postgres_dev_seed superset-init)
MUTATING_SERVICES=("${WRITER_SERVICES[@]}" "${ONE_SHOT_MUTATORS[@]}")

fail() {
  printf 'OMEGA_GCP_REBOOT_CHECK\t%s\tFAIL\t%s\n' "$1" "${2:-}" >&2
  exit "${3:-20}"
}

cleanup() {
  local rc=$?
  if [[ "$rc" -ne 0 && "$MUTATION_STARTED" == "1" && "$COMPOSE_READY" == "1" ]]; then
    "${COMPOSE[@]}" stop --timeout 30 "${MUTATING_SERVICES[@]}" >/dev/null 2>&1 || true
    printf 'OMEGA_GCP_REBOOT_CHECK\tfail-closed fence\tPASS\tdurable marker retained\n'
  fi
  exit "$rc"
}
trap cleanup EXIT

if [[ ! "$COMPOSE_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  fail "Compose project" "invalid project name"
fi
if [[ -e "$OPERATION_MARKER" ]]; then
  fail "durable operation fence" "incomplete operation blocks reboot recovery" 75
fi
if [[ ! -L "$CURRENT_LINK" || ! -L "$STATE_LINK" || ! -s "$BOOTSTRAP_STATE" || \
      ! -s "$SHARED_ENV" || ! -s "$GCP_RUNTIME_COMPOSE" || ! -s "$RUNTIME_PROVENANCE" ]]; then
  fail "canonical reboot inputs" "current/shared runtime provenance is incomplete"
fi
CURRENT_RELEASE="$(readlink -f "$CURRENT_LINK")"
case "$CURRENT_RELEASE" in
  "${APP_ROOT}/releases/"*) ;;
  *) fail "current release confinement" "current points outside release root" ;;
esac
DEPLOY_REF="$(basename "$CURRENT_RELEASE")"
VERSION="$(tr -d '\r\n' < "${CURRENT_RELEASE}/VERSION" 2>/dev/null || true)"
if [[ ! "$DEPLOY_REF" =~ ^[0-9a-f]{40}$ || -z "$VERSION" ]]; then
  fail "current release identity" "invalid deploy ref or VERSION"
fi
BASE_COMPOSE="${CURRENT_RELEASE}/infra/docker-compose.yml"
RUNTIME_CONTRACT="${OMEGA_GCP_RUNTIME_CONTRACT:-${CURRENT_RELEASE}/scripts/gcp/runtime_contract.py}"
case "$RUNTIME_CONTRACT" in
  "${CURRENT_RELEASE}/scripts/gcp/runtime_contract.py"|"${SHARED_ROOT}/bin/"[0-9a-f]*/runtime_contract.py) ;;
  *) fail "runtime verifier confinement" "runtime verifier path is outside current/shared helpers" ;;
esac
if [[ ! -s "$BASE_COMPOSE" || ! -x "$RUNTIME_CONTRACT" ]]; then
  fail "current release files" "base Compose or runtime verifier missing"
fi
if grep -Eq '^(GHCR_[A-Z0-9_]*(TOKEN|PASSWORD|SECRET|CREDENTIAL|AUTH|USER)|GITHUB_TOKEN|DOCKER_AUTH_CONFIG)=' "$SHARED_ENV"; then
  fail "server-owned registry credential boundary" "registry credential found in runtime env"
fi

exec 9>"/var/lock/omega-gcp-day2.lock"
if ! flock -n 9; then
  fail "exclusive day-2 lock" "another canonical operation is active"
fi

PROVENANCE_MODE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["mode"])' "$RUNTIME_PROVENANCE")"
PROVENANCE_ARGS=()
if [[ "$PROVENANCE_MODE" == "day2" ]]; then
  LOCK_ENV="${SHARED_ROOT}/image-locks/${DEPLOY_REF}/release-images.env"
  RELEASE_COMPOSE="${CURRENT_RELEASE}/infra/terraform-gcp/release/docker-compose.release.yml"
  if [[ ! -s "$LOCK_ENV" || ! -s "$RELEASE_COMPOSE" ]]; then
    fail "day-2 reboot inputs" "exact image lock or release overlay missing"
  fi
  COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
    --env-file "$LOCK_ENV" -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" \
    -f "$RELEASE_COMPOSE" --profile sap)
  PROVENANCE_ARGS=(--lock-env "$LOCK_ENV")
elif [[ "$PROVENANCE_MODE" == "bootstrap" ]]; then
  COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
    -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" --profile sap)
else
  fail "runtime provenance" "unsupported provenance mode"
fi
COMPOSE_READY=1
"${COMPOSE[@]}" config -q

python3 - "$OPERATION_MARKER" "$DEPLOY_REF" <<'PY'
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone

path = pathlib.Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "operation": "reboot-recovery",
    "state": "fencing",
    "deploy_ref": sys.argv[2],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
MUTATION_STARTED=1
"${COMPOSE[@]}" stop --timeout 60 "${MUTATING_SERVICES[@]}"
for service in "${MUTATING_SERVICES[@]}"; do
  if docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
      --filter "label=com.docker.compose.service=${service}" | grep -q .; then
    fail "reboot writer fence" "service still running=${service}"
  fi
done

# Reuse existing containers only. A missing/replaced container is provenance
# drift and requires an explicit day-2 operation; reboot never builds or pulls.
readarray -t PERSISTENT_SERVICES < <("${COMPOSE[@]}" config --services \
  | grep -Ev '^(airflow-scheduler|airflow-init|minio-init|postgres_dev_seed|superset-init)$')
"${COMPOSE[@]}" start "${PERSISTENT_SERVICES[@]}" >/dev/null

RUNTIME_GREEN=0
for _ in $(seq 1 120); do
  if python3 "$RUNTIME_CONTRACT" provenance --provenance "$RUNTIME_PROVENANCE" \
      --compose-project "$COMPOSE_PROJECT" --deploy-ref "$DEPLOY_REF" --version "$VERSION" \
      --scheduler stopped "${PROVENANCE_ARGS[@]}" >/dev/null 2>&1; then
    RUNTIME_GREEN=1
    break
  fi
  sleep 3
done
if [[ "$RUNTIME_GREEN" != "1" ]]; then
  fail "reboot runtime recovery" "15 exact services did not return healthy with scheduler fenced"
fi

"${COMPOSE[@]}" start airflow-scheduler >/dev/null
FINAL_GREEN=0
for _ in $(seq 1 60); do
  if python3 "$RUNTIME_CONTRACT" provenance --provenance "$RUNTIME_PROVENANCE" \
      --compose-project "$COMPOSE_PROJECT" --deploy-ref "$DEPLOY_REF" --version "$VERSION" \
      --one-shots "${PROVENANCE_ARGS[@]}" >/dev/null 2>&1; then
    FINAL_GREEN=1
    break
  fi
  sleep 3
done
if [[ "$FINAL_GREEN" != "1" ]]; then
  fail "reboot runtime recovery" "exact runtime/scheduler/one-shot provenance did not recover"
fi

rm -f -- "$OPERATION_MARKER"
MUTATION_STARTED=0
printf 'OMEGA_GCP_REBOOT_CHECK\texact runtime recovery\tPASS\tref=%s version=%s mode=%s\n' \
  "$DEPLOY_REF" "$VERSION" "$PROVENANCE_MODE"
