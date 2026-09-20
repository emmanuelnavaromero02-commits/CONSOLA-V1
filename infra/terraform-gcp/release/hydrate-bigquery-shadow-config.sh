#!/usr/bin/env bash
# Atomically apply the non-secret BigQuery shadow runtime handoff emitted by
# Terraform. The complete document is validated before the env file changes.
# OMEGA_BIGQUERY_SHADOW_CONFIG_MODE=check performs validation without a write.
set -Eeuo pipefail
set +x
umask 077

die() { printf '[gcp-bigquery-shadow-config] ERROR: %s\n' "$*" >&2; exit 1; }

: "${OMEGA_BIGQUERY_SHADOW_CONFIG_FILE:?OMEGA_BIGQUERY_SHADOW_CONFIG_FILE is required}"
: "${OMEGA_GCP_PROJECT_ID:?OMEGA_GCP_PROJECT_ID is required}"
: "${OMEGA_GCP_ENVIRONMENT:?OMEGA_GCP_ENVIRONMENT is required}"
hydration_mode="${OMEGA_BIGQUERY_SHADOW_CONFIG_MODE:-apply}"
[[ "${hydration_mode}" == "apply" || "${hydration_mode}" == "check" ]] \
  || die "OMEGA_BIGQUERY_SHADOW_CONFIG_MODE must be apply or check"
if [[ "${hydration_mode}" == "apply" ]]; then
  : "${OMEGA_ENV_FILE:?OMEGA_ENV_FILE is required}"
fi

[[ -f "${OMEGA_BIGQUERY_SHADOW_CONFIG_FILE}" ]] || die "runtime config file does not exist"

python3 - \
  "${OMEGA_ENV_FILE:-}" \
  "${OMEGA_BIGQUERY_SHADOW_CONFIG_FILE}" \
  "${hydration_mode}" \
  "${OMEGA_GCP_PROJECT_ID}" \
  "${OMEGA_GCP_ENVIRONMENT}" <<'PY'
import json
import os
from pathlib import Path
import re
import tempfile
import sys

target = Path(sys.argv[1])
config_path = Path(sys.argv[2])
hydration_mode = sys.argv[3]
expected_deployment_project_id = sys.argv[4]
expected_environment = sys.argv[5]

try:
    config = json.loads(config_path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit("invalid BigQuery shadow runtime config")

expected = {
    "schema_version",
    "deployment_project_id",
    "environment",
    "enabled",
    "project_id",
    "dataset_id",
    "location",
    "service_account",
    "gold_bucket",
    "tenant_id",
    "workspace_id",
    "maximum_bytes_billed",
    "population_backend",
}
if not isinstance(config, dict) or set(config) != expected:
    raise SystemExit("BigQuery shadow runtime config has an invalid schema")
if config["schema_version"] != 2 or not isinstance(config["enabled"], bool):
    raise SystemExit("BigQuery shadow runtime config version/flag is invalid")
if config["population_backend"] != "postgres_gold":
    raise SystemExit("public Talent backend must remain postgres_gold")
if config["location"] != "us-central1":
    raise SystemExit("BigQuery shadow location must be us-central1")
max_bytes = config["maximum_bytes_billed"]
if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= 10 * 1024**3:
    raise SystemExit("BigQuery shadow maximum_bytes_billed is invalid")

string_fields = (
    "deployment_project_id",
    "environment",
    "project_id",
    "dataset_id",
    "service_account",
    "gold_bucket",
    "tenant_id",
    "workspace_id",
)
if any(not isinstance(config[field], str) for field in string_fields):
    raise SystemExit("BigQuery shadow runtime coordinates must be strings")
if any("\n" in config[field] or "\r" in config[field] or "\x00" in config[field] for field in string_fields):
    raise SystemExit("BigQuery shadow runtime coordinates contain forbidden characters")
if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", expected_deployment_project_id):
    raise SystemExit("deployment project identity is invalid")
if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", expected_environment):
    raise SystemExit("deployment environment identity is invalid")
if config["deployment_project_id"] != expected_deployment_project_id:
    raise SystemExit("BigQuery shadow handoff belongs to another deployment project")
if config["environment"] != expected_environment:
    raise SystemExit("BigQuery shadow handoff belongs to another environment")

uuid_re = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
if config["enabled"]:
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", config["project_id"]):
        raise SystemExit("BigQuery shadow project_id is invalid")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,1023}", config["dataset_id"]):
        raise SystemExit("BigQuery shadow dataset_id is invalid")
    expected_dataset = f"omega_{expected_environment.replace('-', '_')}_talent_shadow"
    if config["dataset_id"] != expected_dataset:
        raise SystemExit("BigQuery shadow dataset_id is not bound to the deployment environment")
    if not re.fullmatch(
        r"[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com",
        config["service_account"],
    ):
        raise SystemExit("BigQuery shadow service_account is invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", config["gold_bucket"]):
        raise SystemExit("BigQuery shadow gold_bucket is invalid")
    if not uuid_re.fullmatch(config["tenant_id"].lower()):
        raise SystemExit("BigQuery shadow tenant_id is invalid")
    if not uuid_re.fullmatch(config["workspace_id"].lower()):
        raise SystemExit("BigQuery shadow workspace_id is invalid")
