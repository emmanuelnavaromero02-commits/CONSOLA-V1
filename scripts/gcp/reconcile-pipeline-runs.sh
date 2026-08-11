#!/usr/bin/env bash
# Apply one immutable, backup-bound pipeline-run reconciliation as an atomic CAS.
set -Eeuo pipefail
set +x
umask 077

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: reconcile-pipeline-runs.sh must run through sudo" >&2
  exit 10
fi

MANIFEST_URI="${1:-}"
MANIFEST_GENERATION="${2:-}"
MANIFEST_SIZE_BYTES="${3:-}"
MANIFEST_SHA256="${4:-}"
EXPECTED_COUNT="${5:-}"
CHANGE_ID="${6:-}"
HELPER_REF="${7:-}"
CURRENT_REF="${8:-}"
BACKUP_MANIFEST_URI="${9:-}"
BACKUP_MANIFEST_GENERATION="${10:-}"
BACKUP_MANIFEST_SIZE_BYTES="${11:-}"
BACKUP_MANIFEST_SHA256="${12:-}"
BACKUP_BUCKET="${13:-}"
BACKUP_POLICY_SHA256="${14:-}"
PROJECT_ID="${15:-}"
SOURCE_BUCKET="${16:-}"
COMPOSE_PROJECT="${17:-}"
APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
CURRENT_LINK="${APP_ROOT}/current"
SHARED_ROOT="${APP_ROOT}/shared"
PREDEPLOY_ATTESTATION="${SHARED_ROOT}/predeploy-backup.json"
SAFE_IO="${OMEGA_GCP_SAFE_IO:-}"
WORKDIR=""

emit() {
  local name="$1" status="$2" evidence="${3:-}"
  evidence="${evidence//$'\t'/ }"
  evidence="${evidence//$'\r'/ }"
  evidence="${evidence//$'\n'/ }"
  printf 'OMEGA_GCP_RECONCILE_CHECK\t%s\t%s\t%s\n' "$name" "$status" "$evidence"
}

fail() {
  emit "$1" "FAIL" "${2:-}"
  exit "${3:-20}"
}

cleanup() {
  local rc=$?
  trap - EXIT
  if [[ -n "$WORKDIR" && "$WORKDIR" == /tmp/omega-gcp-run-reconcile.* ]]; then
    rm -rf -- "$WORKDIR"
  fi
  exit "$rc"
}
trap cleanup EXIT

if [[ ! "$CHANGE_ID" =~ ^[a-z0-9][a-z0-9._-]{2,127}$ || \
      ! "$HELPER_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$CURRENT_REF" =~ ^[0-9a-f]{40}$ ]]; then
  fail "reconciliation identity" "change id or release refs are invalid" 20
fi
if [[ ! "$SOURCE_BUCKET" =~ ^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$ || \
      "$MANIFEST_URI" != "gs://${SOURCE_BUCKET}/pipeline-run-reconciliations/"* ]]; then
  fail "reconciliation manifest URI" "manifest is outside the canonical immutable prefix" 20
