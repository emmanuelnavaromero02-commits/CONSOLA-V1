#!/usr/bin/env bash
# Apply pending infra/init/*.sql and infra/init_gold/*.sql migrations to existing
# local Postgres volumes.
#
# Docker entrypoint init scripts only run on a fresh data directory. This target
# covers the upgrade path for already-created local stacks by replaying
# idempotent SQL files not yet present in schema_migrations.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/infra/docker-compose.yml"
ENV_FILE="${ROOT_DIR}/infra/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

PGOPTIONS_VALUE="-c app.omega_console_password=${OMEGA_CONSOLE_PASSWORD:-} -c app.omega_refinement_password=${OMEGA_REFINEMENT_PASSWORD:-} -c app.omega_vault_password=${OMEGA_VAULT_PASSWORD:-} -c app.omega_workspace_password=${OMEGA_WORKSPACE_PASSWORD:-} -c app.omega_mcp_infra_password=${OMEGA_MCP_INFRA_PASSWORD:-} -c app.omega_refinement_gold_password=${OMEGA_REFINEMENT_GOLD_PASSWORD:-} -c app.omega_cartridge_sap_hcm_password=${OMEGA_CARTRIDGE_SAP_HCM_PASSWORD:-} -c app.omega_cartridge_sap_s4_password=${OMEGA_CARTRIDGE_SAP_S4_PASSWORD:-} -c app.omega_cartridge_sap_sf_password=${OMEGA_CARTRIDGE_SAP_SF_PASSWORD:-} -c app.omega_airflow_dag_password=${OMEGA_AIRFLOW_DAG_PASSWORD:-} -c app.omega_airflow_meta_password=${OMEGA_AIRFLOW_META_PASSWORD:-} -c app.omega_superset_meta_password=${OMEGA_SUPERSET_META_PASSWORD:-} -c app.omega_cartridge_replicon_password=${OMEGA_CARTRIDGE_REPLICON_PASSWORD:-} -c app.omega_cartridge_salesforce_password=${OMEGA_CARTRIDGE_SALESFORCE_PASSWORD:-} -c app.omega_cartridge_hubspot_password=${OMEGA_CARTRIDGE_HUBSPOT_PASSWORD:-} -c app.omega_cartridge_banxico_password=${OMEGA_CARTRIDGE_BANXICO_PASSWORD:-} -c app.omega_cartridge_inegi_password=${OMEGA_CARTRIDGE_INEGI_PASSWORD:-} -c app.omega_cartridge_sec_edgar_password=${OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD:-}"
GOLD_PGOPTIONS_VALUE="-c app.omega_refinement_gold_password=${OMEGA_REFINEMENT_GOLD_PASSWORD:-} -c app.omega_gold_publisher_password=${OMEGA_GOLD_PUBLISHER_PASSWORD:-}"
PSQL=(docker compose -f "${COMPOSE_FILE}" exec -T -e "PGOPTIONS=${PGOPTIONS_VALUE}" postgres psql -v ON_ERROR_STOP=1 -U postgres -d modecissions)
PSQL_GOLD=(docker compose -f "${COMPOSE_FILE}" exec -T -e "PGOPTIONS=${GOLD_PGOPTIONS_VALUE}" postgres_gold psql -v ON_ERROR_STOP=1 -U postgres -d modecissions_gold -p 5433)

"${PSQL[@]}" -c "CREATE TABLE IF NOT EXISTS schema_migrations (id BIGSERIAL PRIMARY KEY, filename TEXT NOT NULL UNIQUE, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), checksum TEXT);"

for sql in "${ROOT_DIR}"/infra/init/[0-9][0-9]*_*.sql; do
  filename="$(basename "${sql}")"
  if [[ "$("${PSQL[@]}" -At -c "SELECT 1 FROM schema_migrations WHERE filename = '${filename}' LIMIT 1;")" == "1" ]]; then
    echo "[migrate] skip ${filename}"
    continue
  fi

  echo "[migrate] apply ${filename}"
  "${PSQL[@]}" -v filename="${filename}" <<SQL
BEGIN;
\\i /docker-entrypoint-initdb.d/${filename}
INSERT INTO schema_migrations (filename, applied_at)
VALUES (:'filename', NOW())
ON CONFLICT (filename) DO NOTHING;
COMMIT;
SQL
done

"${PSQL_GOLD[@]}" -c "CREATE TABLE IF NOT EXISTS schema_migrations (id BIGSERIAL PRIMARY KEY, filename TEXT NOT NULL UNIQUE, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), checksum TEXT);"

for sql in "${ROOT_DIR}"/infra/init_gold/[0-9][0-9]_*.sql; do
  filename="gold/$(basename "${sql}")"
  if [[ "$("${PSQL_GOLD[@]}" -At -c "SELECT 1 FROM schema_migrations WHERE filename = '${filename}' LIMIT 1;")" == "1" ]]; then
    echo "[migrate:gold] skip ${filename}"
    continue
  fi

  echo "[migrate:gold] apply ${filename}"
  "${PSQL_GOLD[@]}" -v filename="${filename}" <<SQL
BEGIN;
\\i /docker-entrypoint-initdb.d/$(basename "${sql}")
INSERT INTO schema_migrations (filename, applied_at)
VALUES (:'filename', NOW())
ON CONFLICT (filename) DO NOTHING;
COMMIT;
SQL
done

echo "[migrate] done"
