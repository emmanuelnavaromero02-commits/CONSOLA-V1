#!/usr/bin/env bash
# infra/bootstrap-keys.sh — generate the per-pair INTERNAL_API_KEY_* secrets.
#
# Sprint v1.12: the platform used to share one INTERNAL_API_KEY across 11
# services; a compromise in any one of them meant every internal call could
# be forged. Each client→server pair now has its own key. This script
# generates those keys into infra/.env IF they don't exist yet.
#
# Idempotent: re-running with an existing key leaves it untouched.
# The legacy INTERNAL_API_KEY in infra/.env is *not* touched — it stays
# valid as a fallback during the migration window and gets retired in a
# later sprint.
set -euo pipefail

ENV_FILE="${1:-infra/.env}"

KEYS=(
  "SECURITY_CONTEXT_SIGNING_KEY"
  "INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT"
  "INTERNAL_API_KEY_CONSOLE_TO_CONSOLE"
  "INTERNAL_API_KEY_CONSOLE_TO_VAULT"
  "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA"
  "INTERNAL_API_KEY_MCP_INFRA_TO_CONSOLE"
  "INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE"
  "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE"
  "INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE"
  "INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT"
  "INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA"
  "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA"
  "INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT"
  "INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE"
  "INTERNAL_API_KEY_REFINEMENT_TO_MCP_INFRA"
  "INTERNAL_API_KEY_REPLICON_TO_CONSOLE"
  "INTERNAL_API_KEY_REPLICON_TO_MCP_INFRA"
  "INTERNAL_API_KEY_REPLICON_TO_REFINEMENT"
  "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE"
  "INTERNAL_API_KEY_HUBSPOT_TO_MCP_INFRA"
  "INTERNAL_API_KEY_HUBSPOT_TO_REFINEMENT"
  "INTERNAL_API_KEY_BANXICO_TO_CONSOLE"
  "INTERNAL_API_KEY_INEGI_TO_CONSOLE"
  "INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE"
  "INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE"
  "INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE"
  "INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE"
  "INTERNAL_API_KEY_MCP_INFRA_TO_VAULT"
  "INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE"
  "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT"
  # Sprint v1.26 (audit F11): provisioned for future use. Workspace and
  # refinement don't call vault TODAY, but vault still accepts them via
  # the legacy shared INTERNAL_API_KEY — so a misrouted call wouldn't
  # be visible until something failed. With these keys in place, when
  # either service starts calling vault it can switch to the dedicated
  # key and the legacy fallback drops a WARNING that's easy to grep.
  "INTERNAL_API_KEY_WORKSPACE_TO_VAULT"
  "INTERNAL_API_KEY_REFINEMENT_TO_VAULT"
)

DB_KEYS=(
  "OMEGA_REFINEMENT_GOLD_PASSWORD"
  "OMEGA_AIRFLOW_DAG_PASSWORD"
  "OMEGA_AIRFLOW_META_PASSWORD"
  "OMEGA_SUPERSET_META_PASSWORD"
  "OMEGA_CARTRIDGE_SAP_HCM_PASSWORD"
  "OMEGA_CARTRIDGE_SAP_S4_PASSWORD"
  "OMEGA_CARTRIDGE_SAP_SF_PASSWORD"
  "OMEGA_CARTRIDGE_REPLICON_PASSWORD"
  "OMEGA_CARTRIDGE_SALESFORCE_PASSWORD"
  "OMEGA_CARTRIDGE_HUBSPOT_PASSWORD"
  "OMEGA_CARTRIDGE_BANXICO_PASSWORD"
  "OMEGA_CARTRIDGE_INEGI_PASSWORD"
)

if ! command -v openssl >/dev/null 2>&1; then
  echo "ERROR: openssl is required to generate secrets" >&2
  exit 1
fi

mkdir -p "$(dirname "${ENV_FILE}")"
touch "${ENV_FILE}"
chmod 600 "${ENV_FILE}" || true

added=0
for key in "${KEYS[@]}"; do
  if grep -q "^${key}=" "${ENV_FILE}"; then
    echo "[bootstrap-keys] ${key} already exists, skipping"
  else
    value="$(openssl rand -hex 32)"
    printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
    echo "[bootstrap-keys] Generated ${key}"
    added=$((added + 1))
  fi
done

for key in "${DB_KEYS[@]}"; do
  if grep -q "^${key}=" "${ENV_FILE}"; then
    echo "[bootstrap-keys] ${key} already exists, skipping"
  else
    value="$(openssl rand -hex 16)"
    printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
    echo "[bootstrap-keys] Generated ${key}"
    added=$((added + 1))
  fi
done

echo "[bootstrap-keys] Done. $((${#KEYS[@]} + ${#DB_KEYS[@]})) keys ensured in ${ENV_FILE} (${added} new)"
