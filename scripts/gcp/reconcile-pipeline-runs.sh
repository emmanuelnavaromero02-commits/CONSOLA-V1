#!/usr/bin/env bash
# Reconcile one immutable stale-run manifest with the canonical audited engine.
set -Eeuo pipefail
set +x
umask 077

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "ERROR: reconcile-pipeline-runs.sh must run through sudo" >&2
  exit 10
fi
if [[ "$#" -ne 26 ]]; then
  echo "ERROR: reconcile-pipeline-runs.sh requires one exact 26-field contract" >&2
  exit 10
fi

MANIFEST_URI="$1"
MANIFEST_GENERATION="$2"
MANIFEST_SIZE_BYTES="$3"
MANIFEST_SHA256="$4"
EXPECTED_COUNT="$5"
CHANGE_ID="$6"
HELPER_REF="$7"
CURRENT_REF="$8"
BACKUP_MANIFEST_URI="$9"
BACKUP_MANIFEST_GENERATION="${10}"
BACKUP_MANIFEST_SIZE_BYTES="${11}"
BACKUP_MANIFEST_SHA256="${12}"
BACKUP_BUCKET="${13}"
BACKUP_POLICY_SHA256="${14}"
PROJECT_ID="${15}"
SOURCE_BUCKET="${16}"
COMPOSE_PROJECT="${17}"
ROUTING_ATTESTATION_URI="${18}"
ROUTING_ATTESTATION_GENERATION="${19}"
ROUTING_ATTESTATION_SIZE_BYTES="${20}"
ROUTING_ATTESTATION_SHA256="${21}"
INSTANCE_ID="${22}"
PUBLISHED_RELEASE_TAG="${23}"
PUBLISHED_MANIFEST_SHA256="${24}"
PUBLISHED_TAG_OBJECT_SHA="${25}"
HANDOFF_TIMEOUT_SECONDS="${26}"

APP_ROOT="${OMEGA_GCP_APP_ROOT:-/opt/modecissions}"
CURRENT_LINK="${APP_ROOT}/current"
SHARED_ROOT="${APP_ROOT}/shared"
SHARED_ENV="${SHARED_ROOT}/infra.env"
GCP_RUNTIME_COMPOSE="${SHARED_ROOT}/docker-compose.gcp.yml"
LEGACY_IMAGE_COMPOSE="${SHARED_ROOT}/docker-compose.legacy-images.gcp.yml"
STATE_LINK="${SHARED_ROOT}/runtime-state"
RUNTIME_PROVENANCE="${STATE_LINK}/runtime-provenance.json"
PREDEPLOY_ATTESTATION="${SHARED_ROOT}/predeploy-backup.json"
OPERATION_MARKER="${SHARED_ROOT}/operation-state.json"
WATCHDOG="/usr/local/sbin/omega-operation-watchdog"
REMOTE_ROOT="${OMEGA_GCP_REMOTE_ROOT:-}"
SAFE_IO="${OMEGA_GCP_SAFE_IO:-}"
CANONICAL_ENGINE="${REMOTE_ROOT}/reconcile_pipeline_runs.py"
RUNTIME_CONTRACT="${REMOTE_ROOT}/runtime_contract.py"
CANDIDATE_WATCHDOG="${REMOTE_ROOT}/operation-watchdog.sh"
METADATA_FIREWALL="${REMOTE_ROOT}/metadata-firewall.sh"
WORKDIR=""
FENCE_ACTIVE=0
HANDOFF_READY=0
CAS_COMMITTED=0
APPLY_STARTED=0
COMPOSE_READY=0
RUNNING_BEFORE=()
WRITER_SERVICES=(airflow-scheduler console workspace refinement vault mcp-infra airflow replicon hubspot salesforce banxico inegi sec-edgar sap-hcm sap-successfactors sap-s4hana superset)
ONE_SHOT_MUTATORS=(airflow-init minio-init postgres_dev_seed superset-init)
MUTATING_SERVICES=("${WRITER_SERVICES[@]}" "${ONE_SHOT_MUTATORS[@]}")

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

running_ids_for_service() {
  docker ps -q \
    --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
    --filter "label=com.docker.compose.service=$1"
}

wait_for_exact_restore() {
  local service ids state_health scheduler_ids project_scheduler_ids all_green expected
  for _ in $(seq 1 90); do
    all_green=1
    for service in "${MUTATING_SERVICES[@]}"; do
      ids="$(running_ids_for_service "$service")"
      expected=0
      for running_service in "${RUNNING_BEFORE[@]}"; do
        if [[ "$running_service" == "$service" ]]; then
          expected=1
          break
        fi
      done
      if [[ "$expected" == "1" ]]; then
        if [[ "$(grep -c . <<<"$ids" || true)" != "1" ]]; then
          all_green=0
          break
        fi
        state_health="$(docker inspect "$ids" --format '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' 2>/dev/null || true)"
        if [[ "$state_health" != "true healthy" ]]; then
          all_green=0
          break
        fi
      elif [[ -n "$ids" ]]; then
        all_green=0
        break
      fi
    done
    scheduler_ids="$(docker ps -q --filter 'label=com.docker.compose.service=airflow-scheduler')"
    project_scheduler_ids="$(running_ids_for_service airflow-scheduler)"
    if [[ "$all_green" == "1" && \
          "$(grep -c . <<<"$scheduler_ids" || true)" == "1" && \
          "$scheduler_ids" == "$project_scheduler_ids" ]]; then
      return 0
    fi
    sleep 2
  done
  return 1
}

