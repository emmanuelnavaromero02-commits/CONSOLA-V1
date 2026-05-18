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
  POSTGRES_PASSWORD
  FIELD_ENCRYPTION_KEY
  OMEGA_CONSOLE_PASSWORD
  OMEGA_REFINEMENT_PASSWORD
  OMEGA_VAULT_PASSWORD
  OMEGA_WORKSPACE_PASSWORD
  OMEGA_MCP_INFRA_PASSWORD
  OMEGA_REFINEMENT_GOLD_PASSWORD
  OMEGA_AIRFLOW_DAG_PASSWORD
  OMEGA_AIRFLOW_META_PASSWORD
  AIRFLOW_SECRET_KEY
  AIRFLOW_ADMIN_PASSWORD
  SUPERSET_SECRET_KEY
  SUPERSET_ADMIN_PASSWORD
)

optional_secrets=(
  GEMINI_API_KEY
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

echo "[aws-entrypoint] writing runtime env to $ENV_FILE"
required_config=(
  AWS_REGION
  S3_BUCKET_NAME
  AIRFLOW_ADMIN_USER
  SUPERSET_ADMIN_USER
)

AIRFLOW_ADMIN_USER="${AIRFLOW_ADMIN_USER:-admin}"
SUPERSET_ADMIN_USER="${SUPERSET_ADMIN_USER:-admin}"
GHCR_OWNER="${GHCR_OWNER:-emmanuelnavaromero02-commits}"
IMAGE_TAG="${IMAGE_TAG:-v1.44.5}"
CONSOLE_URL="${CONSOLE_URL:-http://localhost:8000}"
WORKSPACE_PUBLIC_URL="${WORKSPACE_PUBLIC_URL:-http://localhost:8001}"
APP_BASE_URL="${APP_BASE_URL:-$CONSOLE_URL}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-$CONSOLE_URL,$WORKSPACE_PUBLIC_URL}"
SMTP_HOST="${SMTP_HOST:-mailhog}"
SMTP_PORT="${SMTP_PORT:-1025}"
SMTP_USER="${SMTP_USER:-}"
SMTP_FROM="${SMTP_FROM:-noreply@modecissions.local}"
SMTP_USE_TLS="${SMTP_USE_TLS:-false}"
APP_ENV="${APP_ENV:-production}"
CHAT_LLM_PROVIDER="${CHAT_LLM_PROVIDER:-anthropic}"
CHAT_LLM_MODEL="${CHAT_LLM_MODEL:-claude-haiku-4-5-20251001}"
SQL_LLM_MODEL="${SQL_LLM_MODEL:-claude-sonnet-4-6}"
GEMINI_CACHE_ENABLED="${GEMINI_CACHE_ENABLED:-true}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"
EMBED_DIM="${EMBED_DIM:-768}"
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

for config_name in \
  GHCR_OWNER IMAGE_TAG CONSOLE_URL WORKSPACE_PUBLIC_URL APP_BASE_URL ALLOWED_ORIGINS \
  SMTP_HOST SMTP_PORT SMTP_USER SMTP_FROM SMTP_USE_TLS APP_ENV \
  CHAT_LLM_PROVIDER CHAT_LLM_MODEL SQL_LLM_MODEL GEMINI_CACHE_ENABLED \
  OLLAMA_URL EMBED_MODEL EMBED_DIM INVITE_TOKEN_TTL_HOURS RESET_TOKEN_TTL_HOURS; do
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