fi
EXPECTED_MANIFEST_SUFFIX="/pipeline-run-reconciliations/${CHANGE_ID}/${MANIFEST_SHA256}.json"
if [[ "$MANIFEST_URI" != *"$EXPECTED_MANIFEST_SUFFIX" || \
      ! "$MANIFEST_GENERATION" =~ ^[1-9][0-9]*$ || \
      ! "$MANIFEST_SIZE_BYTES" =~ ^[1-9][0-9]*$ || \
      ! "$MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "reconciliation manifest identity" "URI/generation/size/checksum is invalid" 20
fi
if [[ ! "$EXPECTED_COUNT" =~ ^[1-9][0-9]{0,3}$ ]] || \
   (( EXPECTED_COUNT > 1000 )); then
  fail "reconciliation count" "expected count must be explicit and bounded" 20
fi
if [[ ! "$BACKUP_BUCKET" =~ ^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$ || \
      "$BACKUP_MANIFEST_URI" != "gs://${BACKUP_BUCKET}/_omega_backups/"*"/manifest.json" || \
      ! "$BACKUP_MANIFEST_GENERATION" =~ ^[1-9][0-9]*$ || \
      ! "$BACKUP_MANIFEST_SIZE_BYTES" =~ ^[1-9][0-9]*$ || \
      ! "$BACKUP_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ || \
      ! "$BACKUP_POLICY_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  fail "pre-deploy backup identity" "exact backup manifest inputs are invalid" 20
fi
if [[ ! "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ || \
      ! "$COMPOSE_PROJECT" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$ ]]; then
  fail "canonical target" "project or Compose identity is invalid" 20
fi
if [[ "$SAFE_IO" != /* || ! -x "$SAFE_IO" ]]; then
  fail "safe I/O helper" "controller-owned helper is unavailable" 20
fi

exec 9>"/var/lock/omega-gcp-day2.lock"
if ! flock -n 9; then
  fail "exclusive day-2 lock" "another release/backup operation is active" 21
fi
WORKDIR="$(mktemp -d /tmp/omega-gcp-run-reconcile.XXXXXX)"
chmod 0700 "$WORKDIR"
MANIFEST="${WORKDIR}/reconciliation.json"
BACKUP_MANIFEST="${WORKDIR}/backup-manifest.json"
SQL_FILE="${WORKDIR}/reconcile.sql"

CURRENT_REAL="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
if [[ "$CURRENT_REAL" != "${APP_ROOT}/releases/${CURRENT_REF}" || \
      ! -d "$CURRENT_REAL" ]]; then
  fail "current release identity" "runtime changed after the pre-deploy backup" 22
fi

"$SAFE_IO" gcs-download --uri "$MANIFEST_URI" \
  --generation "$MANIFEST_GENERATION" --size "$MANIFEST_SIZE_BYTES" \
  --sha256 "$MANIFEST_SHA256" --output "$MANIFEST" >/dev/null || \
  fail "reconciliation manifest download" "exact immutable generation did not verify" 22
"$SAFE_IO" gcs-download --uri "$BACKUP_MANIFEST_URI" \
  --generation "$BACKUP_MANIFEST_GENERATION" \
  --size "$BACKUP_MANIFEST_SIZE_BYTES" --sha256 "$BACKUP_MANIFEST_SHA256" \
  --output "$BACKUP_MANIFEST" >/dev/null || \
  fail "pre-deploy backup readback" "exact backup generation did not verify" 22

# This validator emits only SQL whose inputs are a strictly validated JSON
# document encoded as base64. No row identity or evidence is printed.
if ! python3 - "$MANIFEST" "$BACKUP_MANIFEST" "$PREDEPLOY_ATTESTATION" \
  "$EXPECTED_COUNT" "$CHANGE_ID" "$HELPER_REF" "$CURRENT_REF" \
  "$MANIFEST_URI" "$MANIFEST_GENERATION" "$MANIFEST_SIZE_BYTES" \
  "$MANIFEST_SHA256" "$BACKUP_MANIFEST_URI" "$BACKUP_MANIFEST_GENERATION" \
  "$BACKUP_MANIFEST_SIZE_BYTES" "$BACKUP_MANIFEST_SHA256" "$BACKUP_BUCKET" \
  "$BACKUP_POLICY_SHA256" "$PROJECT_ID" >"$SQL_FILE" <<'PY'
import base64
import json
import os
import pathlib
import re
import stat
import sys
import uuid
from datetime import datetime, timedelta, timezone


SCHEMA = "omega.pipeline-run-reconciliation/v1"
RUN_KEYS = {
    "run_id",
    "tenant_id",
    "workspace_id",
    "expected_status",
    "expected_started_at",
    "expected_fencing_token",
    "target_status",
    "reason",
    "evidence",
}


def object_no_duplicates(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise SystemExit("reconciliation JSON repeats a key")
        output[key] = value
    return output


def load_strict(path):
    raw = pathlib.Path(path).read_bytes()
    if not 1 <= len(raw) <= 1024 * 1024:
        raise SystemExit("reconciliation JSON size is invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit("reconciliation JSON is not UTF-8") from exc
    if text.startswith("\ufeff"):
        raise SystemExit("reconciliation JSON has a BOM")
    try:
        return raw, json.loads(
            text,
            object_pairs_hook=object_no_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite JSON value")
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise SystemExit("reconciliation JSON is malformed") from exc


def timestamp(value, field):
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,6})?\+00:00",
        value,
    ) is None:
        raise SystemExit(f"{field} is not an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"{field} is invalid") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise SystemExit(f"{field} is not UTC")
    return parsed


def validate_evidence(value):
    if not isinstance(value, dict) or not value:
        raise SystemExit("reconciliation evidence is empty or invalid")
    nodes = 0

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 1000 or depth > 6:
            raise SystemExit("reconciliation evidence exceeds its bound")
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, int):
            if abs(item) > 2**63 - 1:
                raise SystemExit("reconciliation evidence integer is invalid")
            return
        if isinstance(item, float):
            if item != item or item in {float("inf"), float("-inf")}:
                raise SystemExit("reconciliation evidence number is invalid")
            return
        if isinstance(item, str):
            if len(item.encode()) > 4096 or re.search(
                r"(?i)(?:ghp_|github_pat_|bearer\s+[a-z0-9._-]{8,})", item
            ):
                raise SystemExit("reconciliation evidence string is unsafe")
            return
        if isinstance(item, list):
            if len(item) > 200:
                raise SystemExit("reconciliation evidence list is too large")
            for nested in item:
                visit(nested, depth + 1)
            return
        if isinstance(item, dict):
            if len(item) > 100:
                raise SystemExit("reconciliation evidence object is too large")
            for key, nested in item.items():
                if not isinstance(key, str) or re.fullmatch(
                    r"[a-z][a-z0-9_]{0,63}", key
                ) is None or re.search(
                    r"(?i)(?:password|secret|credential|authorization|access_token|token)",
                    key,
                ):
                    raise SystemExit("reconciliation evidence key is unsafe")
                visit(nested, depth + 1)
            return
        raise SystemExit("reconciliation evidence value is unsupported")

    visit(value, 0)


manifest_raw, payload = load_strict(sys.argv[1])
expected_count = int(sys.argv[4])
if set(payload) != {"schema", "change_id", "runs"} or payload.get("schema") != SCHEMA:
    raise SystemExit("reconciliation manifest shape/schema is invalid")
if payload.get("change_id") != sys.argv[5]:
    raise SystemExit("reconciliation change id differs")
runs = payload.get("runs")
if not isinstance(runs, list) or len(runs) != expected_count:
    raise SystemExit("reconciliation run count differs")
now = datetime.now(timezone.utc)
identities = set()
run_ids = set()
observed_times = []
for row in runs:
    if not isinstance(row, dict) or set(row) != RUN_KEYS:
        raise SystemExit("reconciliation row shape is invalid")
    run_id = row.get("run_id")
    if not isinstance(run_id, str) or not 1 <= len(run_id.encode()) <= 512 or any(
        ord(char) < 0x20 or ord(char) == 0x7f for char in run_id
    ):
        raise SystemExit("reconciliation run id is invalid")
    scope = []
    for key in ("tenant_id", "workspace_id"):
        value = row.get(key)
        try:
            parsed = uuid.UUID(value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise SystemExit("reconciliation scope is invalid") from exc
        if str(parsed) != value:
            raise SystemExit("reconciliation scope is not canonical")
        scope.append(value)
    identity = (run_id, *scope)
    if identity in identities or run_id in run_ids:
        raise SystemExit("reconciliation repeats a run identity")
    identities.add(identity)
    run_ids.add(run_id)
    if row.get("expected_status") != "running":
        raise SystemExit("reconciliation expected status is invalid")
    started = timestamp(row.get("expected_started_at"), "expected_started_at")
    token = row.get("expected_fencing_token")
    if isinstance(token, bool) or not isinstance(token, int) or not 0 <= token < 2**63 - 1:
        raise SystemExit("reconciliation fencing token is invalid")
    if row.get("target_status") not in {"failed", "blocked"}:
        raise SystemExit("reconciliation target status is invalid")
    if not isinstance(row.get("reason"), str) or re.fullmatch(
        r"[a-z][a-z0-9_]{2,63}", row["reason"]
    ) is None:
        raise SystemExit("reconciliation reason is invalid")
    validate_evidence(row.get("evidence"))
    observed = timestamp(row["evidence"].get("observed_at"), "evidence.observed_at")
    if (
        observed < started
        or observed > now + timedelta(minutes=5)
        or now - observed > timedelta(hours=24)
        or started > now + timedelta(minutes=5)
    ):
        raise SystemExit("reconciliation timestamps are inconsistent")
    observed_times.append(observed)

if len(manifest_raw) != int(sys.argv[10]):
    raise SystemExit("reconciliation manifest size changed")
import hashlib
if hashlib.sha256(manifest_raw).hexdigest() != sys.argv[11]:
    raise SystemExit("reconciliation manifest checksum changed")

backup = json.load(open(sys.argv[2], encoding="utf-8"))
attestation_path = pathlib.Path(sys.argv[3])
info = attestation_path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
    raise SystemExit("server-owned pre-deploy backup attestation is unsafe")
attestation = json.load(open(attestation_path, encoding="utf-8"))
attestation_keys = {
    "schema_version", "state", "backup_id", "source_ref", "candidate_ref",
    "manifest_uri", "manifest_generation", "manifest_size_bytes",
    "manifest_sha256", "manifest_created_at", "attested_at", "lakehouse_bucket",
    "backup_bucket", "backup_policy_sha256", "database_restore_settings",
    "database_restore_settings_sha256",
}
if set(attestation) != attestation_keys or attestation.get("schema_version") != 1:
    raise SystemExit("pre-deploy backup attestation shape is invalid")
if backup.get("schema_version") != 4 or backup.get("complete") is not True:
    raise SystemExit("pre-deploy backup manifest is incomplete")
backup_id = backup.get("backup_id")
if not isinstance(backup_id, str) or re.fullmatch(
    r"[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9.-]{0,80}", backup_id
) is None:
    raise SystemExit("pre-deploy backup id is invalid")
backup_storage = backup.get("backup_storage", {})
source_release = backup.get("source_release", {})
expected_backup_uri = f"gs://{sys.argv[16]}/_omega_backups/{backup_id}/manifest.json"
if (
    sys.argv[12] != expected_backup_uri
    or backup_storage.get("bucket") != sys.argv[16]
    or backup_storage.get("policy_sha256") != sys.argv[17]
    or source_release.get("deploy_ref") != sys.argv[7]
):
    raise SystemExit("pre-deploy backup manifest identity differs")
if (
    attestation.get("state") != "ready"
    or attestation.get("backup_id") != backup_id
    or attestation.get("source_ref") != sys.argv[7]
    or attestation.get("candidate_ref") != sys.argv[6]
    or attestation.get("manifest_uri") != sys.argv[12]
    or attestation.get("manifest_generation") != sys.argv[13]
    or attestation.get("manifest_size_bytes") != int(sys.argv[14])
    or attestation.get("manifest_sha256") != sys.argv[15]
    or attestation.get("manifest_created_at") != backup.get("created_at")
    or attestation.get("backup_bucket") != sys.argv[16]
    or attestation.get("backup_policy_sha256") != sys.argv[17]
    or attestation.get("database_restore_settings")
       != backup.get("database_restore_settings")
    or attestation.get("database_restore_settings_sha256")
       != backup.get("database_restore_settings_sha256")
):
    raise SystemExit("pre-deploy backup is not the exact ready attestation")
attested_at = datetime.fromisoformat(str(attestation.get("attested_at")).replace("Z", "+00:00"))
if attested_at.tzinfo is None or now - attested_at > timedelta(hours=4) or attested_at - now > timedelta(minutes=5):
    raise SystemExit("pre-deploy backup attestation is stale")
if any(
    observed > attested_at + timedelta(minutes=5)
    or attested_at - observed > timedelta(hours=24)
    for observed in observed_times
):
    raise SystemExit("reconciliation evidence is not fresh for the attested backup")


def sql_text(value):
    return "'" + str(value).replace("'", "''") + "'"


encoded = base64.b64encode(manifest_raw).decode("ascii")
manifest_uri = sql_text(sys.argv[8])
manifest_generation = sql_text(sys.argv[9])
manifest_sha256 = sql_text(sys.argv[11])
change_id = sql_text(sys.argv[5])
print(f"""BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '120s';
SET LOCAL idle_in_transaction_session_timeout = '120s';
SET LOCAL row_security = off;
CREATE TEMP TABLE omega_reconciliation_input (
  ordinal integer PRIMARY KEY,
  run_id text NOT NULL,
  tenant_id uuid NOT NULL,
  workspace_id uuid NOT NULL,
  expected_status text NOT NULL CHECK (expected_status = 'running'),
  expected_started_at timestamptz NOT NULL,
  expected_fencing_token bigint NOT NULL CHECK (expected_fencing_token >= 0),
  target_status text NOT NULL CHECK (target_status IN ('failed', 'blocked')),
  reason text NOT NULL,
  evidence jsonb NOT NULL
) ON COMMIT DROP;
CREATE TEMP TABLE omega_reconciliation_meta (
  change_id text NOT NULL,
  manifest_uri text NOT NULL,
  manifest_generation text NOT NULL,
  manifest_size_bytes bigint NOT NULL,
  manifest_sha256 text NOT NULL
) ON COMMIT DROP;
INSERT INTO omega_reconciliation_meta VALUES (
  {change_id}, {manifest_uri}, {manifest_generation}, {len(manifest_raw)},
  {manifest_sha256}
);
WITH document AS (
  SELECT convert_from(decode('{encoded}', 'base64'), 'UTF8')::jsonb AS value
)
INSERT INTO omega_reconciliation_input
SELECT
  (item.ordinality - 1)::integer,
  item.value->>'run_id',
  (item.value->>'tenant_id')::uuid,
  (item.value->>'workspace_id')::uuid,
  item.value->>'expected_status',
  (item.value->>'expected_started_at')::timestamptz,
  (item.value->>'expected_fencing_token')::bigint,
  item.value->>'target_status',
  item.value->>'reason',
  item.value->'evidence'
FROM document, LATERAL jsonb_array_elements(document.value->'runs')
  WITH ORDINALITY AS item(value, ordinality);
DO $omega_reconcile$
DECLARE
  expected_count constant integer := {expected_count};
  actual_count integer;
  locked_count integer;
  matched_count integer;
  updated_count integer;
  verified_count integer;
BEGIN
  SELECT count(*) INTO actual_count FROM omega_reconciliation_input;
  IF actual_count <> expected_count THEN
    RAISE EXCEPTION 'reconciliation input count mismatch';
  END IF;
  PERFORM 1
    FROM pipeline_runs AS pr
    JOIN omega_reconciliation_input AS input
      ON pr.run_id = input.run_id
     AND pr.tenant_id = input.tenant_id
     AND pr.workspace_id = input.workspace_id
   ORDER BY pr.run_id
   FOR UPDATE OF pr;
  GET DIAGNOSTICS locked_count = ROW_COUNT;
  IF locked_count <> expected_count THEN
    RAISE EXCEPTION 'reconciliation target identity mismatch';
  END IF;
  SELECT count(*) INTO matched_count
    FROM pipeline_runs AS pr
    JOIN omega_reconciliation_input AS input
      ON pr.run_id = input.run_id
     AND pr.tenant_id = input.tenant_id
     AND pr.workspace_id = input.workspace_id
   WHERE pr.status = input.expected_status
     AND pr.started_at = input.expected_started_at
     AND pr.fencing_token = input.expected_fencing_token
     AND (pr.lease_expires_at IS NULL OR pr.lease_expires_at <= clock_timestamp())
     AND (pr.extra IS NULL OR jsonb_typeof(pr.extra) = 'object')
     AND NOT (COALESCE(pr.extra, '{{}}'::jsonb) ? 'omega_release_reconciliation');
  IF matched_count <> expected_count THEN
    RAISE EXCEPTION 'reconciliation compare-and-swap precondition mismatch';
  END IF;
  UPDATE pipeline_runs AS pr
     SET status = input.target_status,
         finished_at = COALESCE(pr.finished_at, transaction_timestamp()),
         error_message = COALESCE(
           pr.error_message, 'release_reconciliation:' || input.reason
         ),
         heartbeat_at = NULL,
         lease_expires_at = NULL,
         fencing_token = pr.fencing_token + 1,
         extra = COALESCE(pr.extra, '{{}}'::jsonb) || jsonb_build_object(
           'omega_release_reconciliation', jsonb_build_object(
             'schema', '{SCHEMA}',
             'change_id', meta.change_id,
             'manifest_uri', meta.manifest_uri,
             'manifest_generation', meta.manifest_generation,
             'manifest_size_bytes', meta.manifest_size_bytes,
             'manifest_sha256', meta.manifest_sha256,
             'prior_status', input.expected_status,
             'prior_fencing_token', input.expected_fencing_token,
             'target_status', input.target_status,
             'reason', input.reason,
             'evidence', input.evidence,
             'reconciled_at', transaction_timestamp()
           )
         )
    FROM omega_reconciliation_input AS input
    CROSS JOIN omega_reconciliation_meta AS meta
   WHERE pr.run_id = input.run_id
     AND pr.tenant_id = input.tenant_id
     AND pr.workspace_id = input.workspace_id
     AND pr.status = input.expected_status
     AND pr.started_at = input.expected_started_at
     AND pr.fencing_token = input.expected_fencing_token
     AND (pr.lease_expires_at IS NULL OR pr.lease_expires_at <= clock_timestamp());
  GET DIAGNOSTICS updated_count = ROW_COUNT;
  IF updated_count <> expected_count THEN
    RAISE EXCEPTION 'reconciliation atomic update count mismatch';
  END IF;
  SELECT count(*) INTO verified_count
    FROM pipeline_runs AS pr
    JOIN omega_reconciliation_input AS input
      ON pr.run_id = input.run_id
     AND pr.tenant_id = input.tenant_id
     AND pr.workspace_id = input.workspace_id
    CROSS JOIN omega_reconciliation_meta AS meta
   WHERE pr.status = input.target_status
     AND pr.started_at = input.expected_started_at
     AND pr.finished_at IS NOT NULL
     AND pr.heartbeat_at IS NULL
     AND pr.lease_expires_at IS NULL
     AND pr.fencing_token = input.expected_fencing_token + 1
     AND pr.extra->'omega_release_reconciliation'->>'schema' = '{SCHEMA}'
     AND pr.extra->'omega_release_reconciliation'->>'change_id' = meta.change_id
     AND pr.extra->'omega_release_reconciliation'->>'manifest_uri' = meta.manifest_uri
     AND pr.extra->'omega_release_reconciliation'->>'manifest_generation' = meta.manifest_generation
     AND pr.extra->'omega_release_reconciliation'->>'manifest_size_bytes' = meta.manifest_size_bytes::text
     AND pr.extra->'omega_release_reconciliation'->>'manifest_sha256' = meta.manifest_sha256
     AND pr.extra->'omega_release_reconciliation'->>'prior_status' = input.expected_status
     AND (pr.extra->'omega_release_reconciliation'->>'prior_fencing_token')::bigint = input.expected_fencing_token
     AND pr.extra->'omega_release_reconciliation'->>'target_status' = input.target_status
     AND pr.extra->'omega_release_reconciliation'->>'reason' = input.reason
     AND pr.extra->'omega_release_reconciliation'->'evidence' = input.evidence
     AND pr.extra->'omega_release_reconciliation' ? 'reconciled_at';
  IF verified_count <> expected_count THEN
    RAISE EXCEPTION 'reconciliation readback count mismatch';
  END IF;
END
$omega_reconcile$;
SELECT json_build_object(
  'status', 'PASS',
  'change_id', meta.change_id,
  'manifest_uri', meta.manifest_uri,
  'manifest_generation', meta.manifest_generation,
  'manifest_size_bytes', meta.manifest_size_bytes,
  'manifest_sha256', meta.manifest_sha256,
  'reconciled_count', count(*),
  'failed_count', count(*) FILTER (WHERE input.target_status = 'failed'),
  'blocked_count', count(*) FILTER (WHERE input.target_status = 'blocked'),
  'secrets_included', false
)::text
FROM omega_reconciliation_input AS input
CROSS JOIN omega_reconciliation_meta AS meta
GROUP BY meta.change_id, meta.manifest_uri, meta.manifest_generation,
         meta.manifest_size_bytes, meta.manifest_sha256;
COMMIT;
""")
PY
then
  fail "manifest and backup validation" "strict manifest/backup binding failed" 23
fi
chmod 0600 "$SQL_FILE"

ATTESTATION_SHA_BEFORE="$(sha256sum "$PREDEPLOY_ATTESTATION" | awk '{print $1}')"
if [[ ! "$ATTESTATION_SHA_BEFORE" =~ ^[0-9a-f]{64}$ ]]; then
  fail "pre-deploy backup attestation" "cannot hash ready attestation" 23
fi

mapfile -t POSTGRES_CONTAINERS < <(
  docker ps --filter 'name=^/mode_postgres$' --format '{{.Names}}'
)
if (( ${#POSTGRES_CONTAINERS[@]} != 1 )) || \
   [[ "${POSTGRES_CONTAINERS[0]}" != "mode_postgres" ]]; then
  fail "operational database identity" "expected one running mode_postgres" 24
fi
POSTGRES_STATE="$(docker inspect mode_postgres --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || true)"
if [[ "$POSTGRES_STATE" != "running|healthy|${COMPOSE_PROJECT}" ]]; then
  fail "operational database identity" "database state/health/project differs" 24
fi

if ! RESULT="$(
  docker exec -i mode_postgres \
    psql -X -qAt -v ON_ERROR_STOP=1 -U postgres -d modecissions <"$SQL_FILE"
)"; then
  fail "atomic pipeline-run CAS" "transaction rejected; no transition committed" 25
fi

if [[ "$(sha256sum "$PREDEPLOY_ATTESTATION" | awk '{print $1}')" != "$ATTESTATION_SHA_BEFORE" || \
      "$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)" != "$CURRENT_REAL" ]]; then
  fail "post-CAS release/backup identity" "attestation or current release changed" 26
fi
if ! RESULT="$(python3 - "$RESULT" "$EXPECTED_COUNT" "$CHANGE_ID" \
  "$MANIFEST_URI" "$MANIFEST_GENERATION" "$MANIFEST_SIZE_BYTES" \
  "$MANIFEST_SHA256" <<'PY'
import json
import sys

try:
    payload = json.loads(sys.argv[1])
except json.JSONDecodeError as exc:
    raise SystemExit("database result is malformed") from exc
expected = {
    "status": "PASS",
    "change_id": sys.argv[3],
    "manifest_uri": sys.argv[4],
    "manifest_generation": sys.argv[5],
    "manifest_size_bytes": int(sys.argv[6]),
    "manifest_sha256": sys.argv[7],
    "reconciled_count": int(sys.argv[2]),
    "secrets_included": False,
}
if set(payload) != set(expected) | {"failed_count", "blocked_count"}:
    raise SystemExit("database result shape differs")
if any(payload.get(key) != value for key, value in expected.items()):
    raise SystemExit("database result identity differs")
if (
    not isinstance(payload.get("failed_count"), int)
    or not isinstance(payload.get("blocked_count"), int)
    or payload["failed_count"] + payload["blocked_count"] != int(sys.argv[2])
):
    raise SystemExit("database result distribution differs")
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
PY
)"; then
  fail "atomic pipeline-run readback" "exact terminal readback failed" 26
fi

emit "immutable reconciliation manifest" "PASS" "exact generation/size/sha256 verified"
emit "pre-deploy backup attestation" "PASS" "exact fresh ready attestation remained unconsumed"
emit "atomic pipeline-run CAS" "PASS" "exact count transitioned; lease and fencing predicates enforced"
emit "pipeline-run history" "PASS" "no rows deleted; audit JSON persisted; exact readback passed"
printf 'OMEGA_GCP_RECONCILE_JSON=%s\n' "$RESULT"
