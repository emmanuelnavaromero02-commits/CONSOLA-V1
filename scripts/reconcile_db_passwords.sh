#!/usr/bin/env bash
# Reconcile existing local Postgres login roles with infra/.env.
#
# Docker init SQL only runs on a fresh volume. When infra/.env is rotated or
# regenerated later, already-created roles keep their old passwords and services
# fail health checks with "password authentication failed". This script performs
# the safe local rotation path without printing secret values.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/infra/docker-compose.yml"
ENV_FILE="${ROOT_DIR}/infra/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[reconcile-db-passwords] missing ${ENV_FILE}; run infra/bootstrap.sh first" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

REQUIRED=(
  POSTGRES_PASSWORD
  OMEGA_CONSOLE_PASSWORD
  OMEGA_OUTCOME_BINDER_PASSWORD
  OMEGA_REFINEMENT_PASSWORD
  OMEGA_VAULT_PASSWORD
  OMEGA_WORKSPACE_PASSWORD
  OMEGA_MCP_INFRA_PASSWORD
  OMEGA_REFINEMENT_GOLD_PASSWORD
  OMEGA_GOLD_PUBLISHER_PASSWORD
  OMEGA_GOLD_VERIFIER_PASSWORD
  OMEGA_CARTRIDGE_SAP_HCM_PASSWORD
  OMEGA_CARTRIDGE_SAP_S4_PASSWORD
  OMEGA_CARTRIDGE_SAP_SF_PASSWORD
  OMEGA_AIRFLOW_DAG_PASSWORD
  OMEGA_AIRFLOW_META_PASSWORD
  OMEGA_SUPERSET_META_PASSWORD
  OMEGA_CARTRIDGE_REPLICON_PASSWORD
  OMEGA_CARTRIDGE_SALESFORCE_PASSWORD
  OMEGA_CARTRIDGE_HUBSPOT_PASSWORD
  OMEGA_CARTRIDGE_BANXICO_PASSWORD
  OMEGA_CARTRIDGE_INEGI_PASSWORD
  OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD
)

missing=()
for key in "${REQUIRED[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    missing+=("${key}")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "[reconcile-db-passwords] missing required env vars: ${missing[*]}" >&2
  echo "[reconcile-db-passwords] run: bash infra/bootstrap-keys.sh infra/.env" >&2
  exit 2
fi

PGOPTIONS_VALUE="-c app.omega_console_password=${OMEGA_CONSOLE_PASSWORD} -c app.omega_outcome_binder_password=${OMEGA_OUTCOME_BINDER_PASSWORD} -c app.omega_refinement_password=${OMEGA_REFINEMENT_PASSWORD} -c app.omega_vault_password=${OMEGA_VAULT_PASSWORD} -c app.omega_workspace_password=${OMEGA_WORKSPACE_PASSWORD} -c app.omega_mcp_infra_password=${OMEGA_MCP_INFRA_PASSWORD} -c app.omega_cartridge_sap_hcm_password=${OMEGA_CARTRIDGE_SAP_HCM_PASSWORD} -c app.omega_cartridge_sap_s4_password=${OMEGA_CARTRIDGE_SAP_S4_PASSWORD} -c app.omega_cartridge_sap_sf_password=${OMEGA_CARTRIDGE_SAP_SF_PASSWORD} -c app.omega_airflow_dag_password=${OMEGA_AIRFLOW_DAG_PASSWORD} -c app.omega_airflow_meta_password=${OMEGA_AIRFLOW_META_PASSWORD} -c app.omega_superset_meta_password=${OMEGA_SUPERSET_META_PASSWORD} -c app.omega_cartridge_replicon_password=${OMEGA_CARTRIDGE_REPLICON_PASSWORD} -c app.omega_cartridge_salesforce_password=${OMEGA_CARTRIDGE_SALESFORCE_PASSWORD} -c app.omega_cartridge_hubspot_password=${OMEGA_CARTRIDGE_HUBSPOT_PASSWORD} -c app.omega_cartridge_banxico_password=${OMEGA_CARTRIDGE_BANXICO_PASSWORD} -c app.omega_cartridge_inegi_password=${OMEGA_CARTRIDGE_INEGI_PASSWORD} -c app.omega_cartridge_sec_edgar_password=${OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD}"
GOLD_PGOPTIONS_VALUE="-c app.omega_refinement_gold_password=${OMEGA_REFINEMENT_GOLD_PASSWORD} -c app.omega_gold_publisher_password=${OMEGA_GOLD_PUBLISHER_PASSWORD} -c app.omega_gold_verifier_password=${OMEGA_GOLD_VERIFIER_PASSWORD}"
POSTGRES_PGOPTIONS_VALUE="-c app.postgres_password=${POSTGRES_PASSWORD}"

PSQL_ADMIN=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T -e "PGOPTIONS=${POSTGRES_PGOPTIONS_VALUE}" postgres psql -v ON_ERROR_STOP=1 -U postgres -d postgres)
PSQL=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T -e "PGOPTIONS=${PGOPTIONS_VALUE}" postgres psql -v ON_ERROR_STOP=1 -U postgres -d modecissions)
PSQL_GOLD_ADMIN=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T -e "PGOPTIONS=${POSTGRES_PGOPTIONS_VALUE}" postgres_gold psql -v ON_ERROR_STOP=1 -U postgres -d postgres -p 5433)
PSQL_GOLD=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T -e "PGOPTIONS=${GOLD_PGOPTIONS_VALUE}" postgres_gold psql -v ON_ERROR_STOP=1 -U postgres -d modecissions_gold -p 5433)

