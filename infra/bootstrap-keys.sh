#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-infra/.env}"
BOOTSTRAP_CONTROL_ROOM_EVIDENCE="${MODECISSIONS_BOOTSTRAP_CONTROL_ROOM_EVIDENCE:-false}"
if [[ "$BOOTSTRAP_CONTROL_ROOM_EVIDENCE" != "true" && "$BOOTSTRAP_CONTROL_ROOM_EVIDENCE" != "false" ]]; then
  echo "ERROR: MODECISSIONS_BOOTSTRAP_CONTROL_ROOM_EVIDENCE must be true or false" >&2
  exit 1
fi

KEYS=(
  "SECURITY_CONTEXT_SIGNING_KEY"
  "CONTROL_ROOM_EVIDENCE_SIGNING_KEY"
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
  "INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT"
  "INTERNAL_API_KEY_REPLICON_TO_CONSOLE"
  "INTERNAL_API_KEY_REPLICON_TO_MCP_INFRA"
  "INTERNAL_API_KEY_REPLICON_TO_REFINEMENT"
  "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE"
  "INTERNAL_API_KEY_HUBSPOT_TO_MCP_INFRA"
  "INTERNAL_API_KEY_HUBSPOT_TO_REFINEMENT"
  "INTERNAL_API_KEY_BANXICO_TO_CONSOLE"
  "INTERNAL_API_KEY_INEGI_TO_CONSOLE"
  "INTERNAL_API_KEY_SEC_EDGAR_TO_CONSOLE"
  "INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE"
  "INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE"
  "INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE"
  "INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE"
  "INTERNAL_API_KEY_SAP_B1_TO_CONSOLE"
  "INTERNAL_API_KEY_MCP_INFRA_TO_VAULT"
  "INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE"
  "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT"
  "INTERNAL_API_KEY_WORKSPACE_TO_VAULT"
  "INTERNAL_API_KEY_REFINEMENT_TO_VAULT"
)

DB_KEYS=(
  "OMEGA_REFINEMENT_GOLD_PASSWORD"
  "OMEGA_GOLD_PUBLISHER_PASSWORD"
  "OMEGA_GOLD_VERIFIER_PASSWORD"
  "OMEGA_OUTCOME_BINDER_PASSWORD"
  "OMEGA_AIRFLOW_DAG_PASSWORD"
  "OMEGA_AIRFLOW_META_PASSWORD"
  "OMEGA_SUPERSET_META_PASSWORD"
  "OMEGA_CARTRIDGE_SAP_HCM_PASSWORD"
  "OMEGA_CARTRIDGE_SAP_S4_PASSWORD"
  "OMEGA_CARTRIDGE_SAP_SF_PASSWORD"
  "OMEGA_CARTRIDGE_SAP_B1_PASSWORD"
  "OMEGA_CARTRIDGE_REPLICON_PASSWORD"
  "OMEGA_CARTRIDGE_SALESFORCE_PASSWORD"
  "OMEGA_CARTRIDGE_HUBSPOT_PASSWORD"
  "OMEGA_CARTRIDGE_BANXICO_PASSWORD"
  "OMEGA_CARTRIDGE_INEGI_PASSWORD"
  "OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD"
)

DERIVED_KEYS=(
  "GOLD_VERIFIER_DATABASE_URL_HOST_FILE"
)

FERNET_KEYS=(
  "AIRFLOW_FERNET_KEY"
)
FERNET_KEY_SCRIPT='import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'

if ! command -v openssl >/dev/null 2>&1; then
  echo "ERROR: openssl is required to generate secrets" >&2
  exit 1
fi

mkdir -p "$(dirname "${ENV_FILE}")"
touch "${ENV_FILE}"
chmod 600 "${ENV_FILE}" || true

added=0
for key in "${KEYS[@]}"; do
  if [[ "$BOOTSTRAP_CONTROL_ROOM_EVIDENCE" == "false" && "$key" == "CONTROL_ROOM_EVIDENCE_SIGNING_KEY" ]]; then
    continue
  fi
  if grep -q "^${key}=" "${ENV_FILE}"; then
    echo "[bootstrap-keys] ${key} already exists, skipping"
  else
    value="$(openssl rand -hex 32)"
    printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
    echo "[bootstrap-keys] Generated ${key}"
    added=$((added + 1))
  fi
done

if [[ "$BOOTSTRAP_CONTROL_ROOM_EVIDENCE" == "true" ]]; then
  if ! grep -q '^CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID=' "${ENV_FILE}"; then
    printf 'CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID=evidence-%s\n' \
      "$(date -u +%Y%m%d%H%M%S)" >> "${ENV_FILE}"
    added=$((added + 1))
  fi
  if ! grep -q '^CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS=' "${ENV_FILE}"; then
    printf 'CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS={}\n' >> "${ENV_FILE}"
    added=$((added + 1))
  fi
fi

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

for key in "${FERNET_KEYS[@]}"; do
  if grep -q "^${key}=" "${ENV_FILE}"; then
    echo "[bootstrap-keys] ${key} already exists, skipping"
  else
    if ! command -v python3 >/dev/null 2>&1; then
      echo "ERROR: python3 is required to generate ${key}" >&2
      exit 1
    fi
    value="$(python3 -c "${FERNET_KEY_SCRIPT}")"
    printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
    echo "[bootstrap-keys] Generated ${key}"
    added=$((added + 1))
  fi
done

env_dir="$(cd "$(dirname "${ENV_FILE}")" && pwd)"
secret_dir="${env_dir}/.secrets"
secret_path="${secret_dir}/gold_verifier_database_url"
verifier_password="$(awk -F= '$1=="OMEGA_GOLD_VERIFIER_PASSWORD" {print $2}' "${ENV_FILE}")"
install -d -m 0700 "${secret_dir}"
printf 'postgresql://omega_gold_verifier:%s@postgres_gold:5433/modecissions_gold\n' \
  "${verifier_password}" >"${secret_path}"
chmod 0600 "${secret_path}"
if ! grep -q '^GOLD_VERIFIER_DATABASE_URL_HOST_FILE=' "${ENV_FILE}"; then
  printf 'GOLD_VERIFIER_DATABASE_URL_HOST_FILE=%s\n' "${secret_path}" >>"${ENV_FILE}"
fi

ensured=$((${#KEYS[@]} + ${#DB_KEYS[@]} + ${#DERIVED_KEYS[@]} + ${#FERNET_KEYS[@]} + 2))
if [[ "$BOOTSTRAP_CONTROL_ROOM_EVIDENCE" == "false" ]]; then
  ensured=$((ensured - 3))
fi
echo "[bootstrap-keys] Done. ${ensured} keys ensured in ${ENV_FILE} (${added} new)"
