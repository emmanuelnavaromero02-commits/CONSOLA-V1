#!/usr/bin/env bash
set -Eeuo pipefail
set +x

printf '%s\n' \
  '[day2] ERROR: scripts/gcp/day2-release.sh is disabled and performs no action.' \
  '[day2] Use scripts/gcp/gcp-canonical-deploy.sh <target-tag> <deploy-ref-40hex> from the operator workstation.' \
  >&2
exit 64
