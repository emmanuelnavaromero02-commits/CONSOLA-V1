#!/usr/bin/env bash
# Deprecated unsafe GCP release entrypoint.
#
# There is exactly one production deployment path. The canonical controller
# hydrates runtime secrets atomically, quiesces every writer before its verified
# database backup, keeps the databases exclusive through migrations, and stops
# a failed candidate before rollback restore. Maintaining those invariants in a
# second on-VM controller caused the two paths to drift.
set -Eeuo pipefail
set +x

printf '%s\n' \
  '[day2] ERROR: scripts/gcp/day2-release.sh is disabled and performs no action.' \
  '[day2] Use scripts/gcp/gcp-canonical-deploy.sh <target-tag> <deploy-ref-40hex> from the operator workstation.' \
  >&2
exit 64
