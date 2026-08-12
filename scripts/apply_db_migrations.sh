#!/usr/bin/env bash
# Apply pending infra/init/*.sql and infra/init_gold/*.sql migrations to existing
# local Postgres volumes.
#
# Docker entrypoint init scripts only run on a fresh data directory. This target
# covers the upgrade path for already-created local stacks by replaying
# forward-only SQL files not yet present in schema_migrations.
#
# Checkpoint 5.5 migration-release contract (kept small and auditable):
#   (a) forward-only  — a file already in schema_migrations is never re-run.
#   (b) ledger + drift — the sha256 of each applied file is recorded; on later
#                        runs a recorded checksum that no longer matches disk is
#                        a hard, fail-closed error (a historical migration was
#                        edited/regressed instead of a new one being appended).
#   (c) idempotency   — re-running applies nothing already recorded.
#   (d) atomic retry  — each file and its ledger row commit in ONE transaction
#                        with ON_ERROR_STOP, statement/lock timeouts, and a
#                        transaction-scoped advisory lock so a second concurrent
#                        applier cannot interleave (split-brain prevention).
# A host-level flock adds single-writer protection across processes on the host.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/infra/docker-compose.yml"
ENV_FILE="${ROOT_DIR}/infra/.env"

# Advisory lock key: stable, release-scoped. Held only for the duration of each
# per-file transaction, so it serialises concurrent appliers at the database.
MIGRATION_ADVISORY_LOCK_KEY="${OMEGA_MIGRATION_ADVISORY_LOCK_KEY:-145207}"

# Single-writer across processes on this host. flock is Linux/util-linux; on a
# host without it the database advisory lock is still the real guard, so this is
# best-effort and never blocks a machine that lacks flock.
if command -v flock >/dev/null 2>&1; then
  LOCK_FILE="${OMEGA_MIGRATION_LOCK_FILE:-${TMPDIR:-/tmp}/omega-apply-db-migrations.lock}"
  exec 9>"${LOCK_FILE}"
  if ! flock -n 9; then
    echo "[migrate] another migration run holds ${LOCK_FILE}; refusing to run concurrently" >&2
    exit 1
  fi
fi

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

# Portable sha256 of a file's bytes (Linux coreutils or BSD/macOS shasum).
sha256_of() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -- "${path}" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -- "${path}" | awk '{print $1}'
  else
    echo "[migrate] no sha256 tool (sha256sum/shasum) available for drift detection" >&2
    return 3
  fi
}

PGOPTIONS_VALUE="-c app.omega_console_password=${OMEGA_CONSOLE_PASSWORD:-} -c app.omega_outcome_binder_password=${OMEGA_OUTCOME_BINDER_PASSWORD:-} -c app.omega_refinement_password=${OMEGA_REFINEMENT_PASSWORD:-} -c app.omega_vault_password=${OMEGA_VAULT_PASSWORD:-} -c app.omega_workspace_password=${OMEGA_WORKSPACE_PASSWORD:-} -c app.omega_mcp_infra_password=${OMEGA_MCP_INFRA_PASSWORD:-} -c app.omega_refinement_gold_password=${OMEGA_REFINEMENT_GOLD_PASSWORD:-} -c app.omega_cartridge_sap_hcm_password=${OMEGA_CARTRIDGE_SAP_HCM_PASSWORD:-} -c app.omega_cartridge_sap_s4_password=${OMEGA_CARTRIDGE_SAP_S4_PASSWORD:-} -c app.omega_cartridge_sap_sf_password=${OMEGA_CARTRIDGE_SAP_SF_PASSWORD:-} -c app.omega_airflow_dag_password=${OMEGA_AIRFLOW_DAG_PASSWORD:-} -c app.omega_airflow_meta_password=${OMEGA_AIRFLOW_META_PASSWORD:-} -c app.omega_superset_meta_password=${OMEGA_SUPERSET_META_PASSWORD:-} -c app.omega_cartridge_replicon_password=${OMEGA_CARTRIDGE_REPLICON_PASSWORD:-} -c app.omega_cartridge_salesforce_password=${OMEGA_CARTRIDGE_SALESFORCE_PASSWORD:-} -c app.omega_cartridge_hubspot_password=${OMEGA_CARTRIDGE_HUBSPOT_PASSWORD:-} -c app.omega_cartridge_banxico_password=${OMEGA_CARTRIDGE_BANXICO_PASSWORD:-} -c app.omega_cartridge_inegi_password=${OMEGA_CARTRIDGE_INEGI_PASSWORD:-} -c app.omega_cartridge_sec_edgar_password=${OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD:-}"
GOLD_PGOPTIONS_VALUE="-c app.omega_refinement_gold_password=${OMEGA_REFINEMENT_GOLD_PASSWORD:-} -c app.omega_gold_publisher_password=${OMEGA_GOLD_PUBLISHER_PASSWORD:-} -c app.omega_gold_verifier_password=${OMEGA_GOLD_VERIFIER_PASSWORD:-}"
PSQL=(docker compose -f "${COMPOSE_FILE}" exec -T -e "PGOPTIONS=${PGOPTIONS_VALUE}" postgres psql -v ON_ERROR_STOP=1 -U postgres -d modecissions)
PSQL_GOLD=(docker compose -f "${COMPOSE_FILE}" exec -T -e "PGOPTIONS=${GOLD_PGOPTIONS_VALUE}" postgres_gold psql -v ON_ERROR_STOP=1 -U postgres -d modecissions_gold -p 5433)

