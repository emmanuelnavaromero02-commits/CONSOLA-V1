#!/usr/bin/env bash
# Helpers for building and transactionally publishing the AWS runtime env pair.

AWS_ENV_SHARED_TARGET=""
AWS_ENV_EVIDENCE_TARGET=""
AWS_ENV_SHARED_TMP=""
AWS_ENV_EVIDENCE_TMP=""
AWS_ENV_SHARED_STAGE=""
AWS_ENV_EVIDENCE_STAGE=""
AWS_ENV_SHARED_BACKUP=""
AWS_ENV_EVIDENCE_BACKUP=""
AWS_ENV_SHARED_EXISTED="false"
AWS_ENV_EVIDENCE_EXISTED="false"

aws_env_pair_cleanup() {
  local path
  for path in \
    "${AWS_ENV_SHARED_TMP:-}" \
    "${AWS_ENV_EVIDENCE_TMP:-}" \
    "${AWS_ENV_SHARED_STAGE:-}" \
    "${AWS_ENV_EVIDENCE_STAGE:-}" \
    "${AWS_ENV_SHARED_BACKUP:-}" \
    "${AWS_ENV_EVIDENCE_BACKUP:-}"; do
    if [[ -n "$path" ]]; then
      rm -f "$path"
    fi
  done
}

aws_env_pair_init() {
  local shared_target="$1"
  local evidence_target="$2"

  if [[ "$shared_target" == "$evidence_target" ]]; then
    echo "[aws-entrypoint] shared and evidence env files must be different" >&2
    return 1
  fi

  AWS_ENV_SHARED_TARGET="$shared_target"
  AWS_ENV_EVIDENCE_TARGET="$evidence_target"
  trap aws_env_pair_cleanup EXIT

  if ! AWS_ENV_SHARED_TMP="$(mktemp)"; then
    return 1
  fi
  if ! AWS_ENV_EVIDENCE_TMP="$(mktemp)"; then
    return 1
  fi
  chmod 600 "$AWS_ENV_SHARED_TMP" "$AWS_ENV_EVIDENCE_TMP"
}

write_env_file() {
  local output_file="$1"
  local key="$2"
  local value="$3"
  local escaped

  if [[ "$value" == *$'\n'* ]]; then
    echo "[aws-entrypoint] value for $key contains newline; refusing dotenv" >&2
    return 1
  fi
  escaped="${value//\\/\\\\}"
  escaped="${escaped//\"/\\\"}"
  escaped="${escaped//\$/\\$}"
  escaped="${escaped//\`/\\\`}"
  printf '%s="%s"\n' "$key" "$escaped" >> "$output_file"
}

write_env() {
  write_env_file "$AWS_ENV_SHARED_TMP" "$1" "$2"
}

write_evidence_env() {
  write_env_file "$AWS_ENV_EVIDENCE_TMP" "$1" "$2"
}

fetch_secret() {
  local name="$1"
  local arn="$2"
  local delay=1
  local attempt value

  for attempt in 1 2 3; do
    if value="$(aws --region "$AWS_REGION" secretsmanager get-secret-value \
      --secret-id "$arn" \
      --query SecretString \
      --output text 2>/dev/null)"; then
      printf '%s' "$value"
      return 0
    fi
    echo "[aws-entrypoint] secret fetch failed for $name attempt=$attempt" >&2
    if [[ "$attempt" == "3" ]]; then
      return 1
    fi
    sleep "$delay"
    delay=$((delay * 2))
  done
}

aws_env_stage_file() {
  local source_file="$1"
  local target_file="$2"
  local output_var="$3"
  local target_dir staged_file

  target_dir="$(dirname "$target_file")"
  if ! mkdir -p "$target_dir"; then
    return 1
  fi
  if ! staged_file="$(mktemp "${target_file}.tmp.XXXXXX")"; then
    return 1
  fi
  if ! install -m 600 -o root -g root "$source_file" "$staged_file"; then
    rm -f "$staged_file"
    return 1
  fi
  printf -v "$output_var" '%s' "$staged_file"
}