restore_after_failure() {
  local rc=$? restored=1
  trap - EXIT
  set +e
  if [[ "$FENCE_ACTIVE" == "1" && "$APPLY_STARTED" != "1" && \
        "$CAS_COMMITTED" != "1" ]]; then
    if [[ "$COMPOSE_READY" != "1" || "${#RUNNING_BEFORE[@]}" -eq 0 ]]; then
      restored=0
    elif ! "${COMPOSE[@]}" start "${RUNNING_BEFORE[@]}" >/dev/null; then
      restored=0
    elif ! wait_for_exact_restore; then
      restored=0
    fi
    if [[ "$restored" == "1" ]]; then
      rm -f -- "$OPERATION_MARKER"
      "$SAFE_IO" fsync-dir "$SHARED_ROOT" >/dev/null 2>&1 || restored=0
    fi
    if [[ "$restored" == "1" ]]; then
      "$WATCHDOG" disarm "$$" "$OPERATION_MARKER" >/dev/null 2>&1 || restored=0
    fi
    if [[ "$restored" == "1" ]]; then
      emit "failure recovery" "PASS" "exact previously-active GCP host-local service set restored; scheduler=1"
    else
      "${COMPOSE[@]}" stop --timeout 30 "${MUTATING_SERVICES[@]}" >/dev/null 2>&1 || :
      emit "failure recovery" "FAIL" "exact restore failed; durable marker retained and host-local writers remain fenced"
      rc=90
    fi
  elif [[ "$FENCE_ACTIVE" == "1" && "$HANDOFF_READY" != "1" && \
          ( "$APPLY_STARTED" == "1" || "$CAS_COMMITTED" == "1" ) ]]; then
    "${COMPOSE[@]}" stop --timeout 30 "${MUTATING_SERVICES[@]}" >/dev/null 2>&1 || :
    emit "post-CAS failure fence" "PASS" "canonical CAS committed; old runtime was not restarted; durable marker retained"
  fi
  if [[ -n "$WORKDIR" && "$WORKDIR" == /tmp/omega-gcp-run-reconcile.* ]]; then
    rm -rf -- "$WORKDIR"
  fi
  exit "$rc"
}
trap restore_after_failure EXIT

