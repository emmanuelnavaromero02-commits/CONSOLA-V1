#!/usr/bin/env bash
# Apply the exact audited 1.45.207 migration delta to existing operational and
# Gold databases.  Both ledgers are preflighted before the first durable write;
# filename-only migration skipping is deliberately forbidden.
set -Eeuo pipefail
set +x
umask 077

EXPECTED_TOOL_PATH="/usr/sbin:/usr/bin:/sbin:/bin"
if [[ "${OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT-0}" != "1" ]]; then
  EXPECTED_TOOL_PATH="${EXPECTED_TOOL_PATH}:/usr/local/bin:/opt/homebrew/bin"
fi
if [[ "${OMEGA_MIGRATION_HERMETIC-}" != "1" || \
      -z "${OMEGA_MIGRATION_RELEASE_ROOT-}" || \
      "${PATH-}" != "$EXPECTED_TOOL_PATH" ]]; then
  echo "[migrate] ERROR: use scripts/run_db_migrations.py" >&2
  exit 9
fi
ROOT_DIR="${OMEGA_MIGRATION_RELEASE_ROOT}"
if [[ "$ROOT_DIR" != /* || "$(cd "$ROOT_DIR" && pwd -P)" != "$ROOT_DIR" ]]; then
  echo "[migrate] ERROR: hermetic release root is not physical and absolute" >&2
  exit 9
fi
COMPOSE_FILE="${OMEGA_MIGRATION_COMPOSE_FILE:-${ROOT_DIR}/infra/docker-compose.yml}"
ENV_FILE="${OMEGA_MIGRATION_ENV_FILE:-${ROOT_DIR}/infra/.env}"
COMPOSE_PROJECT_NAME_VALUE="${OMEGA_MIGRATION_COMPOSE_PROJECT_NAME:-}"
GUARD="${ROOT_DIR}/scripts/migration_guard.py"
SAFE_IO="${ROOT_DIR}/scripts/gcp/safe_io.py"
BACKEND_CONTROL="${ROOT_DIR}/scripts/migration_backend_control.py"
BASELINE_REF="6b12883c5b5ea0537120279ccbee4947137998a2"
BASELINE_MANIFEST_DEFAULT="${ROOT_DIR}/infra/migrations/manifests/gcp-live-${BASELINE_REF}.json"
RELEASE_MANIFEST_DEFAULT="${ROOT_DIR}/infra/migrations/manifests/v1.45.207-beta.json"
BASELINE_MANIFEST_SHA256_DEFAULT="b6cb33c9b1a0f93e13fe2eb68f2e8fff1fdeedb2979bbfb22840a2a35d2e4a18"
RELEASE_MANIFEST_SHA256_DEFAULT="79a607d045b853fba26812311b930b21d84e676d6cbde43f05ad4ec8150700b2"
WORKDIR=""
RECEIPT_DIR=""
REQUESTED_RECEIPT_DIR="${OMEGA_MIGRATION_RECEIPT_DIR-}"
LAST_PHASE="not_started"
LAST_COMMIT_STATE="none"
MIGRATION_RUN_ID="${OMEGA_MIGRATION_RUN_ID-}"
BACKEND_CLEANUP_REQUIRED=0
POSTGRES_CONTAINER_ID=""
POSTGRES_GOLD_CONTAINER_ID=""
PLAN=""
OPERATIONAL_AFTER=""
GOLD_AFTER=""
CONTRACT_ARGS=()

terminate_migration_backends() {
  if [[ "$BACKEND_CLEANUP_REQUIRED" != "1" ]]; then
    return 0
  fi
  python3 "$BACKEND_CONTROL" \
    --run-id "$MIGRATION_RUN_ID" \
    --container-id "$POSTGRES_CONTAINER_ID" \
    --container-id "$POSTGRES_GOLD_CONTAINER_ID" >/dev/null
}

reconcile_commit_state() {
  local failure_code="$1"
  local operational_state gold_state reconciled
  if [[ -z "$WORKDIR" || ! -f "$PLAN" ]]; then
    LAST_PHASE="failure_state_indeterminate"
    LAST_COMMIT_STATE="indeterminate"
    return 0
  fi
  if ! dump_operational_ledger > "$OPERATIONAL_AFTER" || \
     ! dump_gold_ledger > "$GOLD_AFTER"; then
    LAST_PHASE="failure_state_indeterminate"
    LAST_COMMIT_STATE="indeterminate"
    return 0
  fi
  operational_state="$(
    python3 "$GUARD" classify-ledger "${CONTRACT_ARGS[@]}" \
      --database operational --ledger "$OPERATIONAL_AFTER" 2>/dev/null
  )" || operational_state="indeterminate"
  gold_state="$(
    python3 "$GUARD" classify-ledger "${CONTRACT_ARGS[@]}" \
      --database gold --ledger "$GOLD_AFTER" 2>/dev/null
  )" || gold_state="indeterminate"
  case "${operational_state}|${gold_state}" in
    baseline\|baseline|baseline_expected\|baseline)
      reconciled="none"
      ;;
    baseline\|baseline_expected|baseline_expected\|baseline_expected)
      reconciled="gold_only"
      ;;
    release_expected\|baseline_expected|release_guarded\|baseline_expected)
      reconciled="gold_and_operational"
      ;;
    *)
      reconciled="indeterminate"
      ;;
  esac
  LAST_PHASE="failure_state_reconciled"
  LAST_COMMIT_STATE="$reconciled"
  record_event 80-failure-reconciled.json "$LAST_PHASE" FAIL \
    "$LAST_COMMIT_STATE" "$failure_code" || return 1
}

record_event() {
  local event_name="$1"
  local phase="$2"
  local status="$3"
  local commit_state="$4"
  local exit_code="$5"
  local recorded_at
  local destination
  if [[ -z "$RECEIPT_DIR" ]]; then
    return 0
  fi
  if [[ ! "$event_name" =~ ^(05-runner-started|10-preflight-passed|15-gold-commit-intent|20-gold-committed|25-operational-commit-intent|30-operational-committed|40-operational-authority-committed|50-gold-authority-committed|60-postflight-passed|80-failure-reconciled|90-terminal)\.json$ ]] || \
     [[ ! "$phase" =~ ^[a-z_]+$ ]] || \
     [[ ! "$status" =~ ^(IN_PROGRESS|PASS|FAIL)$ ]] || \
     [[ ! "$commit_state" =~ ^(none|indeterminate|gold_only|gold_and_operational|gold_operational_and_authority)$ ]] || \
     [[ ! "$exit_code" =~ ^[0-9]{1,3}$ ]]; then
    echo "[migrate] ERROR: internal receipt event is invalid" >&2
    return 1
  fi
  recorded_at="$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')" || return 1
  destination="${RECEIPT_DIR}/${event_name}"
  if ! (
    set -o noclobber
    umask 077
    printf '{"candidate_ref":"%s","database_commit_state":"%s","exit_code":%s,"operation":"gcp_day2_database_migration","phase":"%s","recorded_at":"%s","release_manifest_sha256":"%s","release_version":"%s","run_id":"%s","schema_version":1,"status":"%s"}\n' \
      "${OMEGA_MIGRATION_CANDIDATE_REF}" \
      "$commit_state" \
      "$exit_code" \
      "$phase" \
      "$recorded_at" \
      "${OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256}" \
      "${OMEGA_MIGRATION_RELEASE_VERSION}" \
      "$MIGRATION_RUN_ID" \
      "$status" > "$destination"
  ); then
    echo "[migrate] ERROR: immutable receipt event already exists or cannot be written" >&2
    return 1
  fi
  /bin/chmod 0400 "$destination" || return 1
  python3 "$SAFE_IO" fsync-file "$destination" || return 1
  python3 "$SAFE_IO" fsync-dir "$RECEIPT_DIR" || return 1
}

advance_phase() {
  local event_name="$1"
  local phase="$2"
  local commit_state="$3"
  LAST_PHASE="$phase"
  LAST_COMMIT_STATE="$commit_state"
  if ! record_event "$event_name" "$phase" IN_PROGRESS "$commit_state" 0; then
    fail "cannot persist migration phase evidence" 24
  fi
}

cleanup() {
  local rc="$1"
  local terminal_status="FAIL"
  trap - EXIT
  trap '' HUP INT TERM
  if [[ "$rc" -ne 0 ]] && ! terminate_migration_backends; then
    echo "[migrate] ERROR: migration backends could not be terminated and verified" >&2
    rc=25
  fi
  if [[ "$rc" -ne 0 ]] && ! reconcile_commit_state "$rc"; then
    echo "[migrate] ERROR: failure database state could not be reconciled" >&2
    LAST_PHASE="failure_state_indeterminate"
    LAST_COMMIT_STATE="indeterminate"
    rc=25
  fi
  if [[ "$rc" -eq 0 ]]; then
    terminal_status="PASS"
  fi
  if [[ -n "$RECEIPT_DIR" ]] && \
     ! record_event 90-terminal.json "$LAST_PHASE" "$terminal_status" \
         "$LAST_COMMIT_STATE" "$rc"; then
    echo "[migrate] ERROR: terminal migration receipt could not be persisted" >&2
    if [[ "$rc" -eq 0 ]]; then
      rc=24
    fi
  fi
  if [[ -n "${WORKDIR}" && "${WORKDIR}" == /tmp/omega-migration-guard.* ]]; then
    rm -rf -- "${WORKDIR}"
  fi
  exit "$rc"
}
trap 'cleanup "$?"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  echo "[migrate] ERROR: $1" >&2
  exit "${2:-20}"
}

if [[ ! -f "$COMPOSE_FILE" ]]; then
  fail "Compose file is missing" 10
fi
if [[ ! -x "$GUARD" && ! -f "$GUARD" ]]; then
  fail "migration guard is missing" 11
fi
if [[ ! -f "$SAFE_IO" ]]; then
  fail "safe I/O helper is missing" 11
fi
if [[ ! -f "$BACKEND_CONTROL" ]]; then
  fail "migration backend control helper is missing" 11
fi

if [[ -n "$REQUESTED_RECEIPT_DIR" ]]; then
  if [[ "${EUID}" -ne 0 ]] || \
     [[ ! "$REQUESTED_RECEIPT_DIR" =~ ^/opt/modecissions/shared/operation-receipts/migration-[0-9a-f]{40}-[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]] || \
     [[ "$(/usr/bin/readlink -f "$REQUESTED_RECEIPT_DIR" 2>/dev/null || true)" != "$REQUESTED_RECEIPT_DIR" ]] || \
     [[ "$(/usr/bin/stat -Lc '%u:%g:%a' "$REQUESTED_RECEIPT_DIR" 2>/dev/null || true)" != "0:0:700" ]] || \
     [[ ! "${OMEGA_MIGRATION_CANDIDATE_REF-}" =~ ^[0-9a-f]{40}$ ]] || \
     [[ "${OMEGA_MIGRATION_RELEASE_VERSION-}" != "1.45.207-beta" ]] || \
     [[ ! "${OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256-}" =~ ^[0-9a-f]{64}$ ]]; then
    fail "server-owned migration receipt contract is invalid" 24
  fi
  RECEIPT_DIR="$REQUESTED_RECEIPT_DIR"
  advance_phase 05-runner-started.json started none
elif [[ "${OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT-0}" == "1" && "${EUID}" -eq 0 ]]; then
  fail "explicit root migration is missing its server-owned receipt" 24
fi

EXPLICIT_CONTRACT="${OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT-0}"
BOOTSTRAP_MODE="${OMEGA_MIGRATION_BOOTSTRAP_MODE-0}"
ALLOW_BOOTSTRAP_LEDGER="${OMEGA_MIGRATION_ALLOW_BOOTSTRAP_LEDGER-0}"
for flag_binding in \
  "OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT=${EXPLICIT_CONTRACT}" \
  "OMEGA_MIGRATION_BOOTSTRAP_MODE=${BOOTSTRAP_MODE}" \
  "OMEGA_MIGRATION_ALLOW_BOOTSTRAP_LEDGER=${ALLOW_BOOTSTRAP_LEDGER}"; do
  if [[ "${flag_binding#*=}" != "0" && "${flag_binding#*=}" != "1" ]]; then
    fail "${flag_binding%%=*} must be exactly 0 or 1" 13
  fi
done
if [[ "$EXPLICIT_CONTRACT" == "1" && "$BOOTSTRAP_MODE" == "1" ]]; then
  fail "bootstrap source-inferred ledger evidence is forbidden for an explicit day-2 contract" 13
fi
if [[ ! "$MIGRATION_RUN_ID" =~ ^omega_migration_[1-9][0-9]{0,19}_[1-9][0-9]{0,29}$ ]]; then
  fail "migration run identity is missing or invalid" 13
fi
if [[ "$ALLOW_BOOTSTRAP_LEDGER" == "1" && "$BOOTSTRAP_MODE" != "1" ]]; then
  fail "bootstrap ledger opt-in is invalid when bootstrap mode is disabled" 13
fi

MIGRATION_ENVIRONMENT="${OMEGA_MIGRATION_ENVIRONMENT-${APP_ENV-}}"
if [[ "$EXPLICIT_CONTRACT" == "0" ]]; then
  if [[ "$MIGRATION_ENVIRONMENT" != "local" && \
        "$MIGRATION_ENVIRONMENT" != "development" && \
        "$MIGRATION_ENVIRONMENT" != "test" ]]; then
    fail "non-attested migration fallback is restricted to explicit local/development/test" 13
  fi
fi
if [[ "$BOOTSTRAP_MODE" == "1" && "$ALLOW_BOOTSTRAP_LEDGER" != "1" ]]; then
  fail "bootstrap source-inferred ledger evidence requires explicit local opt-in" 13
fi

# The dotenv file is data for Compose, never executable shell.  Validate its
# ownership/mode and strict assignment grammar before giving it to Compose;
# release control-plane inputs are forbidden in that data file.
export COMPOSE_DISABLE_ENV_FILE=1
if [[ "$EXPLICIT_CONTRACT" == "0" && -x /opt/homebrew/bin/docker-compose ]]; then
  # Docker Desktop exposes Compose through a user-owned plugin directory on
  # macOS. The hermetic launcher deliberately strips that directory and any
  # registry credentials, so local validation uses the fixed standalone shim.
  COMPOSE=(/opt/homebrew/bin/docker-compose)
else
  COMPOSE=(docker compose)
fi
if [[ -e "${ENV_FILE}" || -L "${ENV_FILE}" ]]; then
  python3 "$SAFE_IO" env-validate \
    --path "$ENV_FILE" \
    --forbid-prefix OMEGA_MIGRATION_
  COMPOSE+=(--env-file "$ENV_FILE")
fi

if [[ -n "${COMPOSE_PROJECT_NAME_VALUE}" ]]; then
  if [[ ! "${COMPOSE_PROJECT_NAME_VALUE}" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    fail "invalid OMEGA_MIGRATION_COMPOSE_PROJECT_NAME" 12
  fi
  COMPOSE+=(--project-name "${COMPOSE_PROJECT_NAME_VALUE}")
elif [[ "$EXPLICIT_CONTRACT" == "1" ]]; then
  fail "explicit release contract requires OMEGA_MIGRATION_COMPOSE_PROJECT_NAME" 12
fi
COMPOSE+=(-f "${COMPOSE_FILE}")

if [[ "$EXPLICIT_CONTRACT" == "1" ]]; then
  required_contract=(
    OMEGA_MIGRATION_OLD_REF
    OMEGA_MIGRATION_CANDIDATE_REF
    OMEGA_MIGRATION_RELEASE_VERSION
    OMEGA_MIGRATION_BASELINE_MANIFEST
    OMEGA_MIGRATION_BASELINE_MANIFEST_SHA256
    OMEGA_MIGRATION_RELEASE_MANIFEST
    OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256
    OMEGA_MIGRATION_RELEASE_ATTESTATION
  )
  for variable in "${required_contract[@]}"; do
    if [[ -z "${!variable:-}" ]]; then
      fail "explicit release contract is missing ${variable}" 13
    fi
  done
fi

if [[ "$BOOTSTRAP_MODE" == "1" ]]; then
  if [[ "$MIGRATION_ENVIRONMENT" != "local" && \
        "$MIGRATION_ENVIRONMENT" != "development" && \
        "$MIGRATION_ENVIRONMENT" != "test" ]]; then
    fail "bootstrap source-inferred ledger evidence is restricted to local/development/test" 13
  fi
fi
OLD_REF="${OMEGA_MIGRATION_OLD_REF:-${BASELINE_REF}}"
CANDIDATE_REF="${OMEGA_MIGRATION_CANDIDATE_REF-}"
if [[ "$EXPLICIT_CONTRACT" == "0" ]]; then
  if ! git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    fail "local migration fallback requires a Git worktree" 14
  fi
  GIT_HEAD="$(git -C "$ROOT_DIR" rev-parse --verify HEAD)" || \
    fail "local migration fallback cannot resolve Git HEAD" 14
  if [[ -z "$CANDIDATE_REF" ]]; then
    CANDIDATE_REF="$GIT_HEAD"
  fi
  if [[ "$CANDIDATE_REF" != "$GIT_HEAD" ]] || \
     ! git -C "$ROOT_DIR" cat-file -e "${CANDIDATE_REF}^{commit}" 2>/dev/null; then
    fail "local migration candidate must be the existing exact Git HEAD" 14
  fi
  RELEVANT_GIT_PATHS=(
    VERSION
    infra/docker-compose.yml
    infra/init
    infra/init_gold
    infra/migrations/manifests
    scripts/apply_db_migrations.sh
    scripts/migration_backend_control.py
    scripts/run_db_migrations.py
    scripts/gcp/safe_io.py
    scripts/generate_migration_manifests.py
    scripts/migration_guard.py
  )
  if ! RELEVANT_GIT_STATUS="$(
    git -C "$ROOT_DIR" status --porcelain --untracked-files=all -- \
      "${RELEVANT_GIT_PATHS[@]}"
  )"; then
    fail "local migration fallback cannot inspect the relevant Git tree" 14
  fi
  if [[ -n "$RELEVANT_GIT_STATUS" ]]; then
    fail "local migration fallback requires a clean relevant Git tree" 14
  fi
elif [[ -z "$CANDIDATE_REF" ]]; then
  fail "OMEGA_MIGRATION_CANDIDATE_REF is required by the production contract" 14
fi
RELEASE_VERSION="${OMEGA_MIGRATION_RELEASE_VERSION:-$(tr -d '\r\n' < "${ROOT_DIR}/VERSION")}"
BASELINE_MANIFEST="${OMEGA_MIGRATION_BASELINE_MANIFEST:-${BASELINE_MANIFEST_DEFAULT}}"
BASELINE_MANIFEST_SHA256="${OMEGA_MIGRATION_BASELINE_MANIFEST_SHA256:-${BASELINE_MANIFEST_SHA256_DEFAULT}}"
RELEASE_MANIFEST="${OMEGA_MIGRATION_RELEASE_MANIFEST:-${RELEASE_MANIFEST_DEFAULT}}"
RELEASE_MANIFEST_SHA256="${OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256:-${RELEASE_MANIFEST_SHA256_DEFAULT}}"

CONTRACT_ARGS=(
  --old-ref "$OLD_REF"
  --candidate-ref "$CANDIDATE_REF"
  --release-version "$RELEASE_VERSION"
  --baseline-manifest "$BASELINE_MANIFEST"
  --baseline-manifest-sha256 "$BASELINE_MANIFEST_SHA256"
  --release-manifest "$RELEASE_MANIFEST"
  --release-manifest-sha256 "$RELEASE_MANIFEST_SHA256"
)

if [[ "$EXPLICIT_CONTRACT" == "1" ]]; then
  RELEASE_ATTESTATION="${OMEGA_MIGRATION_RELEASE_ATTESTATION}"
  if [[ "$RELEASE_ATTESTATION" != "${ROOT_DIR}/.omega-release.json" ]]; then
    fail "production release attestation must be ROOT/.omega-release.json" 14
  fi
  if ! RELEASE_TREE_SHA256="$(
    python3 "$SAFE_IO" tree-sha256 --root "$ROOT_DIR" \
      --exclude .omega-release.json --require-read-only
  )"; then
    fail "immutable production release tree verification failed" 14
  fi
  python3 "$GUARD" verify-release-attestation "${CONTRACT_ARGS[@]}" \
    --attestation "$RELEASE_ATTESTATION" \
    --tree-sha256 "$RELEASE_TREE_SHA256"
else
  python3 "$GUARD" verify-manifests "${CONTRACT_ARGS[@]}"
fi

# Production migration is one phase of the same host-wide day-2 transaction
# as backup, release and restore rehearsal. Reuse its inherited descriptor
# after the isolated launcher has validated and acquired it. This fences
# concurrent database pairs without inventing an independent lock namespace.
if [[ "$EXPLICIT_CONTRACT" == "1" ]]; then
  DAY2_LOCK="/var/lock/omega-gcp-day2.lock"
  if [[ "${EUID}" -ne 0 ]]; then
    fail "explicit migration contract requires the root-owned day-2 lock" 13
  fi
  if [[ ! -e "/proc/$$/fd/9" ]] || \
     [[ "$(/usr/bin/readlink -f "/proc/$$/fd/9" 2>/dev/null || true)" != "$DAY2_LOCK" ]] || \
     [[ "$(/usr/bin/stat -Lc '%u:%g:%a:%h' "$DAY2_LOCK" 2>/dev/null || true)" != "0:0:600:1" ]]; then
    fail "day-2 lock descriptor or ownership contract differs" 13
  fi
  if ! /usr/bin/flock -w 30 9; then
    fail "timed out waiting for the exclusive day-2 lock" 13
  fi
fi

resolve_read_only_migration_container() {
  local service="$1"
  local expected_source="$2"
  local container_id
  if ! container_id="$("${COMPOSE[@]}" ps -q "$service")" || \
     [[ ! "$container_id" =~ ^[0-9a-f]{64}$ ]]; then
    fail "cannot resolve the exact running ${service} container" 15
  fi
  if ! docker inspect --format '{{json .Mounts}}' "$container_id" \
      | python3 "$GUARD" verify-read-only-mount \
          --expected-source "$expected_source" \
          --target /docker-entrypoint-initdb.d >/dev/null; then
    fail "${service} migration source is not the exact read-only release mount" 15
  fi
  if [[ "$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null || true)" != "true" ]]; then
    fail "${service} migration container is not running" 15
  fi
  printf '%s' "$container_id"
}

POSTGRES_CONTAINER_ID="$(
  resolve_read_only_migration_container postgres "${ROOT_DIR}/infra/init"
)"
POSTGRES_GOLD_CONTAINER_ID="$(
  resolve_read_only_migration_container postgres_gold "${ROOT_DIR}/infra/init_gold"
)"

if [[ -z "$COMPOSE_PROJECT_NAME_VALUE" ]]; then
  POSTGRES_PROJECT="$(docker inspect "$POSTGRES_CONTAINER_ID" --format '{{index .Config.Labels "com.docker.compose.project"}}')"
  POSTGRES_GOLD_PROJECT="$(docker inspect "$POSTGRES_GOLD_CONTAINER_ID" --format '{{index .Config.Labels "com.docker.compose.project"}}')"
  if [[ "$POSTGRES_PROJECT" != "$POSTGRES_GOLD_PROJECT" || \
        ! "$POSTGRES_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    fail "local fallback database Compose projects differ or are invalid" 15
  fi
  COMPOSE_PROJECT_NAME_VALUE="$POSTGRES_PROJECT"
fi

verify_database_target() {
  local service="$1"
  local container_id="$2"
  local expected_source="$3"
  local expected_image="$4"
  docker inspect "$container_id" \
    | python3 "$GUARD" verify-database-target \
        --container-id "$container_id" \
        --service "$service" \
        --compose-project "$COMPOSE_PROJECT_NAME_VALUE" \
        --expected-init-source "$expected_source" \
        --expected-image-reference "$expected_image" >/dev/null
}
if [[ "$EXPLICIT_CONTRACT" == "1" ]]; then
  POSTGRES_IMAGE_REFERENCE="pgvector/pgvector:pg15"
  POSTGRES_GOLD_IMAGE_REFERENCE="postgres:15"
  if [[ "$(docker inspect "$POSTGRES_CONTAINER_ID" --format '{{.Name}}')" != "/mode_postgres" || \
        "$(docker inspect "$POSTGRES_GOLD_CONTAINER_ID" --format '{{.Name}}')" != "/mode_postgres_gold" ]]; then
    fail "canonical PostgreSQL container names differ" 15
  fi
else
  POSTGRES_IMAGE_REFERENCE="$(docker inspect "$POSTGRES_CONTAINER_ID" --format '{{.Config.Image}}')"
  POSTGRES_GOLD_IMAGE_REFERENCE="$(docker inspect "$POSTGRES_GOLD_CONTAINER_ID" --format '{{.Config.Image}}')"
fi
verify_database_target postgres "$POSTGRES_CONTAINER_ID" "${ROOT_DIR}/infra/init" "$POSTGRES_IMAGE_REFERENCE"
verify_database_target postgres_gold "$POSTGRES_GOLD_CONTAINER_ID" "${ROOT_DIR}/infra/init_gold" "$POSTGRES_GOLD_IMAGE_REFERENCE"

verify_compose_container_binding() {
  local service="$1"
  local expected_id="$2"
  local current_id
  if ! current_id="$("${COMPOSE[@]}" ps -q "$service")" || \
     [[ "$current_id" != "$expected_id" ]]; then
    fail "${service} container changed while binding the migration pair" 15
  fi
}
verify_compose_container_binding postgres "$POSTGRES_CONTAINER_ID"
verify_compose_container_binding postgres_gold "$POSTGRES_GOLD_CONTAINER_ID"

verify_final_postgres_pid1() {
  local service="$1"
  local container_id="$2"
  local process_name
  if ! process_name="$(docker exec "$container_id" cat /proc/1/comm)" || \
     [[ "$process_name" != "postgres" ]]; then
    fail "${service} is still in a temporary or unexpected entrypoint" 15
  fi
}
verify_final_postgres_pid1 postgres "$POSTGRES_CONTAINER_ID"
verify_final_postgres_pid1 postgres_gold "$POSTGRES_GOLD_CONTAINER_ID"

# Pin every database read and write to the exact two inspected containers.
# A concurrent Compose recreate makes docker exec fail instead of silently
# migrating a replacement whose mount contract was never verified.
PSQL=(
  docker exec -i -e "PGAPPNAME=$MIGRATION_RUN_ID" "$POSTGRES_CONTAINER_ID"
  psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5432
  -U postgres -d modecissions
)
PSQL_GOLD=(
  docker exec -i -e "PGAPPNAME=$MIGRATION_RUN_ID" "$POSTGRES_GOLD_CONTAINER_ID"
  psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5433
  -U postgres -d modecissions_gold
)
BACKEND_CLEANUP_REQUIRED=1

SYSTEM_IDENTITY_SQL="SELECT current_database(), session_user, current_user,
                            system_identifier
                       FROM pg_control_system();"
POSTGRES_SYSTEM_IDENTIFIER="$(
  "${PSQL[@]}" -At -F '|' -c "$SYSTEM_IDENTITY_SQL" \
    | python3 "$GUARD" verify-database-system-identity \
        --database modecissions
)"
POSTGRES_GOLD_SYSTEM_IDENTIFIER="$(
  "${PSQL_GOLD[@]}" -At -F '|' -c "$SYSTEM_IDENTITY_SQL" \
    | python3 "$GUARD" verify-database-system-identity \
        --database modecissions_gold
)"
if [[ "$POSTGRES_SYSTEM_IDENTIFIER" == "$POSTGRES_GOLD_SYSTEM_IDENTIFIER" ]]; then
  fail "operational and Gold database system identifiers unexpectedly match" 15
fi

WORKDIR="$(mktemp -d /tmp/omega-migration-guard.XXXXXX)"
OPERATIONAL_BEFORE="${WORKDIR}/operational-before.jsonl"
GOLD_BEFORE="${WORKDIR}/gold-before.jsonl"
OPERATIONAL_AFTER="${WORKDIR}/operational-after.jsonl"
GOLD_AFTER="${WORKDIR}/gold-after.jsonl"
PLAN="${WORKDIR}/plan.json"
OPERATIONAL_SQL="${WORKDIR}/operational.sql"
GOLD_SQL="${WORKDIR}/gold.sql"
AUTHORITY_SQL="${WORKDIR}/ledger-authority.sql"

LEDGER_QUERY="SELECT json_build_object(
  'filename', filename,
  'checksum', to_jsonb(sm)->>'checksum',
  'source_ref', to_jsonb(sm)->>'checksum_source_ref',
  'manifest_sha256', to_jsonb(sm)->>'checksum_manifest_sha256',
  'evidence_kind', to_jsonb(sm)->>'checksum_evidence_kind',
  'guarded', NULLIF(to_jsonb(sm)->>'checksum_guarded_at', '') IS NOT NULL
)::text
FROM public.schema_migrations sm
ORDER BY filename;"

dump_operational_ledger() {
  "${PSQL[@]}" -At -c "$LEDGER_QUERY"
}

dump_gold_ledger() {
  "${PSQL_GOLD[@]}" -At -c "$LEDGER_QUERY"
}

# This is the release invariant: both databases are inspected and accepted
# before either database receives a durable write.
dump_operational_ledger > "$OPERATIONAL_BEFORE"
dump_gold_ledger > "$GOLD_BEFORE"
if [[ "$BOOTSTRAP_MODE" == "1" ]]; then
  python3 "$GUARD" preflight "${CONTRACT_ARGS[@]}" \
    --operational-ledger "$OPERATIONAL_BEFORE" \
    --gold-ledger "$GOLD_BEFORE" \
    --plan "$PLAN" \
    --allow-bootstrap-release-ledger
else
  python3 "$GUARD" preflight "${CONTRACT_ARGS[@]}" \
    --operational-ledger "$OPERATIONAL_BEFORE" \
    --gold-ledger "$GOLD_BEFORE" \
    --plan "$PLAN"
fi
advance_phase 10-preflight-passed.json preflight_passed none

# Render both transactions into private, read-only files before the first
# durable write.  The guard embeds the exact migration bytes it hashed, so
# psql never re-opens a mutable bind-mount path after verification.
python3 "$GUARD" render-sql --plan "$PLAN" --database gold > "$GOLD_SQL"
python3 "$GUARD" render-sql --plan "$PLAN" --database operational \
  > "$OPERATIONAL_SQL"
chmod 0400 "$GOLD_SQL" "$OPERATIONAL_SQL"
python3 "$SAFE_IO" fsync-file "$GOLD_SQL" "$OPERATIONAL_SQL"

# Gold has no pending schema delta in this release. Record its source-inferred
# expected-byte evidence first so the transaction that installs the
# operational 99zzt/99zzu/99zzv set is the last durable write; a retry accepts
# Gold-expected/operational-baseline safely.
advance_phase 15-gold-commit-intent.json gold_commit_in_progress indeterminate
"${PSQL_GOLD[@]}" -f - < "$GOLD_SQL"
advance_phase 20-gold-committed.json gold_committed_operational_pending gold_only
advance_phase 25-operational-commit-intent.json operational_commit_in_progress indeterminate
"${PSQL[@]}" -f - < "$OPERATIONAL_SQL"
advance_phase 30-operational-committed.json both_databases_committed_authority_pending gold_and_operational

verify_compose_container_binding postgres "$POSTGRES_CONTAINER_ID"
verify_compose_container_binding postgres_gold "$POSTGRES_GOLD_CONTAINER_ID"
verify_database_target postgres "$POSTGRES_CONTAINER_ID" "${ROOT_DIR}/infra/init" "$POSTGRES_IMAGE_REFERENCE"
verify_database_target postgres_gold "$POSTGRES_GOLD_CONTAINER_ID" "${ROOT_DIR}/infra/init_gold" "$POSTGRES_GOLD_IMAGE_REFERENCE"
dump_operational_ledger > "$OPERATIONAL_AFTER"
dump_gold_ledger > "$GOLD_AFTER"
python3 "$GUARD" render-authority-sql > "$AUTHORITY_SQL"
"${PSQL[@]}" -f - < "$AUTHORITY_SQL"
advance_phase 40-operational-authority-committed.json operational_authority_committed_gold_authority_pending gold_and_operational
"${PSQL_GOLD[@]}" -f - < "$AUTHORITY_SQL"
advance_phase 50-gold-authority-committed.json database_authority_committed_postflight_pending gold_operational_and_authority
python3 "$GUARD" postflight "${CONTRACT_ARGS[@]}" \
  --operational-ledger "$OPERATIONAL_AFTER" \
  --gold-ledger "$GOLD_AFTER" \
  --plan "$PLAN"
advance_phase 60-postflight-passed.json postflight_passed gold_operational_and_authority
if ! terminate_migration_backends; then
  fail "migration backends remain after successful completion" 25
fi
BACKEND_CLEANUP_REQUIRED=0

EXPECTED_PENDING_EVIDENCE="$(
  python3 "$GUARD" plan-field \
    --plan "$PLAN" \
    --field expected_pending_evidence
)"
if [[ "$EXPECTED_PENDING_EVIDENCE" == "baseline_expected" ]]; then
  echo "[migrate] done: operational=201 gold=12 baseline_expected=213 guarded_transaction=0"
else
  echo "[migrate] done: operational=201 gold=12 baseline_expected=210 guarded_transaction=3"
fi
