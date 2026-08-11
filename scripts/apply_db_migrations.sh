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

if [[ -n "${COMPOSE_PROJECT_NAME_VALUE}" ]]; then
  if [[ ! "${COMPOSE_PROJECT_NAME_VALUE}" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    fail "invalid OMEGA_MIGRATION_COMPOSE_PROJECT_NAME" 12
  fi
  COMPOSE=(
    docker compose --project-name "${COMPOSE_PROJECT_NAME_VALUE}"
    -f "${COMPOSE_FILE}"
  )
else
  COMPOSE=(docker compose -f "${COMPOSE_FILE}")
fi

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

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
EXPECTED_PENDING_EVIDENCE="guarded_transaction"
if [[ "$BOOTSTRAP_MODE" == "1" ]]; then
  EXPECTED_PENDING_EVIDENCE="baseline_expected"
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

PGOPTIONS_VALUE="-c app.omega_console_password=${OMEGA_CONSOLE_PASSWORD:-} -c app.omega_outcome_binder_password=${OMEGA_OUTCOME_BINDER_PASSWORD:-} -c app.omega_refinement_password=${OMEGA_REFINEMENT_PASSWORD:-} -c app.omega_vault_password=${OMEGA_VAULT_PASSWORD:-} -c app.omega_workspace_password=${OMEGA_WORKSPACE_PASSWORD:-} -c app.omega_mcp_infra_password=${OMEGA_MCP_INFRA_PASSWORD:-} -c app.omega_refinement_gold_password=${OMEGA_REFINEMENT_GOLD_PASSWORD:-} -c app.omega_cartridge_sap_hcm_password=${OMEGA_CARTRIDGE_SAP_HCM_PASSWORD:-} -c app.omega_cartridge_sap_s4_password=${OMEGA_CARTRIDGE_SAP_S4_PASSWORD:-} -c app.omega_cartridge_sap_sf_password=${OMEGA_CARTRIDGE_SAP_SF_PASSWORD:-} -c app.omega_airflow_dag_password=${OMEGA_AIRFLOW_DAG_PASSWORD:-} -c app.omega_airflow_meta_password=${OMEGA_AIRFLOW_META_PASSWORD:-} -c app.omega_superset_meta_password=${OMEGA_SUPERSET_META_PASSWORD:-} -c app.omega_cartridge_replicon_password=${OMEGA_CARTRIDGE_REPLICON_PASSWORD:-} -c app.omega_cartridge_salesforce_password=${OMEGA_CARTRIDGE_SALESFORCE_PASSWORD:-} -c app.omega_cartridge_hubspot_password=${OMEGA_CARTRIDGE_HUBSPOT_PASSWORD:-} -c app.omega_cartridge_banxico_password=${OMEGA_CARTRIDGE_BANXICO_PASSWORD:-} -c app.omega_cartridge_inegi_password=${OMEGA_CARTRIDGE_INEGI_PASSWORD:-} -c app.omega_cartridge_sec_edgar_password=${OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD:-}"
GOLD_PGOPTIONS_VALUE="-c app.omega_refinement_gold_password=${OMEGA_REFINEMENT_GOLD_PASSWORD:-} -c app.omega_gold_publisher_password=${OMEGA_GOLD_PUBLISHER_PASSWORD:-} -c app.omega_gold_verifier_password=${OMEGA_GOLD_VERIFIER_PASSWORD:-}"
PSQL=(
  "${COMPOSE[@]}" exec -T -e "PGOPTIONS=${PGOPTIONS_VALUE}"
  postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d modecissions
)
PSQL_GOLD=(
  "${COMPOSE[@]}" exec -T -e "PGOPTIONS=${GOLD_PGOPTIONS_VALUE}"
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

LEDGER_QUERY="SELECT json_build_object(
  'filename', filename,
  'checksum', checksum,
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
python3 "$GUARD" postflight "${CONTRACT_ARGS[@]}" \
  --operational-ledger "$OPERATIONAL_AFTER" \
  --gold-ledger "$GOLD_AFTER" \
  --expected-pending-evidence "$EXPECTED_PENDING_EVIDENCE"

if [[ "$BOOTSTRAP_MODE" == "1" ]]; then
  echo "[migrate] done: operational=200 gold=12 baseline_expected=212 guarded_transaction=0"
else
  echo "[migrate] done: operational=200 gold=12 baseline_expected=210 guarded_transaction=2"
fi
