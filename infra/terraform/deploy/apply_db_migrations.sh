#!/bin/bash
# Apply infra/init migrations against an existing AWS Postgres volume.
#
# Docker's /docker-entrypoint-initdb.d only runs when the data directory is
# empty. This script is the upgrade path for long-lived EC2 volumes.
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

DEPLOY_DIR="/opt/modecissions/infra/terraform/deploy"
ROOT_DIR="/opt/modecissions"
cd "${DEPLOY_DIR}"

if [ ! -f .env ]; then
  echo "ERROR: .env no existe en ${DEPLOY_DIR}. Ejecuta /opt/modecissions/scripts/aws-entrypoint.sh."
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

PGOPTIONS_VALUE="-c app.omega_console_password=${OMEGA_CONSOLE_PASSWORD:-} -c app.omega_refinement_password=${OMEGA_REFINEMENT_PASSWORD:-} -c app.omega_vault_password=${OMEGA_VAULT_PASSWORD:-} -c app.omega_workspace_password=${OMEGA_WORKSPACE_PASSWORD:-} -c app.omega_mcp_infra_password=${OMEGA_MCP_INFRA_PASSWORD:-} -c app.omega_refinement_gold_password=${OMEGA_REFINEMENT_GOLD_PASSWORD:-} -c app.omega_cartridge_sap_hcm_password=${OMEGA_CARTRIDGE_SAP_HCM_PASSWORD:-} -c app.omega_cartridge_sap_s4_password=${OMEGA_CARTRIDGE_SAP_S4_PASSWORD:-} -c app.omega_cartridge_sap_sf_password=${OMEGA_CARTRIDGE_SAP_SF_PASSWORD:-} -c app.omega_airflow_dag_password=${OMEGA_AIRFLOW_DAG_PASSWORD:-} -c app.omega_airflow_meta_password=${OMEGA_AIRFLOW_META_PASSWORD:-} -c app.omega_superset_meta_password=${OMEGA_SUPERSET_META_PASSWORD:-} -c app.omega_cartridge_replicon_password=${OMEGA_CARTRIDGE_REPLICON_PASSWORD:-} -c app.omega_cartridge_hubspot_password=${OMEGA_CARTRIDGE_HUBSPOT_PASSWORD:-} -c app.omega_cartridge_banxico_password=${OMEGA_CARTRIDGE_BANXICO_PASSWORD:-} -c app.omega_cartridge_inegi_password=${OMEGA_CARTRIDGE_INEGI_PASSWORD:-} -c app.omega_cartridge_sec_edgar_password=${OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD:-}"
GOLD_PGOPTIONS_VALUE="-c app.omega_refinement_gold_password=${OMEGA_REFINEMENT_GOLD_PASSWORD:-} -c app.omega_gold_publisher_password=${OMEGA_GOLD_PUBLISHER_PASSWORD:-}"

PSQL=(docker compose -f docker-compose.aws.yml exec -T -e "PGOPTIONS=${PGOPTIONS_VALUE}" postgres psql -v ON_ERROR_STOP=1 -U postgres -d modecissions)
PSQL_GOLD=(docker compose -f docker-compose.aws.yml exec -T -e "PGOPTIONS=${GOLD_PGOPTIONS_VALUE}" postgres_gold psql -v ON_ERROR_STOP=1 -U postgres -d modecissions_gold -p 5433)

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

for sql in "${ROOT_DIR}"/infra/init_gold/[0-9][0-9]*_*.sql; do
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
