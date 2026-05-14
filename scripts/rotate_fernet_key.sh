#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-infra/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "env file not found: $ENV_FILE" >&2
  exit 1
fi

old_key="$(grep -E '^VAULT_ENCRYPTION_KEY=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
old_kid="$(grep -E '^VAULT_ENCRYPTION_KEY_ID=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
old_ring="$(grep -E '^VAULT_ENCRYPTION_KEYS=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"

if [[ -z "$old_key" ]]; then
  echo "VAULT_ENCRYPTION_KEY missing in $ENV_FILE" >&2
  exit 1
fi

if [[ -z "$old_kid" ]]; then
  old_kid="legacy-$(date -u +%Y%m%d%H%M%S)"
fi

new_kid="key-$(date -u +%Y%m%d%H%M%S)"
new_key="$(
  python3 - <<'PY'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
PY
)"

backup="${ENV_FILE}.fernet-rotation.$(date -u +%Y%m%d%H%M%S).bak"
cp "$ENV_FILE" "$backup"

tmp="$(mktemp)"
grep -Ev '^(VAULT_ENCRYPTION_KEY|VAULT_ENCRYPTION_KEY_ID|VAULT_ENCRYPTION_KEYS)=' "$ENV_FILE" > "$tmp"

if [[ -n "$old_ring" ]]; then
  new_ring="${old_ring},${old_kid}=${old_key}"
else
  new_ring="${old_kid}=${old_key}"
fi

{
  cat "$tmp"
  echo "VAULT_ENCRYPTION_KEY=${new_key}"
  echo "VAULT_ENCRYPTION_KEY_ID=${new_kid}"
  echo "VAULT_ENCRYPTION_KEYS=${new_ring}"
} > "$ENV_FILE"
rm -f "$tmp"

echo "Rotated Vault Fernet key in $ENV_FILE"
echo "Backup: $backup"
echo "New key id: $new_kid"
echo "Previous key retained as: $old_kid"
