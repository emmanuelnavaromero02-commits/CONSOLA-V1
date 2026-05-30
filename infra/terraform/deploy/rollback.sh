#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

REPO_DIR="${REPO_DIR:-/opt/modecissions}"
DEPLOY_DIR="${DEPLOY_DIR:-${REPO_DIR}/infra/terraform/deploy}"
cd "${DEPLOY_DIR}"

if [ ! -f .env ]; then
  echo "ERROR: .env no existe en ${DEPLOY_DIR}. Ejecuta ${REPO_DIR}/scripts/aws-entrypoint.sh." >&2
  exit 1
fi

ROLLBACK_ARG="${1:-}"
set -a
# shellcheck disable=SC1091
source .env
set +a

TARGET_TAG="${ROLLBACK_ARG:-${IMAGE_TAG:-}}"
if [[ -z "${TARGET_TAG}" || "${TARGET_TAG}" == "latest" || ! "${TARGET_TAG}" =~ ^v[0-9] ]]; then
  echo "ERROR: rollback requires immutable release tag, e.g. bash rollback.sh v1.0.0-rc2" >&2
  exit 2
fi

if [[ "${RUN_BACKUP_BEFORE_ROLLBACK:-1}" == "1" ]]; then
  BACKUP_ID="pre-rollback-$(date -u +%Y%m%dT%H%M%SZ)-${TARGET_TAG}" bash backup.sh
fi

BACKUP_ENV=".env.rollback.$(date -u +%Y%m%dT%H%M%SZ).bak"
cp .env "${BACKUP_ENV}"

python3 - "$TARGET_TAG" <<'PY'
from pathlib import Path
import sys

target = sys.argv[1]
path = Path(".env")
lines = path.read_text(encoding="utf-8").splitlines()
seen = {"DEPLOY_REF": False, "IMAGE_TAG": False}
out = []
for line in lines:
    if line.startswith("DEPLOY_REF="):
        out.append(f"DEPLOY_REF={target}")
        seen["DEPLOY_REF"] = True
    elif line.startswith("IMAGE_TAG="):
        out.append(f"IMAGE_TAG={target}")
        seen["IMAGE_TAG"] = True
    else:
        out.append(line)
for key, did_see in seen.items():
    if not did_see:
        out.append(f"{key}={target}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

echo "[rollback] .env updated to ${TARGET_TAG}; previous copy: ${BACKUP_ENV}"
bash update.sh

set -a
# shellcheck disable=SC1091
source .env
set +a

curl -fsS --max-time 10 "${CONSOLE_URL:?CONSOLE_URL is required}/healthz" >/dev/null
curl -fsS --max-time 10 "${CONSOLE_URL}/readyz" >/dev/null

if [[ "${RUN_SMOKE:-1}" == "1" ]]; then
  bash "${REPO_DIR}/scripts/smoke_test.sh"
fi

echo "[rollback] Complete: ${TARGET_TAG}"
