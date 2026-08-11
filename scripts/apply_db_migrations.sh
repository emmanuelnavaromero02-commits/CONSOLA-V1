#!/usr/bin/env bash
# Apply the exact audited 1.45.207 migration delta to existing operational and
# Gold databases.  Both ledgers are preflighted before the first durable write;
# filename-only migration skipping is deliberately forbidden.
set -Eeuo pipefail
set +x
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${OMEGA_MIGRATION_COMPOSE_FILE:-${ROOT_DIR}/infra/docker-compose.yml}"
ENV_FILE="${OMEGA_MIGRATION_ENV_FILE:-${ROOT_DIR}/infra/.env}"
COMPOSE_PROJECT_NAME_VALUE="${OMEGA_MIGRATION_COMPOSE_PROJECT_NAME:-}"
GUARD="${ROOT_DIR}/scripts/migration_guard.py"
SAFE_IO="${OMEGA_GCP_SAFE_IO:-${ROOT_DIR}/scripts/gcp/safe_io.py}"
BASELINE_REF="6b12883c5b5ea0537120279ccbee4947137998a2"
BASELINE_MANIFEST_DEFAULT="${ROOT_DIR}/infra/migrations/manifests/gcp-live-${BASELINE_REF}.json"
RELEASE_MANIFEST_DEFAULT="${ROOT_DIR}/infra/migrations/manifests/v1.45.207-beta.json"
BASELINE_MANIFEST_SHA256_DEFAULT="b6cb33c9b1a0f93e13fe2eb68f2e8fff1fdeedb2979bbfb22840a2a35d2e4a18"
RELEASE_MANIFEST_SHA256_DEFAULT="fbc2db83f82eecf9733a60a23fae7c9982d0f9aba52e474f41ba909acbaedc93"
WORKDIR=""

cleanup() {
  local rc="$1"
  trap - EXIT
  if [[ -n "${WORKDIR}" && "${WORKDIR}" == /tmp/omega-migration-guard.* ]]; then
    rm -rf -- "${WORKDIR}"
  fi
  exit "$rc"
}
trap 'cleanup "$?"' EXIT

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

# The dotenv file is data for Compose, never executable shell.  Validate its
# ownership/mode and strict assignment grammar before giving it to Compose;
# release control-plane inputs are forbidden in that data file.
export COMPOSE_DISABLE_ENV_FILE=1
COMPOSE=(docker compose)
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
fi
COMPOSE+=(-f "${COMPOSE_FILE}")

if [[ "${OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT:-0}" == "1" ]]; then
  required_contract=(
    OMEGA_MIGRATION_OLD_REF
    OMEGA_MIGRATION_CANDIDATE_REF
    OMEGA_MIGRATION_RELEASE_VERSION
    OMEGA_MIGRATION_BASELINE_MANIFEST
    OMEGA_MIGRATION_BASELINE_MANIFEST_SHA256
    OMEGA_MIGRATION_RELEASE_MANIFEST
    OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256
  )
  for variable in "${required_contract[@]}"; do
    if [[ -z "${!variable:-}" ]]; then
      fail "explicit release contract is missing ${variable}" 13
    fi
  done
fi

BOOTSTRAP_MODE="${OMEGA_MIGRATION_BOOTSTRAP_MODE:-0}"
if [[ "$BOOTSTRAP_MODE" != "0" && "$BOOTSTRAP_MODE" != "1" ]]; then
  fail "OMEGA_MIGRATION_BOOTSTRAP_MODE must be 0 or 1" 13
fi
if [[ "${OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT:-0}" == "1" && "$BOOTSTRAP_MODE" == "1" ]]; then
  fail "bootstrap source-inferred ledger evidence is forbidden for an explicit day-2 contract" 13
