#!/usr/bin/env bash
# Guarded backup/restore rehearsal.
#
# Default mode is non-destructive: it verifies the DR scripts still contain the
# required backup, restore, manifest, and destructive-confirmation controls.
# Set OMEGA_DR_REHEARSAL_EXECUTE=1 only in a disposable staging/prod-like
# environment to run backup.sh + restore.sh and then smoke/readiness.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

log() {
  printf '[dr-rehearsal] %s\n' "$*"
}

check_live_llm_if_required() {
  if [[ "${OMEGA_REQUIRE_LIVE_LLM:-0}" != "1" ]]; then
    log "live LLM probe skipped; set OMEGA_REQUIRE_LIVE_LLM=1 to require Anthropic"
    return
  fi
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    log "ANTHROPIC_API_KEY is required when OMEGA_REQUIRE_LIVE_LLM=1"
    exit 2
  fi
  log "checking live Anthropic chat path after restore"
  PYTHONPATH=console "${PYTHON_BIN}" - <<'PY'
import asyncio

from app.services import llm_client


async def main() -> None:
    reply, _urls, _messages = await llm_client.chat(
        system="Responde solo OK.",
        messages=[{"role": "user", "content": "OK"}],
        tools=[],
        invoke_tool=None,
        tool_server_map={},
        max_tokens=16,
        temperature=0,
    )
    if not str(reply or "").strip():
        raise SystemExit("Anthropic returned an empty reply")


asyncio.run(main())
PY
}

require_in_file() {
  local needle="$1" file="$2"
  if ! grep -Fq "$needle" "$file"; then
    log "missing required DR control in ${file}: ${needle}"
    exit 1
  fi
}

BACKUP_SCRIPT="infra/terraform/deploy/backup.sh"
RESTORE_SCRIPT="infra/terraform/deploy/restore.sh"

for script in "${BACKUP_SCRIPT}" "${RESTORE_SCRIPT}"; do
  if [[ ! -f "${script}" ]]; then
    log "missing ${script}"
    exit 1
  fi
  require_in_file "set -euo pipefail" "${script}"
done

require_in_file "pg_dumpall -U postgres --clean --if-exists" "${BACKUP_SCRIPT}"
require_in_file "manifest.json" "${BACKUP_SCRIPT}"
require_in_file "aws s3 sync" "${BACKUP_SCRIPT}"
require_in_file "CONFIRM_RESTORE=modecissions" "${RESTORE_SCRIPT}"
require_in_file "RESTORE_DELETE_PREFIX" "${RESTORE_SCRIPT}"
require_in_file "backups/*" "${RESTORE_SCRIPT}"

if [[ "${OMEGA_DR_REHEARSAL_EXECUTE:-0}" != "1" ]]; then
  log "static DR controls PASS"
  log "set OMEGA_DR_REHEARSAL_EXECUTE=1 in disposable staging to run backup + restore + smoke"
  exit 0
fi

if [[ -z "${S3_BUCKET_NAME:-}" || -z "${AWS_REGION:-}" ]]; then
  log "S3_BUCKET_NAME and AWS_REGION are required to execute a DR rehearsal"
  exit 2
fi

BACKUP_ID="${BACKUP_ID:-dr-rehearsal-$(date -u +%Y%m%dT%H%M%SZ)}"
export BACKUP_ID

log "creating backup ${BACKUP_ID}"
bash "${BACKUP_SCRIPT}"

log "restoring backup ${BACKUP_ID}"
CONFIRM_RESTORE=modecissions bash "${RESTORE_SCRIPT}"

log "running post-restore smoke"
make smoke

log "checking post-restore strict readiness"
curl -fsS --max-time 10 "${CONSOLE_URL:-http://127.0.0.1:8000}/readyz?require_data=1" >/dev/null

check_live_llm_if_required

log "PASS"
