#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

LOG_FILE="${MODECISSIONS_AWS_ENTRYPOINT_LOG:-/var/log/aws-entrypoint.log}"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

ENV_CONFIG="${MODECISSIONS_AWS_ENTRYPOINT_CONFIG:-/etc/modecissions/aws-entrypoint.env}"
if [[ -f "$ENV_CONFIG" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_CONFIG"
fi

AWS_REGION="${AWS_REGION:-us-east-1}"
ENV_FILE="${MODECISSIONS_ENV_FILE:-/opt/modecissions/infra/terraform/deploy/.env}"

required_secrets=(
  ANTHROPIC_API_KEY
  JWT_SECRET_KEY
  INTERNAL_API_KEY
  SECURITY_CONTEXT_SIGNING_KEY
  INTERNAL_API_KEY_CONSOLE_TO_CONSOLE
  INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT
  INTERNAL_API_KEY_CONSOLE_TO_VAULT
  INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA
  INTERNAL_API_KEY_MCP_INFRA_TO_CONSOLE
  INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE
  INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE
  INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE
  INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT
  INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA
  INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA
  INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT
  INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE
  INTERNAL_API_KEY_REFINEMENT_TO_MCP_INFRA
  INTERNAL_API_KEY_MCP_INFRA_TO_REFINEMENT
  INTERNAL_API_KEY_REPLICON_TO_CONSOLE
  INTERNAL_API_KEY_REPLICON_TO_MCP_INFRA
  INTERNAL_API_KEY_REPLICON_TO_REFINEMENT
  INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE
  INTERNAL_API_KEY_HUBSPOT_TO_MCP_INFRA
  INTERNAL_API_KEY_HUBSPOT_TO_REFINEMENT
  INTERNAL_API_KEY_BANXICO_TO_CONSOLE
  INTERNAL_API_KEY_INEGI_TO_CONSOLE
  INTERNAL_API_KEY_SEC_EDGAR_TO_CONSOLE
  INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE
  INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE
  INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE
  INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE
  INTERNAL_API_KEY_MCP_INFRA_TO_VAULT
  INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE
  INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT
  INTERNAL_API_KEY_WORKSPACE_TO_VAULT
  INTERNAL_API_KEY_REFINEMENT_TO_VAULT
  POSTGRES_PASSWORD
  FIELD_ENCRYPTION_KEY
  VAULT_ENCRYPTION_KEY
  OMEGA_CONSOLE_PASSWORD
  OMEGA_REFINEMENT_PASSWORD
  OMEGA_VAULT_PASSWORD
  OMEGA_WORKSPACE_PASSWORD
  OMEGA_MCP_INFRA_PASSWORD
  OMEGA_REFINEMENT_GOLD_PASSWORD
  OMEGA_AIRFLOW_DAG_PASSWORD
  OMEGA_AIRFLOW_META_PASSWORD
  OMEGA_SUPERSET_META_PASSWORD
  OMEGA_CARTRIDGE_SAP_HCM_PASSWORD
  OMEGA_CARTRIDGE_SAP_S4_PASSWORD
  OMEGA_CARTRIDGE_SAP_SF_PASSWORD
  OMEGA_CARTRIDGE_REPLICON_PASSWORD
  OMEGA_CARTRIDGE_SALESFORCE_PASSWORD
  OMEGA_CARTRIDGE_HUBSPOT_PASSWORD
  OMEGA_CARTRIDGE_BANXICO_PASSWORD
  OMEGA_CARTRIDGE_INEGI_PASSWORD
  OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD
  AIRFLOW_SECRET_KEY
  AIRFLOW_ADMIN_PASSWORD
  AGENT_RUNNER_TOKEN
  SUPERSET_SECRET_KEY
  SUPERSET_ADMIN_PASSWORD
  SUPERSET_SERVICE_PASSWORD
)

optional_secrets=(
  SMTP_PASSWORD
)

tmp_file="$(mktemp)"
err_file="$(mktemp)"
trap 'rm -f "$tmp_file" "$err_file"' EXIT
chmod 600 "$tmp_file" "$err_file"

write_env() {
  local key="$1"
  local value="$2"
  if [[ "$value" == *$'\n'* ]]; then
    echo "[aws-entrypoint] value for $key contains newline; refusing to write dotenv" >&2
    return 1
  fi
  local escaped="${value//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  escaped="${escaped//\$/\\$}"
  escaped="${escaped//\`/\\\`}"
  printf '%s="%s"\n' "$key" "$escaped" >> "$tmp_file"
}

fetch_secret() {
  local name="$1"
  local arn="$2"
  local delay=1
  local attempt

  for attempt in 1 2 3; do
    if value="$(aws --region "$AWS_REGION" secretsmanager get-secret-value \
      --secret-id "$arn" \
      --query SecretString \
      --output text 2>"$err_file")"; then
      printf '%s' "$value"
      return 0
    fi
    echo "[aws-entrypoint] secret fetch failed for $name attempt=$attempt" >&2
    if [[ "$attempt" == "3" ]]; then
      cat "$err_file" >&2 || true
      return 1
    fi
    sleep "$delay"
    delay=$((delay * 2))
  done
}

derive_public_url() {
  local base="$1"
  local port="$2"
  local scheme rest hostport host
  scheme="${base%%://*}"
  rest="${base#*://}"
  hostport="${rest%%/*}"
  host="${hostport%%:*}"
  printf '%s://%s:%s' "$scheme" "$host" "$port"
}

is_release_tag() {
  [[ "${1:-}" =~ ^v[0-9] ]]
}

assert_release_refs_coherent() {
  if is_release_tag "${DEPLOY_REF:-}" && is_release_tag "${IMAGE_TAG:-}" && [[ "${DEPLOY_REF}" != "${IMAGE_TAG}" ]]; then
    echo "[aws-entrypoint] DEPLOY_REF (${DEPLOY_REF}) must match IMAGE_TAG (${IMAGE_TAG}) for tag-based production deploys" >&2
    exit 1
  fi
}

echo "[aws-entrypoint] writing runtime env to $ENV_FILE"
required_config=(
  AWS_REGION
  S3_BUCKET_NAME
  AIRFLOW_ADMIN_USER
  SUPERSET_ADMIN_USER
  CONSOLE_URL
  WORKSPACE_PUBLIC_URL
)

AIRFLOW_ADMIN_USER="${AIRFLOW_ADMIN_USER:-admin}"
SUPERSET_ADMIN_USER="${SUPERSET_ADMIN_USER:-admin}"
SUPERSET_SERVICE_USER="${SUPERSET_SERVICE_USER:-omega_service}"
APP_ENV="${APP_ENV:-production}"
COOKIE_SECURE="${COOKIE_SECURE:-}"
GHCR_OWNER="${GHCR_OWNER:-emmanuelnavaromero02-commits}"
IMAGE_TAG="${IMAGE_TAG:-}"
DEPLOY_REF="${DEPLOY_REF:-}"
DEPLOY_CARTRIDGES_SAME_HOST="${DEPLOY_CARTRIDGES_SAME_HOST:-true}"
CONSOLE_URL="${CONSOLE_URL:-}"
WORKSPACE_PUBLIC_URL="${WORKSPACE_PUBLIC_URL:-}"
APP_BASE_URL="${APP_BASE_URL:-$CONSOLE_URL}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-$CONSOLE_URL,$WORKSPACE_PUBLIC_URL}"
AIRFLOW_PUBLIC_URL="${AIRFLOW_PUBLIC_URL:-${CONSOLE_URL%/}/airflow}"
# Superset is internal/admin-only by default for multi-tenant beta. Do not
# derive a public tenant-facing URL unless an explicit, tested admin proxy is
# configured by the deployment.
SUPERSET_PUBLIC_URL="${SUPERSET_PUBLIC_URL:-}"
PUBLIC_HTTPS_DEFAULT="false"
if [[ "$CONSOLE_URL" == https://* && "$WORKSPACE_PUBLIC_URL" == https://* ]]; then
  PUBLIC_HTTPS_DEFAULT="true"
fi
COOKIE_SECURE="${COOKIE_SECURE:-$PUBLIC_HTTPS_DEFAULT}"
SUPERSET_SESSION_COOKIE_SECURE="${SUPERSET_SESSION_COOKIE_SECURE:-$PUBLIC_HTTPS_DEFAULT}"
SUPERSET_FORCE_HTTPS="${SUPERSET_FORCE_HTTPS:-$PUBLIC_HTTPS_DEFAULT}"
SUPERSET_ENABLE_PROXY_FIX="${SUPERSET_ENABLE_PROXY_FIX:-true}"
SUPERSET_TALISMAN_ENABLED="${SUPERSET_TALISMAN_ENABLED:-true}"
SUPERSET_CSRF_ENABLED="${SUPERSET_CSRF_ENABLED:-true}"
SUPERSET_RATELIMIT_ENABLED="${SUPERSET_RATELIMIT_ENABLED:-true}"
SUPERSET_RATELIMIT_STORAGE_URI="${SUPERSET_RATELIMIT_STORAGE_URI:-redis://redis:6379/1}"
SUPERSET_SESSION_COOKIE_SAMESITE="${SUPERSET_SESSION_COOKIE_SAMESITE:-Lax}"
SMTP_HOST="${SMTP_HOST:-mailhog}"
EMAIL_PROVIDER="${EMAIL_PROVIDER:-smtp}"
SMTP_PORT="${SMTP_PORT:-1025}"
SMTP_USER="${SMTP_USER:-}"
SMTP_FROM="${SMTP_FROM:-noreply@modecissions.local}"
SMTP_FROM_DOMAIN="${SMTP_FROM_DOMAIN:-modecissions.local}"
SMTP_USE_TLS="${SMTP_USE_TLS:-false}"
CHAT_LLM_PROVIDER="${CHAT_LLM_PROVIDER:-anthropic}"
CHAT_LLM_MODEL="${CHAT_LLM_MODEL:-claude-haiku-4-5-20251001}"
SQL_LLM_MODEL="${SQL_LLM_MODEL:-claude-sonnet-4-6}"
OLLAMA_URL="${OLLAMA_URL:-http://host.docker.internal:11434}"
BEDROCK_REGION="${BEDROCK_REGION:-$AWS_REGION}"
EMBED_MODEL="${EMBED_MODEL:-amazon.titan-embed-text-v2:0}"
EMBED_DIM="${EMBED_DIM:-1024}"
INVITE_TOKEN_TTL_HOURS="${INVITE_TOKEN_TTL_HOURS:-72}"
RESET_TOKEN_TTL_HOURS="${RESET_TOKEN_TTL_HOURS:-1}"

for config_name in "${required_config[@]}"; do
  value="${!config_name:-}"
  if [[ -z "$value" ]]; then
    echo "[aws-entrypoint] missing required config: $config_name" >&2
    exit 1
  fi
  write_env "$config_name" "$value"
done

if [[ "$APP_ENV" =~ ^(production|prod)$ ]]; then
  if [[ -z "$IMAGE_TAG" || "$IMAGE_TAG" == "latest" ]]; then
    echo "[aws-entrypoint] IMAGE_TAG must be an immutable release tag in production (not empty/latest)" >&2
    exit 1
  fi
  if [[ -z "$DEPLOY_REF" ]]; then
    echo "[aws-entrypoint] DEPLOY_REF must be an immutable release ref in production" >&2
    exit 1
  fi
  assert_release_refs_coherent
	  for url_var in CONSOLE_URL WORKSPACE_PUBLIC_URL APP_BASE_URL AIRFLOW_PUBLIC_URL SUPERSET_PUBLIC_URL; do
	    value="${!url_var:-}"
	    if [[ -z "$value" ]]; then
	      continue
	    fi
	    if [[ "$value" == *"localhost"* || "$value" == *"127.0.0.1"* ]]; then
	      echo "[aws-entrypoint] $url_var must not point to localhost in production: $value" >&2
	      exit 1
	    fi
	  done
	  for url_var in CONSOLE_URL WORKSPACE_PUBLIC_URL APP_BASE_URL; do
	    value="${!url_var:-}"
	    if [[ "$value" != https://* ]]; then
	      echo "[aws-entrypoint] $url_var must use https in production: $value" >&2
	      exit 1
	    fi
	  done
	fi

for config_name in \
	  GHCR_OWNER IMAGE_TAG DEPLOY_REF DEPLOY_CARTRIDGES_SAME_HOST APP_BASE_URL ALLOWED_ORIGINS \
	  COOKIE_SECURE \
	  AIRFLOW_PUBLIC_URL SUPERSET_PUBLIC_URL \
	  SUPERSET_ENABLE_PROXY_FIX SUPERSET_TALISMAN_ENABLED SUPERSET_CSRF_ENABLED \
	  SUPERSET_RATELIMIT_ENABLED SUPERSET_RATELIMIT_STORAGE_URI \
	  SUPERSET_SESSION_COOKIE_SECURE SUPERSET_FORCE_HTTPS SUPERSET_SESSION_COOKIE_SAMESITE \
	  SUPERSET_SERVICE_USER \
	  EMAIL_PROVIDER SMTP_HOST SMTP_PORT SMTP_USER SMTP_FROM SMTP_FROM_DOMAIN SMTP_USE_TLS APP_ENV \
	  CHAT_LLM_PROVIDER CHAT_LLM_MODEL SQL_LLM_MODEL \
	  OLLAMA_URL BEDROCK_REGION EMBED_MODEL EMBED_DIM INVITE_TOKEN_TTL_HOURS RESET_TOKEN_TTL_HOURS; do
  write_env "$config_name" "${!config_name}"
done

for secret_name in "${required_secrets[@]}"; do
  arn_var="MODECISSIONS_SECRET_${secret_name}_ARN"
  arn="${!arn_var:-}"
  if [[ -z "$arn" ]]; then
    echo "[aws-entrypoint] missing ARN env var: $arn_var"
    exit 1
  fi
  secret_value="$(fetch_secret "$secret_name" "$arn")" || {
    echo "[aws-entrypoint] unable to fetch required secret: $secret_name"
    exit 1
  }
  write_env "$secret_name" "$secret_value"
done

for secret_name in "${optional_secrets[@]}"; do
  arn_var="MODECISSIONS_SECRET_${secret_name}_ARN"
  arn="${!arn_var:-}"
  if [[ -z "$arn" ]]; then
    write_env "$secret_name" ""
    continue
  fi
  if secret_value="$(fetch_secret "$secret_name" "$arn")"; then
    write_env "$secret_name" "$secret_value"
  else
    echo "[aws-entrypoint] optional secret unavailable, writing empty value: $secret_name" >&2
    write_env "$secret_name" ""
  fi
done

while IFS='=' read -r env_name env_value; do
  if [[ "$env_name" == MODECISSIONS_ENV_* ]]; then
    write_env "${env_name#MODECISSIONS_ENV_}" "$env_value"
  fi
done < <(env)

install -m 600 -o root -g root "$tmp_file" "$ENV_FILE"
echo "[aws-entrypoint] runtime env written successfully"