# Verify a migration already in the ledger has not drifted on disk, and backfill
# a legacy row that predates checksum tracking. Fails closed on real drift.
# Args: <db-kind: main|gold> <ledger-filename> <disk-path>
# (No bash namerefs so this stays portable to the bash 3.2 shipped on macOS.)
assert_no_drift_and_backfill() {
  local db_kind="$1" ledger_filename="$2" disk_path="$3"
  local recorded disk
  case "${db_kind}" in
    main) recorded="$("${PSQL[@]}" -At -c "SELECT COALESCE(checksum, '') FROM schema_migrations WHERE filename = '${ledger_filename}' LIMIT 1;")" ;;
    gold) recorded="$("${PSQL_GOLD[@]}" -At -c "SELECT COALESCE(checksum, '') FROM schema_migrations WHERE filename = '${ledger_filename}' LIMIT 1;")" ;;
    *) echo "[migrate] internal error: unknown db kind ${db_kind}" >&2; exit 2 ;;
  esac
  disk="$(sha256_of "${disk_path}")"
  if [[ -n "${recorded}" && "${recorded}" != "${disk}" ]]; then
    echo "[migrate] DRIFT: ${ledger_filename} was already applied with checksum ${recorded} but the file on disk now hashes to ${disk}." >&2
    echo "[migrate] Historical migrations are forward-only: append a new migration instead of editing an applied one." >&2
    exit 1
  fi
  if [[ -z "${recorded}" ]]; then
    case "${db_kind}" in
      main) "${PSQL[@]}" -c "UPDATE schema_migrations SET checksum = '${disk}' WHERE filename = '${ledger_filename}';" >/dev/null ;;
      gold) "${PSQL_GOLD[@]}" -c "UPDATE schema_migrations SET checksum = '${disk}' WHERE filename = '${ledger_filename}';" >/dev/null ;;
    esac
  fi
}

"${PSQL[@]}" -c "CREATE TABLE IF NOT EXISTS schema_migrations (id BIGSERIAL PRIMARY KEY, filename TEXT NOT NULL UNIQUE, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), checksum TEXT);"

for sql in "${ROOT_DIR}"/infra/init/[0-9][0-9]*_*.sql; do
  filename="$(basename "${sql}")"
  if [[ "$("${PSQL[@]}" -At -c "SELECT 1 FROM schema_migrations WHERE filename = '${filename}' LIMIT 1;")" == "1" ]]; then
    assert_no_drift_and_backfill main "${filename}" "${sql}"
    echo "[migrate] skip ${filename}"
    continue
  fi

  checksum="$(sha256_of "${sql}")"
  echo "[migrate] apply ${filename}"
  "${PSQL[@]}" -v filename="${filename}" -v checksum="${checksum}" <<SQL
BEGIN;
SET LOCAL lock_timeout = '15s';
SET LOCAL statement_timeout = '600s';
SET LOCAL idle_in_transaction_session_timeout = '120s';
SELECT pg_advisory_xact_lock(${MIGRATION_ADVISORY_LOCK_KEY});
\\i /docker-entrypoint-initdb.d/${filename}
INSERT INTO schema_migrations (filename, checksum, applied_at)
VALUES (:'filename', :'checksum', NOW())
ON CONFLICT (filename) DO NOTHING;
COMMIT;
SQL
done

"${PSQL_GOLD[@]}" -c "CREATE TABLE IF NOT EXISTS schema_migrations (id BIGSERIAL PRIMARY KEY, filename TEXT NOT NULL UNIQUE, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), checksum TEXT);"

for sql in "${ROOT_DIR}"/infra/init_gold/[0-9][0-9]_*.sql; do
  filename="gold/$(basename "${sql}")"
  if [[ "$("${PSQL_GOLD[@]}" -At -c "SELECT 1 FROM schema_migrations WHERE filename = '${filename}' LIMIT 1;")" == "1" ]]; then
    assert_no_drift_and_backfill gold "${filename}" "${sql}"
    echo "[migrate:gold] skip ${filename}"
    continue
  fi

  checksum="$(sha256_of "${sql}")"
  echo "[migrate:gold] apply ${filename}"
  "${PSQL_GOLD[@]}" -v filename="${filename}" -v checksum="${checksum}" <<SQL
BEGIN;
SET LOCAL lock_timeout = '15s';
SET LOCAL statement_timeout = '600s';
SET LOCAL idle_in_transaction_session_timeout = '120s';
SELECT pg_advisory_xact_lock(${MIGRATION_ADVISORY_LOCK_KEY});
\\i /docker-entrypoint-initdb.d/$(basename "${sql}")
INSERT INTO schema_migrations (filename, checksum, applied_at)
VALUES (:'filename', :'checksum', NOW())
ON CONFLICT (filename) DO NOTHING;
COMMIT;
SQL
done

echo "[migrate] done"