if [[ ! "$CHANGE_ID" =~ ^[a-z0-9][a-z0-9._-]{2,127}$ || \
      ! "$HELPER_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$CURRENT_REF" =~ ^[0-9a-f]{40}$ || \
      ! "$INSTANCE_ID" =~ ^[1-9][0-9]{5,30}$ || \
      ! "$PUBLISHED_RELEASE_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-[0-9A-Za-z.-]+$ || \
      ! "$PUBLISHED_MANIFEST_SHA256" =~ ^sha256:[0-9a-f]{64}$ || \
      ! "$PUBLISHED_TAG_OBJECT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  fail "reconciliation identity" "change id, refs, release tag, or instance id are invalid"
fi
if [[ ! "$EXPECTED_COUNT" =~ ^[1-9][0-9]{0,3}$ || "$EXPECTED_COUNT" -gt 1000 ]]; then
  fail "reconciliation count" "expected count must be explicit and bounded"
fi
if [[ ! "$HANDOFF_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ || \
      "$HANDOFF_TIMEOUT_SECONDS" -lt 300 || "$HANDOFF_TIMEOUT_SECONDS" -gt 3600 ]]; then
  fail "handoff timeout" "timeout must be 300..3600 seconds"
fi
if [[ ! "$SOURCE_BUCKET" =~ ^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$ || \
      "$MANIFEST_URI" != "gs://${SOURCE_BUCKET}/pipeline-run-reconciliations/${CHANGE_ID}/${MANIFEST_SHA256}.json" || \
      "$ROUTING_ATTESTATION_URI" != "gs://${SOURCE_BUCKET}/external-routing-scheduler-attestations/${HELPER_REF}/${ROUTING_ATTESTATION_SHA256}.json" ]]; then
  fail "immutable input URIs" "manifest or routing/scheduler attestation prefix differs"
fi
for value in "$MANIFEST_GENERATION" "$MANIFEST_SIZE_BYTES" \
  "$BACKUP_MANIFEST_GENERATION" "$BACKUP_MANIFEST_SIZE_BYTES" \
  "$ROUTING_ATTESTATION_GENERATION" "$ROUTING_ATTESTATION_SIZE_BYTES"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || fail "immutable input identity" "generation or size is invalid"
done
for value in "$MANIFEST_SHA256" "$BACKUP_MANIFEST_SHA256" \
  "$BACKUP_POLICY_SHA256" "$ROUTING_ATTESTATION_SHA256"; do
  [[ "$value" =~ ^[0-9a-f]{64}$ ]] || fail "immutable input identity" "checksum is invalid"
done
if [[ ! "$BACKUP_BUCKET" =~ ^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$ || \
      "$BACKUP_MANIFEST_URI" != "gs://${BACKUP_BUCKET}/_omega_backups/"*"/manifest.json" || \
      ! "$PROJECT_ID" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ || \
      ! "$COMPOSE_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  fail "canonical target" "backup, project, or Compose identity is invalid"
fi
if [[ "$SAFE_IO" != /* || ! -x "$SAFE_IO" || "$REMOTE_ROOT" != /run/omega-gcp-remote.* || \
      ! -x "$CANONICAL_ENGINE" || ! -x "$RUNTIME_CONTRACT" || \
      ! -x "$CANDIDATE_WATCHDOG" || ! -x "$METADATA_FIREWALL" ]]; then
  fail "exact candidate helpers" "controller-owned canonical helpers are unavailable"
fi

exec 9>"/var/lock/omega-gcp-day2.lock"
flock -n 9 || fail "exclusive day-2 lock" "another canonical operation is active" 21
if [[ -e "$OPERATION_MARKER" ]]; then
  fail "durable operation fence" "an incomplete operation marker already exists" 21
fi
WORKDIR="$(mktemp -d /tmp/omega-gcp-run-reconcile.XXXXXX)"
chmod 0700 "$WORKDIR"
MANIFEST="${WORKDIR}/reconciliation.json"
BACKUP_MANIFEST="${WORKDIR}/backup-manifest.json"
ROUTING_ATTESTATION="${WORKDIR}/routing-scheduler-attestation.json"
RUNNER_ENV="${WORKDIR}/runner.env"

CURRENT_REAL="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
if [[ "$CURRENT_REAL" != "${APP_ROOT}/releases/${CURRENT_REF}" || ! -d "$CURRENT_REAL" ]]; then
  fail "current release identity" "runtime changed after the pre-deploy backup" 22
fi
CURRENT_VERSION="$(tr -d '\r\n' < "${CURRENT_REAL}/VERSION" 2>/dev/null || true)"
BASE_COMPOSE="${CURRENT_REAL}/infra/docker-compose.yml"
if [[ -z "$CURRENT_VERSION" || ! -s "$BASE_COMPOSE" || ! -s "$SHARED_ENV" || \
      ! -s "$GCP_RUNTIME_COMPOSE" || ! -s "$RUNTIME_PROVENANCE" ]]; then
  fail "current runtime inputs" "release, env, overlay, or provenance is missing" 22
fi

"$SAFE_IO" gcs-download --uri "$MANIFEST_URI" --generation "$MANIFEST_GENERATION" \
  --size "$MANIFEST_SIZE_BYTES" --sha256 "$MANIFEST_SHA256" --output "$MANIFEST" >/dev/null || \
  fail "reconciliation manifest" "exact immutable generation did not verify" 22
"$SAFE_IO" gcs-download --uri "$BACKUP_MANIFEST_URI" \
  --generation "$BACKUP_MANIFEST_GENERATION" --size "$BACKUP_MANIFEST_SIZE_BYTES" \
  --sha256 "$BACKUP_MANIFEST_SHA256" --output "$BACKUP_MANIFEST" >/dev/null || \
  fail "pre-deploy backup" "exact immutable backup generation did not verify" 22
"$SAFE_IO" gcs-download --uri "$ROUTING_ATTESTATION_URI" \
  --generation "$ROUTING_ATTESTATION_GENERATION" --size "$ROUTING_ATTESTATION_SIZE_BYTES" \
  --sha256 "$ROUTING_ATTESTATION_SHA256" --output "$ROUTING_ATTESTATION" >/dev/null || \
  fail "external routing/scheduler attestation" "exact immutable generation did not verify" 22

python3 - "$MANIFEST" "$EXPECTED_COUNT" "$CHANGE_ID" "$MANIFEST_SHA256" \
  "$BACKUP_MANIFEST" "$PREDEPLOY_ATTESTATION" "$CURRENT_REF" "$HELPER_REF" \
  "$BACKUP_MANIFEST_URI" "$BACKUP_MANIFEST_GENERATION" \
  "$BACKUP_MANIFEST_SIZE_BYTES" "$BACKUP_MANIFEST_SHA256" "$BACKUP_BUCKET" \
  "$BACKUP_POLICY_SHA256" "$ROUTING_ATTESTATION" "$INSTANCE_ID" <<'PY'
import hashlib
import json
import os
import pathlib
import stat
import sys
from datetime import datetime, timedelta, timezone

(
    manifest_path, expected_count, change_id, manifest_sha, backup_path,
    predeploy_path, current_ref, helper_ref, backup_uri, backup_generation,
    backup_size, backup_sha, backup_bucket, backup_policy, routing_path,
    instance_id,
) = sys.argv[1:]

manifest_raw = pathlib.Path(manifest_path).read_bytes()
manifest = json.loads(manifest_raw)
if (
    set(manifest) != {"schema", "change_id", "runs"}
    or manifest.get("schema") != "omega.pipeline-run-reconciliation/v1"
    or manifest.get("change_id") != change_id
    or not isinstance(manifest.get("runs"), list)
    or len(manifest["runs"]) != int(expected_count)
    or hashlib.sha256(manifest_raw).hexdigest() != manifest_sha
):
    raise SystemExit("manifest identity differs")

predeploy = pathlib.Path(predeploy_path)
info = predeploy.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
    raise SystemExit("pre-deploy attestation ownership differs")
backup = json.load(open(backup_path, encoding="utf-8"))
attested = json.load(open(predeploy, encoding="utf-8"))
if (
    backup.get("schema_version") != 4
    or backup.get("complete") is not True
    or attested.get("state") != "ready"
    or attested.get("source_ref") != current_ref
    or attested.get("candidate_ref") != helper_ref
    or attested.get("manifest_uri") != backup_uri
    or str(attested.get("manifest_generation")) != backup_generation
    or int(attested.get("manifest_size_bytes", -1)) != int(backup_size)
    or attested.get("manifest_sha256") != backup_sha
    or attested.get("backup_bucket") != backup_bucket
    or attested.get("backup_policy_sha256") != backup_policy
):
    raise SystemExit("pre-deploy backup binding differs")
attested_at = datetime.fromisoformat(str(attested["attested_at"]).replace("Z", "+00:00"))
now = datetime.now(timezone.utc)
if (
    attested_at.tzinfo is None
    or attested_at > now
    or now - attested_at > timedelta(minutes=30)
):
    raise SystemExit("pre-deploy backup attestation is stale")

routing = json.load(open(routing_path, encoding="utf-8"))
observed = datetime.fromisoformat(str(routing["attested_at"]).replace("Z", "+00:00"))
if (
    routing.get("schema") != "omega.gcp-canonical-routing-scheduler-attestation/v1"
    or routing.get("scope") != "routing-and-scheduler-observation-only"
    or routing.get("source_sha") != helper_ref
    or str(routing.get("instance_id")) != instance_id
    or routing.get("gcp_scheduler_count") != 1
    or routing.get("decision", {}).get("canonical_cloud") != "GCP"
    or routing.get("decision", {}).get("canonical_writer") != "GCP"
    or routing.get("decision", {}).get("exactly_one_scheduled_writer_gcp") is not True
    or routing.get("decision", {}).get("checkpoint_zero_aws_writer_gate") != "BLOCKED"
    or routing.get("decision", {}).get("deployment_authorized") is not False
    or routing.get("aws", {}).get("db_api_hard_fence_proven") is not False
    or observed.tzinfo is None
    or observed > now + timedelta(minutes=2)
    or now - observed > timedelta(minutes=15)
):
    raise SystemExit("external routing/scheduler attestation differs or is stale")
PY
emit "immutable gate inputs" "PASS" "manifest, backup, and external routing/scheduler identities verified"
emit "checkpoint zero-AWS writer gate" "BLOCKED" \
  "scoped routing/scheduler evidence explicitly does not prove a DB/API hard fence"
fail "pre-mutation reconciliation gate" \
  "a separately verified AWS zero-writer evidence schema is required; no marker, writer stop, or pipeline-run CAS was attempted" 31

PROVENANCE_MODE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["mode"])' "$RUNTIME_PROVENANCE")"
RUNTIME_INPUT_ARGS=(
  --runtime-input "shared_env=${SHARED_ENV}"
  --runtime-input "base_compose=${BASE_COMPOSE}"
  --runtime-input "gcp_compose=${GCP_RUNTIME_COMPOSE}"
)
PROVENANCE_ARGS=()
if [[ "$PROVENANCE_MODE" == "day2" ]]; then
  LOCK_ENV="${SHARED_ROOT}/image-locks/${CURRENT_REF}/release-images.env"
  RELEASE_COMPOSE="${CURRENT_REAL}/infra/terraform-gcp/release/docker-compose.release.yml"
  [[ -s "$LOCK_ENV" && -s "$RELEASE_COMPOSE" ]] || fail "day-2 runtime inputs" "image lock or release overlay missing" 23
  COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
    --env-file "$LOCK_ENV" -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" \
    -f "$RELEASE_COMPOSE" --profile sap)
  PROVENANCE_ARGS=(--lock-env "$LOCK_ENV")
  RUNTIME_INPUT_ARGS+=(--runtime-input "release_compose=${RELEASE_COMPOSE}")
elif [[ "$PROVENANCE_MODE" == "bootstrap" ]]; then
  if python3 - "$RUNTIME_PROVENANCE" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if "legacy_image_compose" in payload.get("runtime_input_sha256", {}) else 1)
PY
  then
    [[ -s "$LEGACY_IMAGE_COMPOSE" ]] || fail "bootstrap runtime inputs" "legacy image overlay missing" 23
    COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
      -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" -f "$LEGACY_IMAGE_COMPOSE" --profile sap)
    RUNTIME_INPUT_ARGS+=(--runtime-input "legacy_image_compose=${LEGACY_IMAGE_COMPOSE}")
  else
    COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT" --env-file "$SHARED_ENV" \
      -f "$BASE_COMPOSE" -f "$GCP_RUNTIME_COMPOSE" --profile sap)
  fi
else
  fail "runtime provenance" "unsupported provenance mode" 23
fi
COMPOSE_READY=1
"${COMPOSE[@]}" config -q
python3 "$RUNTIME_CONTRACT" provenance --provenance "$RUNTIME_PROVENANCE" \
  --compose-project "$COMPOSE_PROJECT" --deploy-ref "$CURRENT_REF" \
  --version "$CURRENT_VERSION" --one-shots "${RUNTIME_INPUT_ARGS[@]}" \
  "${PROVENANCE_ARGS[@]}" >/dev/null || \
  fail "live runtime provenance" "GCP host-local runtime or scheduler drifted" 23

PREFLIGHT_ROOT="${SHARED_ROOT}/image-preflights/release-published-${PUBLISHED_RELEASE_TAG}-${HELPER_REF}-by-${HELPER_REF}"
PREFLIGHT_MANIFEST="${PREFLIGHT_ROOT}/manifest.json"
PREFLIGHT_LOCK="${PREFLIGHT_ROOT}/release-images.env"
PREFLIGHT_AUTHORITY="${PREFLIGHT_ROOT}/image-authority.json"
PREFLIGHT_COMPLETION="${PREFLIGHT_ROOT}/completion.json"
if [[ -L "$PREFLIGHT_ROOT" || ! -d "$PREFLIGHT_ROOT" || \
      "$(stat -c '%U:%G:%a' "$PREFLIGHT_ROOT" 2>/dev/null || true)" != "root:root:700" ]]; then
  fail "published release images" "server-owned preflight directory identity differs" 23
fi
python3 - "$PREFLIGHT_MANIFEST" "$PREFLIGHT_LOCK" "$PREFLIGHT_AUTHORITY" \
  "$HELPER_REF" "$PUBLISHED_RELEASE_TAG" "$PUBLISHED_MANIFEST_SHA256" \
  "$PUBLISHED_TAG_OBJECT_SHA" <<'PY'
import hashlib, json, pathlib, re, stat, sys
path = pathlib.Path(sys.argv[1])
info = path.lstat()
payload = json.load(open(path, encoding="utf-8"))
lock = pathlib.Path(sys.argv[2])
lock_info = lock.lstat()
authority_path = pathlib.Path(sys.argv[3])
authority_info = authority_path.lstat()
authority_raw = authority_path.read_bytes()
authority = json.loads(authority_raw)
expected_images = {
    "OMEGA_GCP_IMAGE_CONSOLE": "console",
    "OMEGA_GCP_IMAGE_WORKSPACE": "workspace",
    "OMEGA_GCP_IMAGE_REFINEMENT": "refinement",
    "OMEGA_GCP_IMAGE_VAULT": "vault",
    "OMEGA_GCP_IMAGE_MCP_INFRA": "mcp-infra",
    "OMEGA_GCP_IMAGE_AIRFLOW": "airflow",
    "OMEGA_GCP_IMAGE_REPLICON": "replicon",
    "OMEGA_GCP_IMAGE_HUBSPOT": "hubspot",
    "OMEGA_GCP_IMAGE_SALESFORCE": "salesforce",
    "OMEGA_GCP_IMAGE_BANXICO": "banxico",
    "OMEGA_GCP_IMAGE_INEGI": "inegi",
    "OMEGA_GCP_IMAGE_SEC_EDGAR": "sec_edgar",
    "OMEGA_GCP_IMAGE_SAP_HCM": "sap_hcm",
    "OMEGA_GCP_IMAGE_SAP_SUCCESSFACTORS": "sap_successfactors",
    "OMEGA_GCP_IMAGE_SAP_S4HANA": "sap_s4hana",
}
assignments = {}
lock_raw = lock.read_bytes()
for raw_line in lock_raw.decode("utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    key, separator, value = line.partition("=")
    if not separator or key in assignments:
        raise SystemExit("published release lock grammar differs")
    assignments[key] = value
if set(assignments) != set(expected_images):
    raise SystemExit("published release lock is not exact 15/15")
for key, repository in expected_images.items():
    expected = (
        rf"ghcr\.io/emmanuelnavaromero02-commits/{re.escape(repository)}:"
        rf"{re.escape(sys.argv[5])}@sha256:[0-9a-f]{{64}}"
    )
    if re.fullmatch(expected, assignments[key]) is None:
        raise SystemExit(f"published release lock differs: {key}")
if (
    not stat.S_ISREG(info.st_mode)
    or info.st_uid != 0
    or info.st_gid != 0
    or stat.S_IMODE(info.st_mode) != 0o400
    or info.st_nlink != 1
    or not stat.S_ISREG(lock_info.st_mode)
    or lock_info.st_uid != 0
    or lock_info.st_gid != 0
    or stat.S_IMODE(lock_info.st_mode) != 0o400
    or lock_info.st_nlink != 1
    or not stat.S_ISREG(authority_info.st_mode)
    or authority_info.st_uid != 0
    or authority_info.st_gid != 0
    or stat.S_IMODE(authority_info.st_mode) != 0o400
    or authority_info.st_nlink != 1
    or payload.get("schema_version") != 2
    or payload.get("image_count") != 15
    or payload.get("purpose") != "release"
    or payload.get("target_kind") != "published"
    or payload.get("target_tag") != sys.argv[5]
    or payload.get("target_version") != sys.argv[5].removeprefix("v")
    or payload.get("target_ref") != sys.argv[4]
    or payload.get("helper_ref") != sys.argv[4]
    or payload.get("ghcr_owner") != "emmanuelnavaromero02-commits"
    or payload.get("lock_sha256") != hashlib.sha256(lock_raw).hexdigest()
    or payload.get("image_authority_sha256") != hashlib.sha256(authority_raw).hexdigest()
    or payload.get("image_authority_mode") != "published"
    or payload.get("release_candidate_manifest_digest") != sys.argv[6]
    or payload.get("release_tag_object_sha") != sys.argv[7]
    or payload.get("candidate_workflow") is not None
    or payload.get("legacy_tag_commit") is not None
    or payload.get("secrets_included") is not False
):
    raise SystemExit("published release 15/15 preflight differs")
if (
    set(authority) != {
        "schema_version", "authority_mode", "source_sha", "release_tag",
        "image_tag", "version", "manifest_digest", "tag_object_sha",
        "candidate_workflow", "legacy_image_ids", "legacy_tag_commit",
        "images", "labels_authoritative", "secrets_included",
    }
    or authority.get("schema_version") != 1
    or authority.get("authority_mode") != "published"
    or authority.get("source_sha") != sys.argv[4]
    or authority.get("release_tag") != sys.argv[5]
    or authority.get("image_tag") != sys.argv[5]
    or authority.get("version") != sys.argv[5].removeprefix("v")
    or authority.get("manifest_digest") != sys.argv[6]
    or authority.get("tag_object_sha") != sys.argv[7]
    or authority.get("candidate_workflow") is not None
    or authority.get("legacy_image_ids") is not None
    or authority.get("legacy_tag_commit") is not None
    or authority.get("labels_authoritative") is not False
    or authority.get("secrets_included") is not False
    or not isinstance(authority.get("images"), list)
    or len(authority["images"]) != 15
):
    raise SystemExit("published release image authority differs")
authority_digests = {
    row.get("service"): row.get("digest")
    for row in authority["images"]
    if isinstance(row, dict)
}
if len(authority_digests) != 15:
    raise SystemExit("published release image authority is not unique 15/15")
for key, repository in expected_images.items():
    value = assignments[key]
    digest = value.rsplit("@", 1)[-1]
    if authority_digests.get(repository) != digest:
        raise SystemExit(f"published lock/authority digest differs: {key}")
PY
PREFLIGHT_MANIFEST_SHA256="$(sha256sum "$PREFLIGHT_MANIFEST" | awk '{print $1}')"
PREFLIGHT_LOCK_SHA256="$(sha256sum "$PREFLIGHT_LOCK" | awk '{print $1}')"
PREFLIGHT_AUTHORITY_SHA256="$(sha256sum "$PREFLIGHT_AUTHORITY" | awk '{print $1}')"
PREFLIGHT_COMPLETION_SHA256="$(sha256sum "$PREFLIGHT_COMPLETION" | awk '{print $1}')"
python3 - "$PREFLIGHT_COMPLETION" "$PREFLIGHT_MANIFEST_SHA256" \
  "$PREFLIGHT_LOCK_SHA256" "$HELPER_REF" "$PUBLISHED_RELEASE_TAG" \
  "$PREDEPLOY_ATTESTATION" "$BACKUP_MANIFEST" <<'PY'
import json, pathlib, stat, sys
from datetime import datetime, timezone

completion_path = pathlib.Path(sys.argv[1])
info = completion_path.lstat()
completion = json.load(open(completion_path, encoding="utf-8"))
predeploy = json.load(open(sys.argv[6], encoding="utf-8"))
backup = json.load(open(sys.argv[7], encoding="utf-8"))
completed_at = datetime.fromisoformat(str(completion.get("completed_at", "")))
backup_attested_at = datetime.fromisoformat(str(predeploy.get("attested_at", "")))
backup_created_at = datetime.fromisoformat(str(backup.get("created_at", "")))
now = datetime.now(timezone.utc)
if (
    not stat.S_ISREG(info.st_mode)
    or info.st_uid != 0
    or info.st_gid != 0
    or stat.S_IMODE(info.st_mode) != 0o400
    or info.st_nlink != 1
    or set(completion) != {
        "schema_version", "completed_at", "manifest_sha256", "lock_sha256",
        "helper_ref", "target_tag", "target_ref", "target_kind", "purpose",
        "image_count", "secrets_included",
    }
    or completion.get("schema_version") != 1
    or completion.get("manifest_sha256") != sys.argv[2]
    or completion.get("lock_sha256") != sys.argv[3]
    or completion.get("helper_ref") != sys.argv[4]
    or completion.get("target_tag") != sys.argv[5]
    or completion.get("target_ref") != sys.argv[4]
    or completion.get("target_kind") != "published"
    or completion.get("purpose") != "release"
    or completion.get("image_count") != 15
    or completion.get("secrets_included") is not False
    or any(value.tzinfo is None or value.utcoffset().total_seconds() != 0
           for value in (completed_at, backup_attested_at, backup_created_at))
    or completed_at > now
    or backup_created_at <= completed_at
    or backup_attested_at < backup_created_at
):
    raise SystemExit("fresh backup is not strictly after published 15/15 preflight")
PY
emit "published release then fresh backup" "PASS" \
  "backup completion strictly follows the immutable published 15/15 pull receipt"
if ! PUBLISHED_CONSOLE_IMAGE="$(python3 - "$PREFLIGHT_LOCK" "$PUBLISHED_RELEASE_TAG" <<'PY'
import pathlib, re, sys
matches = []
for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if line.startswith("OMEGA_GCP_IMAGE_CONSOLE="):
        matches.append(line.split("=", 1)[1])
expected = rf"ghcr\.io/emmanuelnavaromero02-commits/console:{re.escape(sys.argv[2])}@sha256:[0-9a-f]{{64}}"
if len(matches) != 1 or re.fullmatch(expected, matches[0]) is None:
    raise SystemExit(1)
print(matches[0])
PY
)"; then
  fail "published release console image" "published console digest lock differs" 23
fi
PUBLISHED_CONSOLE_IMAGE_ID="$(docker image inspect "$PUBLISHED_CONSOLE_IMAGE" --format '{{.Id}}' 2>/dev/null || true)"
[[ "$PUBLISHED_CONSOLE_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || \
  fail "published release console image" "digest-pinned published image is not local" 23
if ! python3 - "$PREFLIGHT_MANIFEST" "$HELPER_REF" "$PUBLISHED_CONSOLE_IMAGE" <<'PY'
import json, subprocess, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
result = subprocess.run(
    ["docker", "image", "inspect", sys.argv[3], "--format", "{{json .Config.Labels}}"],
    text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
)
labels = json.loads(result.stdout) if result.returncode == 0 else {}
if (
    labels.get("org.opencontainers.image.revision") != sys.argv[2]
    or labels.get("org.opencontainers.image.version") != manifest.get("target_version")
    or labels.get("org.opencontainers.image.source")
       != "https://github.com/emmanuelnavaromero02-commits/CONSOLA-V1"
):
    raise SystemExit(1)
PY
then
  fail "published release console image" "local OCI identity differs from the 15/15 published preflight" 23
fi
emit "published release images" "PASS" \
  "tag=${PUBLISHED_RELEASE_TAG} ref=${HELPER_REF} authenticated preflight remains 15/15"

for service in "${MUTATING_SERVICES[@]}"; do
  ids="$(running_ids_for_service "$service")"
  count="$(grep -c . <<<"$ids" || true)"
  if [[ "$count" -gt 1 ]]; then
    fail "GCP host-local runtime inventory" "duplicate service=${service}" 24
  elif [[ "$count" == "1" ]]; then
    RUNNING_BEFORE+=("$service")
  fi
done
if [[ "$(running_ids_for_service airflow-scheduler | grep -c . || true)" != "1" ]]; then
  fail "GCP scheduler baseline" "expected exactly one host-local scheduler" 24
fi
for container in mode_postgres mode_postgres_gold mode_console; do
  if [[ "$(docker inspect "$container" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || true)" != "$COMPOSE_PROJECT" ]]; then
    fail "Compose project identity" "database/console container project differs" 24
  fi
done

CONSOLE_NETWORK="$(docker inspect mode_console --format '{{json .NetworkSettings.Networks}}' | python3 -c 'import json,sys; value=json.load(sys.stdin); raise SystemExit(1) if len(value)!=1 else print(next(iter(value)))')" || \
  fail "canonical runner network" "console must have one exact Compose network" 24
python3 /dev/fd/3 "$RUNNER_ENV" \
  3<<'PY' < <(docker inspect mode_console --format '{{json .Config.Env}}')
import json, os, pathlib, sys
values = [item.split("=", 1)[1] for item in json.load(sys.stdin) if item.startswith("DATABASE_URL=")]
if len(values) != 1 or not values[0] or "\n" in values[0] or "\r" in values[0]:
    raise SystemExit("console DATABASE_URL is unavailable")
path = pathlib.Path(sys.argv[1])
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    stream.write("DATABASE_URL=" + values[0] + "\n")
PY
"$METADATA_FIREWALL" verify-container >/dev/null || \
  fail "container metadata isolation" "metadata firewall is not effective" 24

temporary="$(mktemp /usr/local/sbin/.omega-operation-watchdog.XXXXXX)"
install -m 0755 "$CANDIDATE_WATCHDOG" "$temporary"
mv -Tf "$temporary" "$WATCHDOG"
"$SAFE_IO" fsync-dir /usr/local/sbin >/dev/null

python3 - "$OPERATION_MARKER" "$CURRENT_REF" "$HELPER_REF" "$CHANGE_ID" \
  "$MANIFEST_URI" "$MANIFEST_GENERATION" "$MANIFEST_SIZE_BYTES" "$MANIFEST_SHA256" \
  "$BACKUP_MANIFEST_URI" "$BACKUP_MANIFEST_SHA256" "$ROUTING_ATTESTATION_URI" \
  "$ROUTING_ATTESTATION_GENERATION" "$ROUTING_ATTESTATION_SIZE_BYTES" \
  "$ROUTING_ATTESTATION_SHA256" "$PUBLISHED_RELEASE_TAG" \
  "$PREFLIGHT_COMPLETION_SHA256" "$PREFLIGHT_MANIFEST_SHA256" \
  "$PREFLIGHT_LOCK_SHA256" "$PREFLIGHT_AUTHORITY_SHA256" \
  "$PUBLISHED_MANIFEST_SHA256" "$PUBLISHED_TAG_OBJECT_SHA" \
  "$HANDOFF_TIMEOUT_SECONDS" \
  "${RUNNING_BEFORE[@]}" <<'PY'
import json, os, pathlib, sys, tempfile
from datetime import datetime, timedelta, timezone
path = pathlib.Path(sys.argv[1])
now = datetime.now(timezone.utc)
payload = {
    "schema_version": 2,
    "operation": "pipeline-run-reconciliation",
    "state": "fencing",
    "current_ref": sys.argv[2],
    "candidate_ref": sys.argv[3],
    "change_id": sys.argv[4],
    "manifest": {"uri": sys.argv[5], "generation": sys.argv[6], "size_bytes": int(sys.argv[7]), "sha256": sys.argv[8]},
    "backup_manifest": {"uri": sys.argv[9], "sha256": sys.argv[10]},
    "external_routing_scheduler_attestation": {"uri": sys.argv[11], "generation": sys.argv[12], "size_bytes": int(sys.argv[13]), "sha256": sys.argv[14]},
    "published_release_tag": sys.argv[15],
    "published_release_preflight": {
        "completion_sha256": sys.argv[16], "manifest_sha256": sys.argv[17],
        "lock_sha256": sys.argv[18], "authority_sha256": sys.argv[19],
        "release_candidate_manifest_digest": sys.argv[20],
        "release_tag_object_sha": sys.argv[21],
    },
    "running_before": sorted(sys.argv[23:]),
    "created_at": now.isoformat(),
    "expires_at": (now + timedelta(seconds=int(sys.argv[22]))).isoformat(),
    "receipt": None,
    "secrets_included": False,
}
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
with os.fdopen(fd, "w", encoding="utf-8") as stream:
    os.fchmod(stream.fileno(), 0o600)
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
os.replace(temporary, path)
directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
os.fsync(directory); os.close(directory)
PY
"$WATCHDOG" arm "$$" "$OPERATION_MARKER"
FENCE_ACTIVE=1
emit "durable reconciliation owner" "PASS" "marker persisted and independent watchdog armed before writer mutation"

"${COMPOSE[@]}" stop --timeout 60 "${MUTATING_SERVICES[@]}"
python3 "$RUNTIME_CONTRACT" writer-fence --compose-project "$COMPOSE_PROJECT" >/dev/null || \
  fail "GCP host-local writer fence" "a labeled or unlabeled proprietary writer remains running" 25
for pair in "mode_postgres:5432" "mode_postgres_gold:5433"; do
  container="${pair%%:*}"; port="${pair#*:}"
  [[ "$(docker inspect "$container" --format '{{.State.Running}}' 2>/dev/null || true)" == "true" ]] || \
    fail "database continuity" "database container stopped during application fence" 25
  docker exec "$container" psql -v ON_ERROR_STOP=1 -At -U postgres -d postgres -p "$port" \
    -c "SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a JOIN pg_roles r ON r.rolname=a.usename WHERE a.pid<>pg_backend_pid() AND NOT r.rolsuper;" >/dev/null || \
    fail "database session quiescence" "cannot terminate non-superuser sessions" 25
done
emit "GCP host-local writer fence" "PASS" "writers and scheduler stopped; databases preserved; external routing/scheduler assertion consumed"

run_engine() {
  local mode="$1" output="$2"
  local -a command=(docker run --rm --pull never \
    --name "omega_pipeline_reconcile_${HELPER_REF:0:12}_${mode}" \
    --label omega.pipeline-reconciliation-runner=true \
    --read-only --cap-drop ALL --security-opt no-new-privileges \
    --pids-limit 64 \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
    --network "$CONSOLE_NETWORK" --env-file "$RUNNER_ENV" \
    --entrypoint python \
    -v "${CANONICAL_ENGINE}:/run/omega/reconcile_pipeline_runs.py:ro" \
    -v "${MANIFEST}:/run/omega/reconciliation.json:ro")
  if [[ "$mode" == "apply" ]]; then
    command+=(
      -e OMEGA_PIPELINE_RECONCILIATION_ALLOW_APPLY=1
      -e OMEGA_PIPELINE_RECONCILIATION_ACTOR=gcp-release-operator
      -e "OMEGA_PIPELINE_RECONCILIATION_CHANGE_ID=${CHANGE_ID}"
    -e "OMEGA_PIPELINE_RECONCILIATION_MANIFEST_SHA256=${MANIFEST_SHA256}"
    )
  fi
  command+=("$PUBLISHED_CONSOLE_IMAGE" /run/omega/reconcile_pipeline_runs.py \
    --manifest /run/omega/reconciliation.json)
  [[ "$mode" == "apply" ]] && command+=(--apply)
  "${command[@]}" >"$output" 2>"${output}.err"
}

validate_engine_output() {
  local output="$1" expected_result="$2" expected_mode="$3"
  python3 - "$output" "$EXPECTED_COUNT" "$MANIFEST_SHA256" "$CHANGE_ID" \
    "$expected_result" "$expected_mode" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
runs = payload.get("runs")
if (
    payload.get("ok") is not True
    or payload.get("manifest_sha256") != sys.argv[3]
    or payload.get("change_id") != sys.argv[4]
    or payload.get("mode") != sys.argv[6]
    or not isinstance(runs, list)
    or len(runs) != int(sys.argv[2])
    or any(row.get("result") != sys.argv[5] for row in runs)
):
    raise SystemExit("canonical engine aggregate result differs")
PY
}

run_engine dry-run "${WORKDIR}/dry-run.json" || fail "canonical dry-run" "engine rejected live rows; details retained only on host" 26
validate_engine_output "${WORKDIR}/dry-run.json" would_apply dry-run || \
  fail "canonical dry-run" "exact count was not eligible" 26
emit "canonical dry-run" "PASS" "exact count eligible; active leases rejected; no writes"

# The final host-local fence readback is deliberately adjacent to apply.
python3 "$RUNTIME_CONTRACT" writer-fence --compose-project "$COMPOSE_PROJECT" >/dev/null || \
  fail "final GCP host-local writer fence" "writer appeared after dry-run" 26
APPLY_STARTED=1
python3 - "$OPERATION_MARKER" <<'PY'
import json, os, pathlib, sys, tempfile
from datetime import datetime, timezone
path = pathlib.Path(sys.argv[1])
payload = json.load(open(path, encoding="utf-8"))
if payload.get("operation") != "pipeline-run-reconciliation" or payload.get("state") != "fencing":
    raise SystemExit("operation marker changed before apply")
payload["state"] = "applying"
payload["updated_at"] = datetime.now(timezone.utc).isoformat()
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
with os.fdopen(fd, "w", encoding="utf-8") as stream:
    os.fchmod(stream.fileno(), 0o600); json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
os.replace(temporary, path)
directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY); os.fsync(directory); os.close(directory)
PY
run_engine apply "${WORKDIR}/apply.json" || fail "canonical apply" "transaction outcome is not proven; old runtime remains fenced" 27
CAS_COMMITTED=1
validate_engine_output "${WORKDIR}/apply.json" applied apply || \
  fail "canonical apply" "exact apply aggregate differs" 27
emit "canonical apply" "PASS" "single canonical transaction committed extra.reconciliation and critical audit_events"

run_engine dry-run "${WORKDIR}/idempotency.json" || fail "canonical idempotency" "poststate readback failed" 28
validate_engine_output "${WORKDIR}/idempotency.json" already_applied dry-run || \
  fail "canonical idempotency" "poststate was not exactly idempotent" 28
python3 "$RUNTIME_CONTRACT" writer-fence --compose-project "$COMPOSE_PROJECT" >/dev/null || \
  fail "continuous GCP host-local writer fence" "writer appeared after reconciliation" 28
emit "canonical poststate" "PASS" "exact count already_applied; leases/heartbeats cleared; fence retained"

RECEIPT_DIR="${SHARED_ROOT}/reconciliation-receipts"
install -d -m 0700 "$RECEIPT_DIR"
RECEIPT="${RECEIPT_DIR}/${CHANGE_ID}-${MANIFEST_SHA256}.json"
python3 - "$RECEIPT" "$CHANGE_ID" "$CURRENT_REF" "$HELPER_REF" "$EXPECTED_COUNT" \
  "$MANIFEST_URI" "$MANIFEST_GENERATION" "$MANIFEST_SIZE_BYTES" "$MANIFEST_SHA256" \
  "$BACKUP_MANIFEST_URI" "$BACKUP_MANIFEST_SHA256" "$ROUTING_ATTESTATION_URI" \
  "$ROUTING_ATTESTATION_GENERATION" "$ROUTING_ATTESTATION_SIZE_BYTES" \
  "$ROUTING_ATTESTATION_SHA256" "$PUBLISHED_RELEASE_TAG" \
  "$PREFLIGHT_COMPLETION_SHA256" "$PREFLIGHT_MANIFEST_SHA256" \
  "$PREFLIGHT_LOCK_SHA256" "$PREFLIGHT_AUTHORITY_SHA256" \
  "$PUBLISHED_MANIFEST_SHA256" "$PUBLISHED_TAG_OBJECT_SHA" <<'PY'
import json, os, pathlib, sys, tempfile
from datetime import datetime, timezone
path = pathlib.Path(sys.argv[1])
payload = {
    "schema_version": 1, "status": "PASS", "change_id": sys.argv[2],
    "current_ref": sys.argv[3], "candidate_ref": sys.argv[4],
    "reconciled_count": int(sys.argv[5]),
    "manifest": {"uri": sys.argv[6], "generation": sys.argv[7], "size_bytes": int(sys.argv[8]), "sha256": sys.argv[9]},
    "backup_manifest": {"uri": sys.argv[10], "sha256": sys.argv[11]},
    "external_routing_scheduler_attestation": {"uri": sys.argv[12], "generation": sys.argv[13], "size_bytes": int(sys.argv[14]), "sha256": sys.argv[15]},
    "published_release_tag": sys.argv[16],
    "published_release_preflight": {
        "completion_sha256": sys.argv[17], "manifest_sha256": sys.argv[18],
        "lock_sha256": sys.argv[19], "authority_sha256": sys.argv[20],
        "release_candidate_manifest_digest": sys.argv[21],
        "release_tag_object_sha": sys.argv[22],
    },
    "canonical_engine": "scripts/reconcile_pipeline_runs.py",
    "dry_run": "would_apply", "apply": "applied", "poststate": "already_applied",
    "gcp_host_local_writer_fence": "held", "checkpoint_zero_aws_writer_gate": "PASS",
    "pre_release_runtime_restart_attempted": False,
    "completed_at": datetime.now(timezone.utc).isoformat(), "secrets_included": False,
}
if os.path.lexists(path):
    raise SystemExit("reconciliation receipt already exists")
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600); json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.link(temporary, path)
    os.unlink(temporary)
finally:
    if os.path.lexists(temporary):
        os.unlink(temporary)
directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY); os.fsync(directory); os.close(directory)
PY
RECEIPT_SHA256="$(sha256sum "$RECEIPT" | awk '{print $1}')"

python3 - "$OPERATION_MARKER" "$RECEIPT" "$RECEIPT_SHA256" \
  "$HANDOFF_TIMEOUT_SECONDS" <<'PY'
import json, os, pathlib, sys, tempfile
from datetime import datetime, timedelta, timezone
path = pathlib.Path(sys.argv[1])
payload = json.load(open(path, encoding="utf-8"))
if payload.get("operation") != "pipeline-run-reconciliation" or payload.get("state") != "applying":
    raise SystemExit("operation marker changed")
payload["state"] = "handoff-ready"
payload["receipt"] = {"path": sys.argv[2], "sha256": sys.argv[3]}
now = datetime.now(timezone.utc)
payload["updated_at"] = now.isoformat()
payload["expires_at"] = (now + timedelta(seconds=int(sys.argv[4]))).isoformat()
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
with os.fdopen(fd, "w", encoding="utf-8") as stream:
    os.fchmod(stream.fileno(), 0o600); json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
os.replace(temporary, path)
directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY); os.fsync(directory); os.close(directory)
PY
"$WATCHDOG" arm-hold "$HANDOFF_TIMEOUT_SECONDS" "$OPERATION_MARKER"
HANDOFF_READY=1
emit "continuous fence handoff" "PASS" \
  "published-release-bound receipt sealed; old runtime remains stopped for immediate day-2 takeover"
printf 'OMEGA_GCP_RECONCILE_JSON={"status":"PASS","change_id":"%s","current_ref":"%s","candidate_ref":"%s","published_release_tag":"%s","reconciled_count":%s,"receipt_sha256":"%s","host_local_writer_fence":"held","checkpoint_zero_aws_writer_gate":"PASS","secrets_included":false}\n' \
  "$CHANGE_ID" "$CURRENT_REF" "$HELPER_REF" "$PUBLISHED_RELEASE_TAG" \
  "$EXPECTED_COUNT" "$RECEIPT_SHA256"