aws_env_backup_target() {
  local target_file="$1"
  local backup_var="$2"
  local existed_var="$3"
  local backup_file

  printf -v "$backup_var" '%s' ""
  printf -v "$existed_var" '%s' "false"
  if [[ ! -e "$target_file" && ! -L "$target_file" ]]; then
    return 0
  fi

  if ! backup_file="$(mktemp "${target_file}.backup.XXXXXX")"; then
    return 1
  fi
  if ! cp -p "$target_file" "$backup_file"; then
    rm -f "$backup_file"
    return 1
  fi
  printf -v "$backup_var" '%s' "$backup_file"
  printf -v "$existed_var" '%s' "true"
}

aws_env_restore_target() {
  local target_file="$1"
  local backup_var="$2"
  local existed_var="$3"
  local backup_file="${!backup_var}"
  local existed="${!existed_var}"

  if [[ "$existed" == "true" ]]; then
    if [[ -z "$backup_file" ]] || ! mv -f "$backup_file" "$target_file"; then
      return 1
    fi
    printf -v "$backup_var" '%s' ""
    return 0
  fi
  rm -f "$target_file"
}

aws_env_pair_rollback() {
  local failed=0

  aws_env_restore_target \
    "$AWS_ENV_SHARED_TARGET" AWS_ENV_SHARED_BACKUP AWS_ENV_SHARED_EXISTED \
    || failed=1
  aws_env_restore_target \
    "$AWS_ENV_EVIDENCE_TARGET" AWS_ENV_EVIDENCE_BACKUP AWS_ENV_EVIDENCE_EXISTED \
    || failed=1
  return "$failed"
}

aws_env_discard_backups() {
  if [[ -n "$AWS_ENV_SHARED_BACKUP" ]]; then
    rm -f "$AWS_ENV_SHARED_BACKUP"
    AWS_ENV_SHARED_BACKUP=""
  fi
  if [[ -n "$AWS_ENV_EVIDENCE_BACKUP" ]]; then
    rm -f "$AWS_ENV_EVIDENCE_BACKUP"
    AWS_ENV_EVIDENCE_BACKUP=""
  fi
}

publish_env_pair() {
  if ! aws_env_stage_file \
    "$AWS_ENV_SHARED_TMP" "$AWS_ENV_SHARED_TARGET" AWS_ENV_SHARED_STAGE; then
    return 1
  fi
  if ! aws_env_stage_file \
    "$AWS_ENV_EVIDENCE_TMP" "$AWS_ENV_EVIDENCE_TARGET" AWS_ENV_EVIDENCE_STAGE; then
    return 1
  fi
  if ! aws_env_backup_target \
    "$AWS_ENV_SHARED_TARGET" AWS_ENV_SHARED_BACKUP AWS_ENV_SHARED_EXISTED; then
    return 1
  fi
  if ! aws_env_backup_target \
    "$AWS_ENV_EVIDENCE_TARGET" AWS_ENV_EVIDENCE_BACKUP AWS_ENV_EVIDENCE_EXISTED; then
    return 1
  fi

  if mv -f "$AWS_ENV_EVIDENCE_STAGE" "$AWS_ENV_EVIDENCE_TARGET"; then
    AWS_ENV_EVIDENCE_STAGE=""
  else
    echo "[aws-entrypoint] env pair publish failed; restoring previous files" >&2
    if ! aws_env_pair_rollback; then
      echo "[aws-entrypoint] env pair rollback failed" >&2
    fi
    return 1
  fi
  if mv -f "$AWS_ENV_SHARED_STAGE" "$AWS_ENV_SHARED_TARGET"; then
    AWS_ENV_SHARED_STAGE=""
  else
    echo "[aws-entrypoint] env pair publish failed; restoring previous files" >&2
    if ! aws_env_pair_rollback; then
      echo "[aws-entrypoint] env pair rollback failed" >&2
    fi
    return 1
  fi

  aws_env_discard_backups
}