fi
if [[ "$BOOTSTRAP_MODE" == "1" ]]; then
  if [[ "${OMEGA_MIGRATION_ALLOW_BOOTSTRAP_LEDGER:-0}" != "1" ]]; then
    fail "bootstrap source-inferred ledger evidence requires explicit local opt-in" 13
  fi
  MIGRATION_ENVIRONMENT="${OMEGA_MIGRATION_ENVIRONMENT:-${APP_ENV:-}}"
  if [[ "$MIGRATION_ENVIRONMENT" != "local" && \
        "$MIGRATION_ENVIRONMENT" != "development" && \
        "$MIGRATION_ENVIRONMENT" != "test" ]]; then
    fail "bootstrap source-inferred ledger evidence is restricted to local/development/test" 13
  fi
fi
OLD_REF="${OMEGA_MIGRATION_OLD_REF:-${BASELINE_REF}}"
CANDIDATE_REF="${OMEGA_MIGRATION_CANDIDATE_REF:-}"
if [[ -z "$CANDIDATE_REF" ]] && git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  CANDIDATE_REF="$(git -C "$ROOT_DIR" rev-parse HEAD)"
fi
if [[ -z "$CANDIDATE_REF" ]]; then
  fail "OMEGA_MIGRATION_CANDIDATE_REF is required outside a Git worktree" 14
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

python3 "$GUARD" verify-manifests "${CONTRACT_ARGS[@]}"

PSQL=(
  "${COMPOSE[@]}" exec -T
  postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d modecissions
)
PSQL_GOLD=(
  "${COMPOSE[@]}" exec -T
  postgres_gold psql -X -v ON_ERROR_STOP=1 -U postgres
  -d modecissions_gold -p 5433
)

PENDING_CONTAINER_FILES=(
  "99zzt_analytic_app_dataset_grants.sql:99f87377bf875474ae0be08ce69dac28a84a4951fab7f49655ac229cfd4e975f"
  "99zzu_analytic_app_manifest_registry.sql:adad0c9f710ce80aa5ec46dad16b919233f1f0f20ad8fd6d9af7984197577123"
)
for binding in "${PENDING_CONTAINER_FILES[@]}"; do
  filename="${binding%%:*}"
  expected="${binding#*:}"
  container_path="/docker-entrypoint-initdb.d/${filename}"
  read -r actual actual_path extra < <(
    "${COMPOSE[@]}" exec -T postgres sha256sum "$container_path"
  )
  if [[ -n "${extra:-}" || "$actual" != "$expected" || "$actual_path" != "$container_path" ]]; then
    fail "container migration bytes do not match the release lock: ${filename}" 15
  fi
done

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

# Gold has no pending schema delta in this release. Record its source-inferred
# expected-byte evidence first so the
# transaction that installs the operational 99zzt/99zzu pair is the last
# durable write; a retry accepts Gold-expected/operational-baseline safely.
python3 "$GUARD" render-sql --plan "$PLAN" --database gold > "$GOLD_SQL"
"${PSQL_GOLD[@]}" -f - < "$GOLD_SQL"

python3 "$GUARD" render-sql --plan "$PLAN" --database operational \
  > "$OPERATIONAL_SQL"
"${PSQL[@]}" -f - < "$OPERATIONAL_SQL"

dump_operational_ledger > "$OPERATIONAL_AFTER"
dump_gold_ledger > "$GOLD_AFTER"
python3 "$GUARD" render-authority-sql > "$AUTHORITY_SQL"
"${PSQL[@]}" -f - < "$AUTHORITY_SQL"
"${PSQL_GOLD[@]}" -f - < "$AUTHORITY_SQL"
python3 "$GUARD" postflight "${CONTRACT_ARGS[@]}" \
  --operational-ledger "$OPERATIONAL_AFTER" \
  --gold-ledger "$GOLD_AFTER" \
  --plan "$PLAN"

EXPECTED_PENDING_EVIDENCE="$(
  python3 "$GUARD" plan-field \
    --plan "$PLAN" \
    --field expected_pending_evidence
)"
if [[ "$EXPECTED_PENDING_EVIDENCE" == "baseline_expected" ]]; then
  echo "[migrate] done: operational=200 gold=12 baseline_expected=212 guarded_transaction=0"
else
  echo "[migrate] done: operational=200 gold=12 baseline_expected=210 guarded_transaction=2"
fi
