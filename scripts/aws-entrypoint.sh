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
  GEMINI_API_KEY
  JWT_SECRET_KEY
  INTERNAL_API_KEY
  POSTGRES_PASSWORD
  FIELD_ENCRYPTION_KEY
  SMTP_PASSWORD
)

fetch_secret() {
  local name="$1"
  local arn="$2"
  local delay=1
  local attempt

  for attempt in 1 2 3; do
    if value="$(aws --region "$AWS_REGION" secretsmanager get-secret-value \
      --secret-id "$arn" \
      --query SecretString \
      --output text 2>/tmp/aws-entrypoint-secret.err)"; then
      printf '%s' "$value"
      return 0
    fi
    echo "[aws-entrypoint] secret fetch failed for $name attempt=$attempt"
    if [[ "$attempt" == "3" ]]; then
      cat /tmp/aws-entrypoint-secret.err || true
      return 1
    fi
    sleep "$delay"
    delay=$((delay * 2))
  done
}

tmp_file="$(mktemp)"
chmod 600 "$tmp_file"

echo "[aws-entrypoint] writing runtime env to $ENV_FILE"
for secret_name in "${required_secrets[@]}"; do
  arn_var="MODECISSIONS_SECRET_${secret_name}_ARN"
  arn="${!arn_var:-}"
  if [[ -z "$arn" ]]; then
    echo "[aws-entrypoint] missing ARN env var: $arn_var"
    rm -f "$tmp_file"
    exit 1
  fi
  secret_value="$(fetch_secret "$secret_name" "$arn")" || {
    echo "[aws-entrypoint] unable to fetch required secret: $secret_name"
    rm -f "$tmp_file"
    exit 1
  }
  printf '%s=%s\n' "$secret_name" "$secret_value" >> "$tmp_file"
done

install -m 600 -o root -g root "$tmp_file" "$ENV_FILE"
rm -f "$tmp_file"
echo "[aws-entrypoint] runtime env written successfully"
