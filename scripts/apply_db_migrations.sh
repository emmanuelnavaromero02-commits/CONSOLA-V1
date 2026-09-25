#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATION_COMPOSE_PATH="${ROOT_DIR}/infra/docker-compose.yml"
ENV_FILE="${ROOT_DIR}/infra/.env"

if [[ ${COMPOSE_FILE+x} == x && -z ${COMPOSE_FILE} ]]; then
  unset COMPOSE_FILE
fi

MIGRATION_ADVISORY_LOCK_KEY="${OMEGA_MIGRATION_ADVISORY_LOCK_KEY:-145207}"

if command -v flock >/dev/null 2>&1; then
  LOCK_FILE="${OMEGA_MIGRATION_LOCK_FILE:-${TMPDIR:-/tmp}/omega-apply-db-migrations.lock}"
  exec 9>"${LOCK_FILE}"
  if ! flock -n 9; then
    echo "[migrate] another migration run holds ${LOCK_FILE}; refusing to run concurrently" >&2
    exit 1
  fi
fi

load_passive_dotenv() {
  local input_path="$1" output_path key value
  output_path="$(mktemp "${TMPDIR:-/tmp}/omega-release-dotenv.XXXXXX")"
  chmod 0600 "${output_path}"
  if ! python3 -I "${ROOT_DIR}/scripts/load_release_dotenv.py" \
      --input "${input_path}" --output "${output_path}"; then
    rm -f -- "${output_path}"
    return 2
  fi
  while IFS= read -r -d '' key && IFS= read -r -d '' value; do
    if ! export "${key}=${value}"; then
      rm -f -- "${output_path}"
      echo "[migrate] passive dotenv import failed for ${key}" >&2
      return 2
    fi
  done < "${output_path}"
  rm -f -- "${output_path}"
}

if [[ -f "${ENV_FILE}" ]]; then
  load_passive_dotenv "${ENV_FILE}"
fi

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

PGOPTIONS_VALUE="-c app.omega_console_password=${OMEGA_CONSOLE_PASSWORD:-} -c app.omega_outcome_binder_password=${OMEGA_OUTCOME_BINDER_PASSWORD:-} -c app.omega_refinement_password=${OMEGA_REFINEMENT_PASSWORD:-} -c app.omega_vault_password=${OMEGA_VAULT_PASSWORD:-} -c app.omega_workspace_password=${OMEGA_WORKSPACE_PASSWORD:-} -c app.omega_mcp_infra_password=${OMEGA_MCP_INFRA_PASSWORD:-} -c app.omega_refinement_gold_password=${OMEGA_REFINEMENT_GOLD_PASSWORD:-} -c app.omega_cartridge_sap_hcm_password=${OMEGA_CARTRIDGE_SAP_HCM_PASSWORD:-} -c app.omega_cartridge_sap_s4_password=${OMEGA_CARTRIDGE_SAP_S4_PASSWORD:-} -c app.omega_cartridge_sap_sf_password=${OMEGA_CARTRIDGE_SAP_SF_PASSWORD:-} -c app.omega_cartridge_sap_b1_password=${OMEGA_CARTRIDGE_SAP_B1_PASSWORD:-} -c app.omega_airflow_dag_password=${OMEGA_AIRFLOW_DAG_PASSWORD:-} -c app.omega_airflow_meta_password=${OMEGA_AIRFLOW_META_PASSWORD:-} -c app.omega_superset_meta_password=${OMEGA_SUPERSET_META_PASSWORD:-} -c app.omega_cartridge_replicon_password=${OMEGA_CARTRIDGE_REPLICON_PASSWORD:-} -c app.omega_cartridge_salesforce_password=${OMEGA_CARTRIDGE_SALESFORCE_PASSWORD:-} -c app.omega_cartridge_hubspot_password=${OMEGA_CARTRIDGE_HUBSPOT_PASSWORD:-} -c app.omega_cartridge_banxico_password=${OMEGA_CARTRIDGE_BANXICO_PASSWORD:-} -c app.omega_cartridge_inegi_password=${OMEGA_CARTRIDGE_INEGI_PASSWORD:-} -c app.omega_cartridge_sec_edgar_password=${OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD:-}"
GOLD_PGOPTIONS_VALUE="-c app.omega_refinement_gold_password=${OMEGA_REFINEMENT_GOLD_PASSWORD:-} -c app.omega_gold_publisher_password=${OMEGA_GOLD_PUBLISHER_PASSWORD:-} -c app.omega_gold_verifier_password=${OMEGA_GOLD_VERIFIER_PASSWORD:-}"
PSQL=(docker compose -f "${MIGRATION_COMPOSE_PATH}" exec -T -e "PGOPTIONS=${PGOPTIONS_VALUE}" postgres psql -v ON_ERROR_STOP=1 -U postgres -d modecissions)
PSQL_GOLD=(docker compose -f "${MIGRATION_COMPOSE_PATH}" exec -T -e "PGOPTIONS=${GOLD_PGOPTIONS_VALUE}" postgres_gold psql -v ON_ERROR_STOP=1 -U postgres -d modecissions_gold -p 5433)

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
"${PSQL[@]}" -c "ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS checksum TEXT;"

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
ON CONFLICT (filename) DO UPDATE
SET checksum = COALESCE(schema_migrations.checksum, EXCLUDED.checksum);
COMMIT;
SQL
done

"${PSQL_GOLD[@]}" -c "CREATE TABLE IF NOT EXISTS schema_migrations (id BIGSERIAL PRIMARY KEY, filename TEXT NOT NULL UNIQUE, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), checksum TEXT);"
"${PSQL_GOLD[@]}" -c "ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS checksum TEXT;"

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
ON CONFLICT (filename) DO UPDATE
SET checksum = COALESCE(schema_migrations.checksum, EXCLUDED.checksum);
COMMIT;
SQL
done

echo "[migrate] done"