else:
    # Disabled handoffs deliberately clear all coordinates. This prevents a
    # later accidental flag flip from activating stale Terraform values.
    coordinate_fields = (
        "project_id",
        "dataset_id",
        "service_account",
        "gold_bucket",
        "tenant_id",
        "workspace_id",
    )
    if any(config[field] for field in coordinate_fields):
        raise SystemExit("disabled BigQuery shadow config must clear runtime coordinates")

if hydration_mode == "check":
    raise SystemExit(0)

updates = {
    "TALENT_POPULATION_BACKEND": "postgres_gold",
    "BIGQUERY_TALENT_9BOX_SHADOW_ENABLED": "true" if config["enabled"] else "false",
    "BIGQUERY_TALENT_9BOX_SHADOW_PROJECT_ID": config["project_id"],
    "BIGQUERY_TALENT_9BOX_SHADOW_DATASET": config["dataset_id"],
    "BIGQUERY_TALENT_9BOX_SHADOW_LOCATION": config["location"],
    "BIGQUERY_TALENT_9BOX_SHADOW_SERVICE_ACCOUNT": config["service_account"],
    "BIGQUERY_TALENT_9BOX_SHADOW_GOLD_BUCKET": config["gold_bucket"],
    "BIGQUERY_TALENT_9BOX_SHADOW_TENANT_ALLOWLIST": config["tenant_id"].lower(),
    "BIGQUERY_TALENT_9BOX_SHADOW_WORKSPACE_ALLOWLIST": config["workspace_id"].lower(),
    "BIGQUERY_TALENT_9BOX_SHADOW_MAX_BYTES_BILLED": str(max_bytes),
}

original = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
output = []
written = set()
for line in original:
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in updates:
        if key not in written:
            output.append(f"{key}={updates[key]}")
            written.add(key)
        continue
    output.append(line)
for key, value in updates.items():
    if key not in written:
        output.append(f"{key}={value}")

target.parent.mkdir(parents=True, exist_ok=True)
fd, temporary_name = tempfile.mkstemp(prefix=".omega-env.", dir=target.parent)
try:
    os.fchmod(fd, 0o600)
    if target.exists():
        stat = target.stat()
        try:
            os.fchown(fd, stat.st_uid, stat.st_gid)
        except PermissionError:
            pass
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        fd = -1
        handle.write("\n".join(output) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_name, target)
    directory_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    if fd >= 0:
        os.close(fd)
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
PY

if [[ "${hydration_mode}" == "check" ]]; then
  printf '[gcp-bigquery-shadow-config] runtime config validated; environment unchanged\n'
else
  printf '[gcp-bigquery-shadow-config] runtime config updated atomically\n'
fi