echo "[reconcile-db-passwords] rotating postgres superuser password"
"${PSQL_ADMIN[@]}" <<'SQL'
DO $$
DECLARE
  role_password TEXT := current_setting('app.postgres_password', true);
BEGIN
  IF role_password IS NULL OR role_password = '' THEN
    RAISE EXCEPTION 'password for role postgres is empty';
  END IF;
  EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', 'postgres', role_password);
END $$;
SQL

echo "[reconcile-db-passwords] rotating operational DB role passwords"
"${PSQL[@]}" <<'SQL'
DO $$
DECLARE
  role_name TEXT;
  role_password TEXT;
BEGIN
  FOR role_name, role_password IN
    SELECT * FROM (VALUES
      ('omega_console', current_setting('app.omega_console_password', true)),
      ('omega_outcome_binder', current_setting('app.omega_outcome_binder_password', true)),
      ('omega_refinement', current_setting('app.omega_refinement_password', true)),
      ('omega_vault', current_setting('app.omega_vault_password', true)),
      ('omega_workspace', current_setting('app.omega_workspace_password', true)),
      ('omega_mcp_infra', current_setting('app.omega_mcp_infra_password', true)),
      ('omega_cartridge_sap_hcm', current_setting('app.omega_cartridge_sap_hcm_password', true)),
      ('omega_cartridge_sap_s4', current_setting('app.omega_cartridge_sap_s4_password', true)),
      ('omega_cartridge_sap_sf', current_setting('app.omega_cartridge_sap_sf_password', true)),
      ('omega_airflow_dag', current_setting('app.omega_airflow_dag_password', true)),
      ('omega_airflow_meta', current_setting('app.omega_airflow_meta_password', true)),
      ('omega_superset_meta', current_setting('app.omega_superset_meta_password', true)),
      ('omega_cartridge_replicon', current_setting('app.omega_cartridge_replicon_password', true)),
      ('omega_cartridge_salesforce', current_setting('app.omega_cartridge_salesforce_password', true)),
      ('omega_cartridge_hubspot', current_setting('app.omega_cartridge_hubspot_password', true)),
      ('omega_cartridge_banxico', current_setting('app.omega_cartridge_banxico_password', true)),
      ('omega_cartridge_inegi', current_setting('app.omega_cartridge_inegi_password', true))
    ) AS roles(role_name, role_password)
  LOOP
    IF role_password IS NULL OR role_password = '' THEN
      RAISE EXCEPTION 'password for role % is empty', role_name;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      RAISE EXCEPTION 'role % is missing; run make migrate first', role_name;
    END IF;
    EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', role_name, role_password);
  END LOOP;
END $$;
SQL

echo "[reconcile-db-passwords] rotating gold postgres superuser password"
"${PSQL_GOLD_ADMIN[@]}" <<'SQL'
DO $$
DECLARE
  role_password TEXT := current_setting('app.postgres_password', true);
BEGIN
  IF role_password IS NULL OR role_password = '' THEN
    RAISE EXCEPTION 'password for role postgres is empty';
  END IF;
  EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', 'postgres', role_password);
END $$;
SQL

echo "[reconcile-db-passwords] rotating gold DB role password"
"${PSQL_GOLD[@]}" <<'SQL'
DO $$
DECLARE
  role_password TEXT := current_setting('app.omega_refinement_gold_password', true);
BEGIN
  IF role_password IS NULL OR role_password = '' THEN
    RAISE EXCEPTION 'password for role omega_refinement_gold is empty';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_refinement_gold') THEN
    RAISE EXCEPTION 'role omega_refinement_gold is missing; run make migrate first';
  END IF;
  EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', 'omega_refinement_gold', role_password);
END $$;
SQL

"${PSQL_GOLD[@]}" <<'SQL'
DO $$
DECLARE role_password text := current_setting('app.omega_gold_publisher_password', true);
BEGIN
  IF role_password IS NULL OR role_password = '' THEN
    RAISE EXCEPTION 'password for role omega_gold_publisher is empty';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='omega_gold_publisher') THEN
    RAISE EXCEPTION 'role omega_gold_publisher is missing; run make migrate first';
  END IF;
  EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', 'omega_gold_publisher', role_password);
END $$;
SQL

"${PSQL_GOLD[@]}" <<'SQL'
DO $$
DECLARE role_password text := current_setting('app.omega_gold_verifier_password', true);
BEGIN
  IF role_password IS NULL OR role_password = '' THEN
    RAISE EXCEPTION 'password for role omega_gold_verifier is empty';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='omega_gold_verifier') THEN
    RAISE EXCEPTION 'role omega_gold_verifier is missing; run make migrate first';
  END IF;
  EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', 'omega_gold_verifier', role_password);
END $$;
SQL

echo "[reconcile-db-passwords] done"
