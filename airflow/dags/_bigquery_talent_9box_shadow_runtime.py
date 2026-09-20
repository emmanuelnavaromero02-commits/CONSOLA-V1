"""Pure runtime helpers for the non-serving Talent 9-Box BigQuery pilot."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import requests


TEN_GIB = 10 * 1024 * 1024 * 1024
BOX_KEYS = (
    "enigma",
    "crecimiento",
    "estrella",
    "dilema",
    "core",
    "alto_impacto",
    "riesgo",
    "efectivo",
    "experto",
)
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_RUN_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,511}$")
_GCP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,1023}$")
_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PII_KEY = re.compile(
    r"^(?:name|full_name|first_name|last_name|email|pernr|person_id|employee_id|user_id)$",
    re.IGNORECASE,
)
_EMAIL = re.compile(
    rb"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)
_LOG_PII_FIELD = re.compile(
    rb"(?i)[\"'](?:full_name|first_name|last_name|email|pernr|person_id|employee_id|user_id)[\"']\s*[:=]\s*[\"'][^\"']+"
)
_QUERY_FIELDS = frozenset(
    {
        "tenant_id",
        "workspace_id",
        "invalid_score_input",
        "performance_score",
        "potential_score",
        "box_status",
        "box_key",
        "performance_band_available",
        "source_mode",
        "readiness_status",
        "benchmark_raw_score",
        "benchmark_score",
        "readiness_benchmark_count",
        "benchmark_count",
        "benchmark_provenance_status",
        "benchmark_approval_valid",
        "benchmark_materialization_head",
    }
)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _enabled(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _required_env(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _strict_count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"{label} must be a nonnegative integer")
    return value


def _required_token(value: Any, label: str) -> str:
    token = str(value or "").strip()
    if not token or not _RUN_REF.fullmatch(token):
        raise RuntimeError(f"{label} is invalid")
    return token


@dataclass(frozen=True)
class ShadowRunConfig:
    tenant_id: str
    workspace_id: str
    cartridge_id: str
    source_dataset: str
    pipeline_run_id: str
    source_run_id: str
    project_id: str
    bigquery_dataset: str
    location: str
    service_account: str
    gold_bucket: str
    maximum_bytes_billed: int
    console_url: str
    mcp_infra_url: str

    @classmethod
    def load(
        cls,
        conf: Mapping[str, Any],
        env: Mapping[str, str] | None = None,
    ) -> "ShadowRunConfig":
        environ = os.environ if env is None else env
        if not _enabled(environ.get("BIGQUERY_TALENT_9BOX_SHADOW_ENABLED")):
            raise RuntimeError("BigQuery Talent shadow is disabled")
        if str(
            environ.get("TALENT_POPULATION_BACKEND") or "postgres_gold"
        ).strip().lower() != "postgres_gold":
            raise RuntimeError("PostgreSQL Gold must remain the public Talent backend")
        if str(environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip():
            raise RuntimeError("static Google credential files are prohibited")

        tenant_id = str(conf.get("tenant_id") or "").strip().lower()
        workspace_id = str(conf.get("workspace_id") or "").strip().lower()
        source_run_id = str(conf.get("source_run_id") or "").strip().lower()
        pipeline_run_id = str(conf.get("pipeline_run_id") or "").strip()
        cartridge_id = str(conf.get("cartridge_id") or "").strip()
        source_dataset = str(conf.get("dataset") or "").strip()
        if not all(_UUID.fullmatch(value) for value in (tenant_id, workspace_id, source_run_id)):
            raise RuntimeError("shadow scope and source run must be UUIDs")
        if not _RUN_REF.fullmatch(pipeline_run_id):
            raise RuntimeError("shadow pipeline run reference is invalid")
        if cartridge_id != "sap_successfactors" or source_dataset != "sap_successfactors_talent_9box":
            raise RuntimeError("shadow target is not the allowlisted Talent dataset")
        allowlist = {
            item.strip().lower()
            for item in str(
                environ.get("BIGQUERY_TALENT_9BOX_SHADOW_WORKSPACE_ALLOWLIST") or ""
            ).split(",")
            if item.strip()
        }
        if workspace_id not in allowlist:
            raise RuntimeError("workspace is not in the BigQuery shadow allowlist")
        tenant_allowlist = {
            item.strip().lower()
            for item in str(
                environ.get("BIGQUERY_TALENT_9BOX_SHADOW_TENANT_ALLOWLIST") or ""
            ).split(",")
            if item.strip()
        }
        if tenant_id not in tenant_allowlist:
            raise RuntimeError("tenant is not in the BigQuery shadow allowlist")

        project_id = _required_env(environ, "BIGQUERY_TALENT_9BOX_SHADOW_PROJECT_ID")
        bigquery_dataset = _required_env(
            environ, "BIGQUERY_TALENT_9BOX_SHADOW_DATASET"
        )
        if not _GCP_ID.fullmatch(project_id) or not _GCP_ID.fullmatch(bigquery_dataset):
            raise RuntimeError("BigQuery project or dataset identifier is invalid")
        location = str(
            environ.get("BIGQUERY_TALENT_9BOX_SHADOW_LOCATION") or "us-central1"
        ).strip().lower()
        if location != "us-central1":
            raise RuntimeError("BigQuery Talent shadow must run in us-central1")
        maximum = int(
            environ.get("BIGQUERY_TALENT_9BOX_SHADOW_MAX_BYTES_BILLED") or TEN_GIB
        )
        if maximum <= 0 or maximum > TEN_GIB:
            raise RuntimeError("maximum_bytes_billed exceeds the 10 GiB pilot cap")
        service_account = _required_env(
            environ, "BIGQUERY_TALENT_9BOX_SHADOW_SERVICE_ACCOUNT"
        )
        gold_bucket = _required_env(
            environ, "BIGQUERY_TALENT_9BOX_SHADOW_GOLD_BUCKET"
        )
        if not _SERVICE_ACCOUNT.fullmatch(service_account) or not _BUCKET.fullmatch(
            gold_bucket
        ):
            raise RuntimeError("BigQuery shadow identity or bucket is invalid")
        return cls(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            cartridge_id=cartridge_id,
            source_dataset=source_dataset,
            pipeline_run_id=pipeline_run_id,
            source_run_id=source_run_id,
            project_id=project_id,
            bigquery_dataset=bigquery_dataset,
            location=location,
            service_account=service_account,
            gold_bucket=gold_bucket,
            maximum_bytes_billed=maximum,
            console_url=str(
                environ.get("CONSOLE_INTERNAL_URL")
                or environ.get("CONSOLE_URL")
                or "http://console:8000"
            ).rstrip("/"),
            mcp_infra_url=str(
                environ.get("MCP_INFRA_URL") or "http://mcp-infra:8010"
            ).rstrip("/"),
        )

    @property
    def table_id(self) -> str:
        return f"talent_9box_{self.source_run_id.replace('-', '')}"

    @property
    def qualified_table(self) -> str:
        return f"{self.project_id}.{self.bigquery_dataset}.{self.table_id}"


def _internal_key(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if value:
        return value
    if os.environ.get("APP_ENV", "production").strip().lower() not in {
        "production",
        "prod",
        "staging",
    }:
        fallback = str(os.environ.get("INTERNAL_API_KEY") or "").strip()
        if fallback:
            return fallback
    raise RuntimeError(f"{name} is required")


def audit_no_pii_payload(*payloads: Any) -> dict[str, Any]:
    violations = 0
    checked = 0

    def visit(value: Any) -> None:
        nonlocal checked, violations
        checked += 1
        if isinstance(value, dict):
            for key, nested in value.items():
                if _PII_KEY.fullmatch(str(key)):
                    violations += 1
                visit(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested)
        elif isinstance(value, str) and _EMAIL.search(value.encode("utf-8")):
            violations += 1

    for payload in payloads:
        visit(payload)
    evidence = {
        "scanner": "omega-shadow-xcom-metadata/v1",
        "checked_nodes": checked,
        "violations": violations,
        "passed": violations == 0,
    }
    return {**evidence, "digest": digest(evidence)}


def scan_airflow_logs(base_folder: str, *, dag_id: str, run_id: str) -> dict[str, Any]:
    """Scan completed upstream task logs without serialising their content."""
    base = Path(base_folder).resolve()
    if dag_id != "bigquery_talent_9box_shadow" or not _RUN_REF.fullmatch(run_id):
        raise RuntimeError("invalid Airflow log audit scope")
    if not base.is_dir():
        evidence = {
            "scanner": "omega-shadow-local-log/v1",
            "files_scanned": 0,
            "violations": 0,
            "passed": False,
            "status": "log_folder_unavailable",
        }
        return {**evidence, "digest": digest(evidence)}
    dag_marker = f"dag_id={dag_id}"
    run_marker = f"run_id={run_id}"
    candidates = [
        path
        for path in base.rglob("*.log")
        if dag_marker in path.parts and run_marker in path.parts
    ]
    violations = 0
    scanned = 0
    for path in candidates:
        resolved = path.resolve()
        if base not in resolved.parents:
            continue
        scanned += 1
        with resolved.open("rb") as handle:
            tail = b""
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                sample = tail + chunk
                violations += len(_EMAIL.findall(sample))
                violations += len(_LOG_PII_FIELD.findall(sample))
                tail = sample[-512:]
    evidence = {
        "scanner": "omega-shadow-local-log/v1",
        "files_scanned": scanned,
        "violations": violations,
        "passed": scanned > 0 and violations == 0,
        "status": "complete" if scanned > 0 else "logs_not_yet_visible",
    }
    return {**evidence, "digest": digest(evidence)}


def validate_baseline(baseline: Any, config: ShadowRunConfig) -> None:
    expected = {
        "manifest",
        "nine_box_counts",
        "cohorts",
        "totals",
        "blockers",
        "verification_context",
        "digest",
    }
    if not isinstance(baseline, dict) or set(baseline) != expected:
        raise RuntimeError("PostgreSQL baseline contract is invalid")
    manifest = baseline.get("manifest")
    required_manifest = {
        "pipeline_run_id",
        "source_run_id",
        "head_generation",
        "object_version",
        "receipt_id",
        "uri",
        "bucket",
        "object_key",
        "checksum",
        "schema_digest",
        "evidence_digest",
        "row_count",
        "published_at",
    }
    if not isinstance(manifest, dict) or set(manifest) != required_manifest:
        raise RuntimeError("Gold publication manifest is incomplete")
    if (
        manifest["pipeline_run_id"] != config.pipeline_run_id
        or manifest["source_run_id"] != config.source_run_id
        or manifest["bucket"] != config.gold_bucket
    ):
        raise RuntimeError("Gold publication manifest does not match the trigger")
    if (
        isinstance(manifest["head_generation"], bool)
        or not isinstance(manifest["head_generation"], int)
        or manifest["head_generation"] <= 0
        or not isinstance(manifest["object_version"], str)
        or not manifest["object_version"].isdigit()
        or int(manifest["object_version"]) <= 0
    ):
        raise RuntimeError("Gold publication generation is invalid")
    _strict_count(manifest["row_count"], "Gold publication row count")
    for key in ("checksum", "schema_digest", "evidence_digest"):
        if not _SHA256.fullmatch(str(manifest[key])):
            raise RuntimeError("Gold publication digest is invalid")
    try:
        uuid.UUID(str(manifest["receipt_id"]))
    except ValueError as exc:
        raise RuntimeError("Gold publication receipt is invalid") from exc
    parsed = urlsplit(str(manifest["uri"]))
    expected_key = (
        f"gold/{config.cartridge_id}/{config.source_dataset}/"
        f"tenant_id={config.tenant_id}/workspace_id={config.workspace_id}/"
        f"_snapshots/_pending/{uuid.UUID(config.source_run_id).hex}/"
        f"{manifest['checksum']}.parquet"
    )
    if (
        parsed.scheme != "gs"
        or parsed.netloc != manifest["bucket"]
        or parsed.path.lstrip("/") != manifest["object_key"]
        or manifest["object_key"] != expected_key
        or any(token in str(manifest["uri"]) for token in ("*", "?", "\\", ".."))
    ):
        raise RuntimeError("Gold publication URI is not exact")
    try:
        published_at = datetime.fromisoformat(
            str(manifest["published_at"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise RuntimeError("Gold publication timestamp is invalid") from exc
    if published_at.tzinfo is None:
        raise RuntimeError("Gold publication timestamp must include a timezone")
    age_seconds = (
        datetime.now(timezone.utc) - published_at.astimezone(timezone.utc)
    ).total_seconds()
    max_age = max(
        60,
        int(os.environ.get("BIGQUERY_TALENT_9BOX_MAX_SNAPSHOT_AGE_SECONDS") or 86_400),
    )
    if age_seconds < -300 or age_seconds > max_age:
        raise RuntimeError("Gold publication timestamp is outside the freshness window")
    if set(baseline["nine_box_counts"]) != set(BOX_KEYS):
        raise RuntimeError("baseline does not contain exactly nine cells")
    totals = baseline["totals"]
    if not isinstance(totals, dict) or set(totals) != {
        "population",
        "assigned_to_cell",
        "classified",
        "unclassified",
    }:
        raise RuntimeError("baseline totals contract is invalid")
    population = _strict_count(totals["population"], "population")
    classified = _strict_count(totals["classified"], "classified")
    assigned = _strict_count(totals["assigned_to_cell"], "assigned_to_cell")
    unclassified = _strict_count(totals["unclassified"], "unclassified")
    ready_sum = 0
    employee_sum = 0
    for item in baseline["nine_box_counts"].values():
        if not isinstance(item, dict) or set(item) != {
            "employee_count",
            "ready_count",
            "benchmark_count",
            "blocked_count",
            "box_status",
        }:
            raise RuntimeError("baseline cell contract is invalid")
        employee = _strict_count(item["employee_count"], "cell employee_count")
        ready = _strict_count(item["ready_count"], "cell ready_count")
        benchmark = _strict_count(item["benchmark_count"], "cell benchmark_count")
        blocked = _strict_count(item["blocked_count"], "cell blocked_count")
        if ready > employee or benchmark > ready or blocked != employee - ready:
            raise RuntimeError("baseline cell counts are inconsistent")
        if item["box_status"] != _cell_status(employee, ready, benchmark):
            raise RuntimeError("baseline cell status is inconsistent")
        ready_sum += ready
        employee_sum += employee
    if (
        ready_sum != classified
        or employee_sum != assigned
        or classified > population
        or assigned > population
        or unclassified != population - classified
    ):
        raise RuntimeError("baseline population totals are inconsistent")
    cohorts = baseline["cohorts"]
    if not isinstance(cohorts, dict) or set(cohorts) != {"high", "medium", "low"}:
        raise RuntimeError("baseline cohort contract is invalid")
    cohort_total = sum(
        _strict_count(cohorts[key], f"cohort {key}")
        for key in ("high", "medium", "low")
    )
    if cohort_total > population:
        raise RuntimeError("baseline cohort counts exceed population")
    blockers = baseline["blockers"]
    if not isinstance(blockers, dict) or set(blockers) != {
        "not_classified",
        "assigned_cell_not_ready",
    }:
        raise RuntimeError("baseline blocker contract is invalid")
    if (
        _strict_count(blockers["not_classified"], "not_classified")
        != population - classified
        or _strict_count(
            blockers["assigned_cell_not_ready"], "assigned_cell_not_ready"
        )
        != employee_sum - ready_sum
    ):
        raise RuntimeError("baseline blocker counts are inconsistent")
    verification = baseline["verification_context"]
    if not isinstance(verification, dict) or set(verification) != {
        "ruleset",
        "benchmark_authority_valid",
        "benchmark_head",
        "relation_schema_digest",
    }:
        raise RuntimeError("baseline verification context is invalid")
    if (
        verification["ruleset"] != "successfactors_talent_population/v1"
        or not isinstance(verification["benchmark_authority_valid"], bool)
        or verification["relation_schema_digest"] != manifest["schema_digest"]
        or not isinstance(verification["benchmark_head"], str)
    ):
        raise RuntimeError("baseline verification context is inconsistent")
    if verification["benchmark_head"]:
        try:
            uuid.UUID(verification["benchmark_head"])
        except ValueError as exc:
            raise RuntimeError("benchmark publication head is invalid") from exc
    unsigned = {key: value for key, value in baseline.items() if key != "digest"}
    if digest(unsigned) != baseline["digest"]:
        raise RuntimeError("PostgreSQL baseline digest mismatch")
    if not audit_no_pii_payload(baseline)["passed"]:
        raise RuntimeError("PostgreSQL baseline contains prohibited person metadata")


def fetch_postgres_baseline(config: ShadowRunConfig) -> dict[str, Any]:
    response = requests.post(
        f"{config.console_url}/internal/intelligence/bigquery-shadow/talent-9box-baseline",
        headers={
            "X-Internal-Service": "airflow",
            "X-API-Key": _internal_key("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE"),
        },
        json={
            "tenant_id": config.tenant_id,
            "workspace_id": config.workspace_id,
            "cartridge_id": config.cartridge_id,
            "dataset": config.source_dataset,
            "pipeline_run_id": config.pipeline_run_id,
            "source_run_id": config.source_run_id,
        },
        timeout=(10, 150),
    )
    if response.status_code != 200:
        raise RuntimeError(f"PostgreSQL baseline request failed with HTTP {response.status_code}")
    try:
        baseline = response.json()
    except ValueError as exc:
        raise RuntimeError("PostgreSQL baseline response is not JSON") from exc
    validate_baseline(baseline, config)
    return baseline


def google_clients(config: ShadowRunConfig):
    import google.auth
    from google.auth import impersonated_credentials
    from google.cloud import bigquery, storage

    source_credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials = impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=config.service_account,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        lifetime=900,
    )
    return (
        bigquery.Client(
            project=config.project_id,
            credentials=credentials,
            location=config.location,
        ),
        storage.Client(project=config.project_id, credentials=credentials),
    )


def _job_config_section(value: Any, section: str) -> dict[str, Any]:
    """Extract one API config section for retry identity verification."""
    try:
        representation = value.to_api_repr()
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("existing BigQuery job configuration is unavailable") from exc
    if not isinstance(representation, dict):
        raise RuntimeError("existing BigQuery job configuration is unavailable")
    configuration = representation.get("configuration", representation)
    payload = configuration.get(section) if isinstance(configuration, dict) else None
    if not isinstance(payload, dict):
        raise RuntimeError("existing BigQuery job configuration is unavailable")
    return payload


def verify_gcs_object(
    config: ShadowRunConfig,
    baseline: dict[str, Any],
    storage_client: Any,
) -> dict[str, Any]:
    validate_baseline(baseline, config)
    manifest = baseline["manifest"]
    blob = storage_client.bucket(config.gold_bucket).blob(manifest["object_key"])
    generation = int(manifest["object_version"])
    try:
        # This is intentionally the live head, not a retained historic version.
        blob.reload(if_generation_match=generation)
    except Exception as exc:
        raise RuntimeError("Gold object live generation changed or is unavailable") from exc
    metadata = blob.metadata if isinstance(blob.metadata, dict) else {}
    metadata_checksum = str(metadata.get("omega-sha256") or "").lower()
    content_digest = hashlib.sha256()
    try:
        with blob.open(
            "rb", chunk_size=8 * 1024 * 1024, if_generation_match=generation
        ) as handle:
            while True:
                chunk = handle.read(8 * 1024 * 1024)
                if not chunk:
                    break
                content_digest.update(chunk)
    except Exception as exc:
        raise RuntimeError("Gold object checksum verification failed") from exc
    checksum = content_digest.hexdigest()
    if (
        int(blob.generation or 0) != generation
        or metadata_checksum != manifest["checksum"]
        or checksum != manifest["checksum"]
    ):
        raise RuntimeError("Gold object generation or checksum changed")
    evidence = {
        "object_version": str(generation),
        "checksum": checksum,
        "content_length": int(blob.size or 0),
        "verified": True,
    }
    if evidence["content_length"] <= 0:
        raise RuntimeError("Gold object is empty")
    result = {**evidence, "verification_digest": digest(evidence)}
    if not audit_no_pii_payload(result)["passed"]:
        raise RuntimeError("GCS verification metadata contains prohibited PII")
    return result


def load_immutable_table(
    config: ShadowRunConfig,
    baseline: dict[str, Any],
    bigquery_client: Any,
) -> dict[str, Any]:
    from google.api_core.exceptions import Conflict
    from google.cloud import bigquery

    validate_baseline(baseline, config)
    manifest = baseline["manifest"]
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        autodetect=True,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        write_disposition=bigquery.WriteDisposition.WRITE_EMPTY,
    )
    labels = {
        "omega_workload": "talent_9box_shadow",
        "omega_evidence": manifest["evidence_digest"][:32],
    }
    job_config.labels = labels
    job_id = _load_job_id(config)
    try:
        job = bigquery_client.load_table_from_uri(
            manifest["uri"],
            config.qualified_table,
            job_id=job_id,
            location=config.location,
            job_config=job_config,
        )
    except Conflict:
        job = bigquery_client.get_job(
            job_id, project=config.project_id, location=config.location
        )
        destination = getattr(job, "destination", None)
        destination_id = ".".join(
            str(getattr(destination, field, "") or "")
            for field in ("project", "dataset_id", "table_id")
        )
        expected_load = _job_config_section(job_config, "load")
        actual_load = _job_config_section(job, "load")
        identity_fields = (
            "sourceFormat",
            "createDisposition",
            "writeDisposition",
            "autodetect",
        )
        if (
            destination_id != config.qualified_table
            or list(getattr(job, "source_uris", None) or []) != [manifest["uri"]]
            or (getattr(job, "labels", None) or {}).get("omega_evidence")
            != labels["omega_evidence"]
            or any(
                actual_load.get(field) != expected_load.get(field)
                for field in identity_fields
            )
        ):
            raise RuntimeError("existing BigQuery load job does not match the publication")
    job.result(timeout=900)
    table = bigquery_client.get_table(config.qualified_table)
    output_rows = _strict_count(
        getattr(job, "output_rows", None), "BigQuery load output rows"
    )
    if output_rows != int(manifest["row_count"]):
        raise RuntimeError("BigQuery load row count does not match Gold")
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=24)
    if table.expires is None or table.expires <= now or table.expires > expiry:
        table.expires = expiry
        table = bigquery_client.update_table(table, ["expires"])
    names = {str(field.name).lower() for field in table.schema}
    required = {
        "tenant_id",
        "workspace_id",
        "invalid_score_input",
        "performance_score",
        "potential_score",
        "box_status",
        "box_key",
    }
    if not required.issubset(names):
        raise RuntimeError("BigQuery table does not satisfy the real 9-Box contract")
    result = {
        "table_id": config.table_id,
        "load_job_id": str(job.job_id),
        "output_rows": output_rows,
        "query_fields": {field: field in names for field in sorted(_QUERY_FIELDS)},
        "expires_at": table.expires.astimezone(timezone.utc).isoformat(),
    }
    if not audit_no_pii_payload(result)["passed"]:
        raise RuntimeError("BigQuery load metadata contains prohibited PII")
    return result


def _load_job_id(config: ShadowRunConfig) -> str:
    return f"omega_talent_load_{digest({'source': config.source_run_id})[:24]}"


def _dry_run_job_id(
    config: ShadowRunConfig, baseline: dict[str, Any], query_digest: str
) -> str:
    return (
        "omega_talent_dryrun_"
        + digest(
            {
                "source": config.source_run_id,
                "query": query_digest,
                "baseline": baseline["digest"],
            }
        )[:24]
    )


def _query_job_id(
    config: ShadowRunConfig, baseline: dict[str, Any], query_digest: str
) -> str:
    return (
        "omega_talent_query_"
        + digest(
            {
                "source": config.source_run_id,
                "query": query_digest,
                "baseline": baseline["digest"],
            }
        )[:24]
    )


def _bq_score(column: str) -> str:
    value = f"SAFE_CAST(`{column}` AS FLOAT64)"
    return (
        f"({value} IS NOT NULL AND NOT IFNULL(IS_NAN({value}), TRUE) "
        f"AND NOT IFNULL(IS_INF({value}), TRUE) AND {value} >= 0 AND {value} <= 100)"
    )


def _query_plan(
    config: ShadowRunConfig,
    loaded: dict[str, Any],
) -> dict[str, Any]:
    raw_fields = loaded.get("query_fields")
    if (
        not isinstance(raw_fields, dict)
        or set(raw_fields) != set(_QUERY_FIELDS)
        or any(not isinstance(value, bool) for value in raw_fields.values())
    ):
        raise RuntimeError("BigQuery query-field contract is invalid")
    available = {name for name in _QUERY_FIELDS if raw_fields[name]}
    required = {
        "tenant_id",
        "workspace_id",
        "invalid_score_input",
        "performance_score",
        "potential_score",
        "box_status",
        "box_key",
    }
    if not required.issubset(available):
        raise RuntimeError("BigQuery table does not satisfy the real 9-Box contract")
    claims: list[str] = []
    if "source_mode" in available:
        claims.append("CAST(`source_mode` AS STRING) = 'benchmark_internal'")
    if "readiness_status" in available:
        claims.append("CAST(`readiness_status` AS STRING) = 'benchmark_internal'")
    if "box_status" in available:
        claims.append("CAST(`box_status` AS STRING) = 'benchmark_internal'")
    for field in ("benchmark_raw_score", "benchmark_score"):
        if field in available:
            claims.append(f"`{field}` IS NOT NULL")
    for field in ("readiness_benchmark_count", "benchmark_count"):
        if field in available:
            claims.append(f"COALESCE(SAFE_CAST(`{field}` AS INT64), 0) > 0")
    if "benchmark_provenance_status" in available:
        claims.append(
            "COALESCE(CAST(`benchmark_provenance_status` AS STRING), '') "
            "NOT IN ('', 'approved_durable', 'not_applicable')"
        )
    claims_sql = "(" + " OR ".join(claims) + ")" if claims else "FALSE"
    durable_fields = {
        "benchmark_approval_valid",
        "benchmark_provenance_status",
        "benchmark_materialization_head",
    }
    durable_sql = "FALSE"
    if durable_fields.issubset(available):
        durable_sql = (
            "(@benchmark_authority_valid "
            "AND SAFE_CAST(`benchmark_approval_valid` AS BOOL) IS TRUE "
            "AND CAST(`benchmark_provenance_status` AS STRING)='approved_durable' "
            "AND COALESCE(CAST(`benchmark_materialization_head` AS STRING),'')=@benchmark_head)"
        )
    not_degraded = f"(NOT {claims_sql} OR {durable_sql})"
    ready = (
        "SAFE_CAST(`invalid_score_input` AS BOOL) IS FALSE "
        f"AND {_bq_score('performance_score')} "
        f"AND {_bq_score('potential_score')} "
        "AND LOWER(COALESCE(CAST(`box_status` AS STRING),''))='ready'"
    )
    benchmark = (
        f"({ready}) AND CAST(`source_mode` AS STRING)='benchmark_internal'"
        if "source_mode" in available
        else "FALSE"
    )
    band_source = (
        "CASE WHEN CAST(`performance_band_available` AS STRING) IN "
        "('high','medium','low') THEN CAST(`performance_band_available` AS STRING) END"
        if "performance_band_available" in available
        else "NULL"
    )
    score = "SAFE_CAST(`performance_score` AS FLOAT64)"
    band = (
        f"COALESCE({band_source}, CASE "
        f"WHEN (CASE WHEN {score}>5 THEN {score}/20.0 ELSE {score} END)>=4 THEN 'high' "
        f"WHEN (CASE WHEN {score}>5 THEN {score}/20.0 ELSE {score} END)>=3 THEN 'medium' "
        "ELSE 'low' END)"
    )
    projection = ", ".join(f"`{field}`" for field in sorted(available))
    scoped = (
        f"SELECT {projection} FROM `{config.qualified_table}` "
        "WHERE CAST(`tenant_id` AS STRING)=@tenant_id "
        "AND CAST(`workspace_id` AS STRING)=@workspace_id"
    )
    sql = f"""
        WITH scoped AS ({scoped}),
        cohort AS (
          SELECT {band} AS band
            FROM scoped
           WHERE SAFE_CAST(`invalid_score_input` AS BOOL) IS FALSE
             AND {_bq_score('performance_score')}
        )
        SELECT 'population' AS kind, '' AS bucket,
               COUNT(*) AS employee_count, 0 AS ready_count,
               0 AS benchmark_count, 0 AS blocked_count
          FROM scoped
        UNION ALL
        SELECT 'cell' AS kind, LOWER(CAST(`box_key` AS STRING)) AS bucket,
               COUNT(*) AS employee_count,
               COUNTIF({ready}) AS ready_count,
               COUNTIF({benchmark}) AS benchmark_count,
               COUNT(*) - COUNTIF({ready}) AS blocked_count
          FROM scoped
         WHERE `box_key` IS NOT NULL AND {not_degraded}
         GROUP BY bucket
        UNION ALL
        SELECT 'cohort' AS kind, band AS bucket, COUNT(*) AS employee_count,
               0 AS ready_count, 0 AS benchmark_count, 0 AS blocked_count
          FROM cohort GROUP BY band
    """.strip()
    return {"sql": sql, "query_digest": digest(sql)}


def _query_job_config(
    config: ShadowRunConfig,
    baseline: dict[str, Any],
    *,
    dry_run: bool,
    query_digest: str,
):
    from google.cloud import bigquery

    verification = baseline["verification_context"]
    return bigquery.QueryJobConfig(
        dry_run=dry_run,
        use_legacy_sql=False,
        use_query_cache=False,
        maximum_bytes_billed=config.maximum_bytes_billed,
        query_parameters=[
            bigquery.ScalarQueryParameter("tenant_id", "STRING", config.tenant_id),
            bigquery.ScalarQueryParameter(
                "workspace_id", "STRING", config.workspace_id
            ),
            bigquery.ScalarQueryParameter(
                "benchmark_authority_valid",
                "BOOL",
                bool(verification["benchmark_authority_valid"]),
            ),
            bigquery.ScalarQueryParameter(
                "benchmark_head", "STRING", str(verification["benchmark_head"] or "")
            ),
        ],
        labels={
            "omega_workload": "talent_9box_shadow",
            "omega_query": query_digest[:32],
            "omega_baseline": str(baseline["digest"])[:32],
        },
    )


def dry_run_aggregate_query(
    config: ShadowRunConfig,
    baseline: dict[str, Any],
    loaded: dict[str, Any],
    bigquery_client: Any,
) -> dict[str, Any]:
    from google.api_core.exceptions import Conflict

    validate_baseline(baseline, config)
    plan = _query_plan(config, loaded)
    job_id = _dry_run_job_id(config, baseline, plan["query_digest"])
    job_config = _query_job_config(
        config,
        baseline,
        dry_run=True,
        query_digest=plan["query_digest"],
    )
    try:
        job = bigquery_client.query(
            plan["sql"],
            job_id=job_id,
            job_config=job_config,
            location=config.location,
        )
    except Conflict:
        job = bigquery_client.get_job(
            job_id, project=config.project_id, location=config.location
        )
        expected_query = _job_config_section(job_config, "query")
        actual_query = _job_config_section(job, "query")
        identity_fields = (
            "queryParameters",
            "maximumBytesBilled",
            "useLegacySql",
            "useQueryCache",
        )
        if (
            getattr(job, "dry_run", None) is not True
            or str(getattr(job, "query", "") or "") != plan["sql"]
            or (getattr(job, "labels", None) or {}).get("omega_query")
            != plan["query_digest"][:32]
            or (getattr(job, "labels", None) or {}).get("omega_baseline")
            != str(baseline["digest"])[:32]
            or any(
                canonical_json(actual_query.get(field))
                != canonical_json(expected_query.get(field))
                for field in identity_fields
            )
        ):
            raise RuntimeError(
                "existing BigQuery dry-run job does not match the parity run"
            )
    processed = _strict_count(
        getattr(job, "total_bytes_processed", None),
        "BigQuery dry-run processed bytes",
    )
    if processed > config.maximum_bytes_billed:
        raise RuntimeError("BigQuery dry-run exceeds maximum_bytes_billed")
    if str(job.job_id or "") != job_id:
        raise RuntimeError("BigQuery dry-run job identity is unavailable")
    result = {
        **plan,
        "dry_run_job_id": job_id,
        "dry_run_bytes": processed,
    }
    if not audit_no_pii_payload(result)["passed"]:
        raise RuntimeError("BigQuery dry-run metadata contains prohibited PII")
    return result


def _cell_status(employee: int, ready: int, benchmark: int) -> str:
    if employee == 0:
        return "empty"
    if benchmark and ready:
        return "benchmark_internal"
    return "ready" if ready else "blocked"


def execute_aggregate_query(
    config: ShadowRunConfig,
    baseline: dict[str, Any],
    loaded: dict[str, Any],
    query_plan: dict[str, Any],
    bigquery_client: Any,
) -> dict[str, Any]:
    from google.api_core.exceptions import Conflict

    expected_plan = _query_plan(config, loaded)
    query_plan = _validate_dry_run_artifact(
        query_plan,
        config=config,
        baseline=baseline,
        loaded=loaded,
    )
    query_digest = expected_plan["query_digest"]
    job_id = _query_job_id(config, baseline, query_digest)
    job_config = _query_job_config(
        config, baseline, dry_run=False, query_digest=query_digest
    )
    try:
        job = bigquery_client.query(
            query_plan["sql"],
            job_id=job_id,
            job_config=job_config,
            location=config.location,
        )
    except Conflict:
        job = bigquery_client.get_job(
            job_id, project=config.project_id, location=config.location
        )
        expected_query = _job_config_section(job_config, "query")
        actual_query = _job_config_section(job, "query")
        identity_fields = (
            "queryParameters",
            "maximumBytesBilled",
            "useLegacySql",
            "useQueryCache",
        )
        if (
            str(getattr(job, "query", "") or "") != query_plan["sql"]
            or (getattr(job, "labels", None) or {}).get("omega_query")
            != query_digest[:32]
            or (getattr(job, "labels", None) or {}).get("omega_baseline")
            != str(baseline["digest"])[:32]
            or any(
                canonical_json(actual_query.get(field))
                != canonical_json(expected_query.get(field))
                for field in identity_fields
            )
        ):
            raise RuntimeError("existing BigQuery query job does not match the parity run")

    rows = job.result(timeout=900)
    if str(job.job_id or "") != job_id:
        raise RuntimeError("BigQuery query job identity is unavailable")
    cells: dict[str, dict[str, Any]] = {
        key: {
            "employee_count": 0,
            "ready_count": 0,
            "benchmark_count": 0,
            "blocked_count": 0,
            "box_status": "empty",
        }
        for key in BOX_KEYS
    }
    cohorts = {"high": 0, "medium": 0, "low": 0}
    population_rows: list[int] = []
    seen_cells: set[str] = set()
    seen_cohorts: set[str] = set()
    for row in rows:
        kind = str(row["kind"] or "")
        bucket = str(row["bucket"] or "").strip().lower()
        employee = _strict_count(
            row["employee_count"], "BigQuery employee count"
        )
        ready = _strict_count(row["ready_count"], "BigQuery ready count")
        benchmark = _strict_count(
            row["benchmark_count"], "BigQuery benchmark count"
        )
        blocked = _strict_count(
            row["blocked_count"], "BigQuery blocked count"
        )
        if kind == "population":
            if ready or benchmark or blocked:
                raise RuntimeError("BigQuery population aggregate is malformed")
            population_rows.append(employee)
        elif kind == "cell":
            if bucket == "insufficient_data":
                continue
            if bucket not in cells:
                raise RuntimeError("BigQuery returned an unknown 9-Box cell")
            if bucket in seen_cells:
                raise RuntimeError("BigQuery returned a duplicate 9-Box cell")
            seen_cells.add(bucket)
            if ready > employee or benchmark > ready or blocked != employee - ready:
                raise RuntimeError("BigQuery returned inconsistent cell counts")
            cells[bucket] = {
                "employee_count": employee,
                "ready_count": ready,
                "benchmark_count": benchmark,
                "blocked_count": blocked,
                "box_status": _cell_status(employee, ready, benchmark),
            }
        elif kind == "cohort":
            if bucket not in cohorts:
                raise RuntimeError("BigQuery returned an unknown performance cohort")
            if bucket in seen_cohorts:
                raise RuntimeError("BigQuery returned a duplicate performance cohort")
            if ready or benchmark or blocked:
                raise RuntimeError("BigQuery performance cohort is malformed")
            seen_cohorts.add(bucket)
            cohorts[bucket] = employee
        else:
            raise RuntimeError("BigQuery returned an unknown aggregate row")
    if len(population_rows) != 1:
        raise RuntimeError("BigQuery population aggregate is incomplete")
    population = population_rows[0]
    assigned = sum(int(item["employee_count"]) for item in cells.values())
    classified = sum(int(item["ready_count"]) for item in cells.values())
    cell_blocked = sum(int(item["blocked_count"]) for item in cells.values())
    if assigned > population or classified > population or sum(cohorts.values()) > population:
        raise RuntimeError("BigQuery aggregate exceeds physical population")
    aggregate = {
        "nine_box_counts": cells,
        "cohorts": cohorts,
        "totals": {
            "population": population,
            "assigned_to_cell": assigned,
            "classified": classified,
            "unclassified": population - classified,
        },
        "blockers": {
            "not_classified": population - classified,
            "assigned_cell_not_ready": cell_blocked,
        },
    }
    processed = _strict_count(
        getattr(job, "total_bytes_processed", None),
        "BigQuery query processed bytes",
    )
    billed = _strict_count(
        getattr(job, "total_bytes_billed", None),
        "BigQuery query billed bytes",
    )
    if processed > config.maximum_bytes_billed or billed > config.maximum_bytes_billed:
        raise RuntimeError("BigQuery query exceeded the per-job cost cap")
    result = {
        **aggregate,
        "aggregate_digest": digest(aggregate),
        "query_job_id": str(job.job_id),
        "query_bytes": processed,
        "bytes_billed": billed,
    }
    if not audit_no_pii_payload(result)["passed"]:
        raise RuntimeError("BigQuery result metadata contains prohibited PII")
    return result


def compare_aggregates(
    baseline: dict[str, Any], bigquery_result: dict[str, Any]
) -> dict[str, Any]:
    keys = ("nine_box_counts", "cohorts", "totals", "blockers")
    differences: list[dict[str, str]] = []
    for key in keys:
        if canonical_json(baseline[key]) != canonical_json(bigquery_result[key]):
            differences.append(
                {
                    "component": key,
                    "postgres_digest": digest(baseline[key]),
                    "bigquery_digest": digest(bigquery_result[key]),
                }
            )
    comparison = {
        "parity": not differences,
        "differences": differences,
        "postgres_digest": digest({key: baseline[key] for key in keys}),
        "bigquery_digest": digest({key: bigquery_result[key] for key in keys}),
    }
    result = {**comparison, "comparison_digest": digest(comparison)}
    if not audit_no_pii_payload(result)["passed"]:
        raise RuntimeError("comparison metadata contains prohibited PII")
    return result


def combined_pii_audit(
    *,
    payloads: list[Any],
    base_log_folder: str,
    dag_id: str,
    run_id: str,
) -> dict[str, Any]:
    xcom = audit_no_pii_payload(*payloads)
    logs = scan_airflow_logs(
        base_log_folder, dag_id=dag_id, run_id=run_id
    )
    evidence = {
        "xcom": xcom,
        "logs": logs,
        "passed": bool(xcom["passed"] and logs["passed"]),
    }
    return {**evidence, "digest": digest(evidence)}


def _registry_response(response: Any, expected_status: str) -> None:
    if response.status_code != 200:
        raise RuntimeError(f"pipeline evidence registry failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("pipeline evidence registry returned invalid JSON") from exc
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict) or result.get("status") != expected_status:
        raise RuntimeError("pipeline evidence registry did not persist the shadow outcome")


def _validate_source_verification(
    value: Any,
    *,
    manifest: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    expected_keys = {
        "object_version",
        "checksum",
        "content_length",
        "verified",
        "verification_digest",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeError(f"{label} GCS verification contract is invalid")
    evidence = {key: value[key] for key in expected_keys - {"verification_digest"}}
    if (
        value["verified"] is not True
        or value["object_version"] != manifest["object_version"]
        or value["checksum"] != manifest["checksum"]
        or _strict_count(value["content_length"], f"{label} content length") <= 0
        or not _SHA256.fullmatch(str(value["verification_digest"] or ""))
        or digest(evidence) != value["verification_digest"]
    ):
        raise RuntimeError(f"{label} GCS verification is invalid")
    return dict(value)


def _validate_loaded_artifact(
    value: Any,
    *,
    config: ShadowRunConfig,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    expected_keys = {
        "table_id",
        "load_job_id",
        "output_rows",
        "query_fields",
        "expires_at",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeError("BigQuery load contract is invalid")
    if (
        value["table_id"] != config.table_id
        or _required_token(value["load_job_id"], "BigQuery load job")
        != _load_job_id(config)
        or _strict_count(value["output_rows"], "BigQuery output rows")
        != manifest["row_count"]
    ):
        raise RuntimeError("BigQuery load is not bound to the Gold publication")
    fields = value["query_fields"]
    if (
        not isinstance(fields, dict)
        or set(fields) != set(_QUERY_FIELDS)
        or any(not isinstance(item, bool) for item in fields.values())
    ):
        raise RuntimeError("BigQuery load query-field contract is invalid")
    try:
        expires_at = datetime.fromisoformat(
            str(value["expires_at"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise RuntimeError("BigQuery table expiry is invalid") from exc
    if expires_at.tzinfo is None:
        raise RuntimeError("BigQuery table expiry must include a timezone")
    now = datetime.now(timezone.utc)
    expires_at = expires_at.astimezone(timezone.utc)
    if expires_at <= now - timedelta(minutes=5) or expires_at > now + timedelta(
        hours=25
    ):
        raise RuntimeError("BigQuery table expiry is outside the pilot TTL")
    return dict(value)


def _validate_dry_run_artifact(
    value: Any,
    *,
    config: ShadowRunConfig,
    baseline: dict[str, Any],
    loaded: dict[str, Any],
) -> dict[str, Any]:
    expected_plan = _query_plan(config, loaded)
    if not isinstance(value, dict) or set(value) != {
        "sql",
        "query_digest",
        "dry_run_job_id",
        "dry_run_bytes",
    }:
        raise RuntimeError("BigQuery dry-run contract is invalid")
    dry_bytes = _strict_count(value["dry_run_bytes"], "BigQuery dry-run bytes")
    if (
        value["sql"] != expected_plan["sql"]
        or value["query_digest"] != expected_plan["query_digest"]
        or not _SHA256.fullmatch(str(value["query_digest"] or ""))
        or _required_token(value["dry_run_job_id"], "BigQuery dry-run job")
        != _dry_run_job_id(config, baseline, expected_plan["query_digest"])
        or dry_bytes > config.maximum_bytes_billed
    ):
        raise RuntimeError("BigQuery dry-run evidence is invalid")
    return dict(value)


def _validate_query_artifact(
    value: Any,
    *,
    config: ShadowRunConfig,
    baseline: dict[str, Any],
    query_digest: str,
) -> dict[str, Any]:
    aggregate_keys = {"nine_box_counts", "cohorts", "totals", "blockers"}
    expected_keys = aggregate_keys | {
        "aggregate_digest",
        "query_job_id",
        "query_bytes",
        "bytes_billed",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise RuntimeError("BigQuery aggregate result contract is invalid")
    aggregate = {key: value[key] for key in aggregate_keys}
    candidate_envelope = {
        "manifest": baseline["manifest"],
        **aggregate,
        "verification_context": baseline["verification_context"],
    }
    candidate = {
        **candidate_envelope,
        "digest": digest(candidate_envelope),
    }
    validate_baseline(candidate, config)
    if candidate["totals"]["population"] != baseline["manifest"]["row_count"]:
        raise RuntimeError("BigQuery population does not match the loaded Gold rows")
    query_bytes = _strict_count(value["query_bytes"], "BigQuery query bytes")
    billed = _strict_count(value["bytes_billed"], "BigQuery billed bytes")
    if (
        value["aggregate_digest"] != digest(aggregate)
        or not _SHA256.fullmatch(str(value["aggregate_digest"] or ""))
        or _required_token(value["query_job_id"], "BigQuery query job")
        != _query_job_id(config, baseline, query_digest)
        or query_bytes > config.maximum_bytes_billed
        or billed > config.maximum_bytes_billed
    ):
        raise RuntimeError("BigQuery query evidence is invalid")
    return dict(value)


def _validate_comparison_artifact(
    value: Any,
    *,
    baseline: dict[str, Any],
    bigquery_result: dict[str, Any],
) -> dict[str, Any]:
    expected = compare_aggregates(baseline, bigquery_result)
    if (
        not isinstance(value, dict)
        or set(value) != set(expected)
        or canonical_json(value) != canonical_json(expected)
    ):
        raise RuntimeError("BigQuery comparison evidence is invalid")
    return dict(value)


def _validate_pii_audit_artifact(
    value: Any,
    *,
    payloads: list[Any],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"xcom", "logs", "passed", "digest"}:
        raise RuntimeError("PII audit contract is invalid")
    expected_xcom = audit_no_pii_payload(*payloads)
    if canonical_json(value["xcom"]) != canonical_json(expected_xcom):
        raise RuntimeError("PII XCom audit evidence is invalid")
    logs = value["logs"]
    if not isinstance(logs, dict) or set(logs) != {
        "scanner",
        "files_scanned",
        "violations",
        "passed",
        "status",
        "digest",
    }:
        raise RuntimeError("PII log audit evidence is invalid")
    unsigned_logs = {key: item for key, item in logs.items() if key != "digest"}
    files_scanned = _strict_count(logs["files_scanned"], "PII log files scanned")
    violations = _strict_count(logs["violations"], "PII log violations")
    expected_log_pass = files_scanned > 0 and violations == 0
    if (
        logs["scanner"] != "omega-shadow-local-log/v1"
        or logs["status"] not in {"complete", "logs_not_yet_visible", "log_folder_unavailable"}
        or logs["passed"] is not expected_log_pass
        or digest(unsigned_logs) != logs["digest"]
    ):
        raise RuntimeError("PII log audit evidence is invalid")
    unsigned = {key: item for key, item in value.items() if key != "digest"}
    expected_pass = bool(expected_xcom["passed"] and logs["passed"])
    if value["passed"] is not expected_pass or digest(unsigned) != value["digest"]:
        raise RuntimeError("combined PII audit evidence is invalid")
    return dict(value)


def _verified_shadow_artifacts(
    config: ShadowRunConfig,
    *,
    baseline: Any,
    source_before: Any,
    loaded: Any,
    dry_run: Any,
    bigquery_result: Any,
    source_after: Any,
    comparison: Any,
    pii_audit: Any,
) -> dict[str, Any]:
    validate_baseline(baseline, config)
    manifest = baseline["manifest"]
    before = _validate_source_verification(
        source_before, manifest=manifest, label="pre-load"
    )
    after = _validate_source_verification(
        source_after, manifest=manifest, label="post-query"
    )
    if canonical_json(before) != canonical_json(after):
        raise RuntimeError("Gold object changed between load and comparison")
    checked_load = _validate_loaded_artifact(
        loaded, config=config, manifest=manifest
    )
    checked_dry_run = _validate_dry_run_artifact(
        dry_run,
        config=config,
        baseline=baseline,
        loaded=checked_load,
    )
    checked_query = _validate_query_artifact(
        bigquery_result,
        config=config,
        baseline=baseline,
        query_digest=checked_dry_run["query_digest"],
    )
    checked_comparison = _validate_comparison_artifact(
        comparison, baseline=baseline, bigquery_result=checked_query
    )
    checked_audit = _validate_pii_audit_artifact(
        pii_audit,
        payloads=[
            baseline,
            before,
            checked_load,
            checked_dry_run,
            checked_query,
            after,
            checked_comparison,
        ],
    )
    return {
        "baseline": baseline,
        "source_before": before,
        "loaded": checked_load,
        "dry_run": checked_dry_run,
        "bigquery_result": checked_query,
        "source_after": after,
        "comparison": checked_comparison,
        "pii_audit": checked_audit,
    }


def record_shadow_evidence(
    config: ShadowRunConfig,
    *,
    airflow_dag_run_id: str,
    baseline: dict[str, Any] | None,
    source_before: dict[str, Any] | None,
    loaded: dict[str, Any] | None,
    dry_run: dict[str, Any] | None,
    bigquery_result: dict[str, Any] | None,
    source_after: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
    pii_audit: dict[str, Any] | None,
) -> dict[str, Any]:
    from runtime_security_context import build_pipeline_run_context

    try:
        if airflow_dag_run_id != f"shadow__{config.source_run_id}":
            raise RuntimeError("Airflow shadow run identity is invalid")
        if not 0 < config.maximum_bytes_billed <= TEN_GIB:
            raise RuntimeError("shadow byte cap is invalid")
        verified = _verified_shadow_artifacts(
            config,
            baseline=baseline,
            source_before=source_before,
            loaded=loaded,
            dry_run=dry_run,
            bigquery_result=bigquery_result,
            source_after=source_after,
            comparison=comparison,
            pii_audit=pii_audit,
        )
    except Exception:
        verified = None

    parity = bool(verified and verified["comparison"]["parity"] is True)
    audit_passed = bool(verified and verified["pii_audit"]["passed"] is True)
    status = "success" if verified and parity and audit_passed else "failed"
    if verified:
        baseline = verified["baseline"]
        manifest = baseline["manifest"]
        loaded = verified["loaded"]
        dry_run = verified["dry_run"]
        bigquery_result = verified["bigquery_result"]
        comparison = verified["comparison"]
        pii_audit = verified["pii_audit"]
        source_before = verified["source_before"]
        source_after = verified["source_after"]
        contains_pii = bool(
            pii_audit["xcom"]["violations"] or pii_audit["logs"]["violations"]
        )
        extra = {
            "shadow_contract": "talent-9box-parity/v1",
            "contract_verified": True,
            "source_pipeline_run_id": config.pipeline_run_id,
            "source_run_id": config.source_run_id,
            "source_dataset": config.source_dataset,
            "bigquery_dataset": config.bigquery_dataset,
            "head_generation": manifest["head_generation"],
            "object_version": manifest["object_version"],
            "receipt_id": manifest["receipt_id"],
            "source_checksum": manifest["checksum"],
            "schema_digest": manifest["schema_digest"],
            "evidence_digest": manifest["evidence_digest"],
            "baseline_digest": baseline["digest"],
            "pre_load_verification_digest": source_before["verification_digest"],
            "post_query_verification_digest": source_after["verification_digest"],
            "load_job_id": loaded["load_job_id"],
            "dry_run_job_id": dry_run["dry_run_job_id"],
            "dry_run_bytes": dry_run["dry_run_bytes"],
            "query_digest": dry_run["query_digest"],
            "query_job_id": bigquery_result["query_job_id"],
            "query_bytes": bigquery_result["query_bytes"],
            "bytes_billed": bigquery_result["bytes_billed"],
            "aggregate_digest": bigquery_result["aggregate_digest"],
            "comparison_digest": comparison["comparison_digest"],
            "difference_components": [
                item["component"] for item in comparison["differences"]
            ],
            "parity": parity,
            "contains_pii": contains_pii,
            "pii_audit": pii_audit,
            "maximum_bytes_billed": config.maximum_bytes_billed,
        }
        record_count = baseline["totals"]["population"]
    else:
        extra = {
            "shadow_contract": "talent-9box-parity/v1",
            "contract_verified": False,
            "source_pipeline_run_id": config.pipeline_run_id,
            "source_run_id": config.source_run_id,
            "source_dataset": config.source_dataset,
            "bigquery_dataset": config.bigquery_dataset,
            "parity": False,
            "contains_pii": False,
            "maximum_bytes_billed": config.maximum_bytes_billed,
        }
        record_count = 0
    # This final payload is itself checked before it can be sent to the ledger.
    final_scan = audit_no_pii_payload(extra)
    if not final_scan["passed"]:
        status = "failed"
        extra = {
            "shadow_contract": "talent-9box-parity/v1",
            "contract_verified": False,
            "source_run_id": config.source_run_id,
            "source_dataset": config.source_dataset,
            "bigquery_dataset": config.bigquery_dataset,
            "parity": False,
            "contains_pii": True,
            "pii_audit": final_scan,
            "maximum_bytes_billed": config.maximum_bytes_billed,
        }
        record_count = 0
    now = datetime.now(timezone.utc).isoformat()
    args = {
        "run_id": f"bigquery_talent_9box_shadow:{config.source_run_id}",
        "dag_id": "bigquery_talent_9box_shadow",
        "cartridge_id": config.cartridge_id,
        "entity": "Talent9BoxShadow",
        "airflow_dag_run_id": airflow_dag_run_id,
        "mode": "shadow",
        "status": status,
        "started_at": now,
        "finished_at": now,
        "record_count": record_count,
        # Query billing is recorded explicitly in ``extra``; it is not a
        # storage-write byte count and must not be mislabeled here.
        "bytes_written": None,
        "storage_uri": f"bigquery://{config.qualified_table}",
        "tenant_id": config.tenant_id,
        "workspace_id": config.workspace_id,
        "project_id": config.project_id,
        "error_message": None if status == "success" else "shadow_verification_failed",
        "extra": extra,
    }
    response = requests.post(
        f"{config.mcp_infra_url}/mcp/invoke",
        headers={
            "X-Internal-Service": "airflow",
            "X-API-Key": _internal_key("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA"),
        },
        json={
            "tool": "pipeline_run_save",
            "args": args,
            "security_context": build_pipeline_run_context(args),
        },
        timeout=20,
    )
    _registry_response(response, status)
    return {
        "status": status,
        "parity": parity,
        "contains_pii": bool(extra["contains_pii"]),
        "comparison_digest": extra.get("comparison_digest"),
    }


def _stored_pii_audit_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"xcom", "logs", "passed", "digest"}:
        return False
    xcom = value.get("xcom")
    logs = value.get("logs")
    if (
        not isinstance(xcom, dict)
        or set(xcom)
        != {"scanner", "checked_nodes", "violations", "passed", "digest"}
        or not isinstance(logs, dict)
        or set(logs)
        != {
            "scanner",
            "files_scanned",
            "violations",
            "passed",
            "status",
            "digest",
        }
    ):
        return False
    try:
        checked_nodes = _strict_count(xcom["checked_nodes"], "stored XCom nodes")
        xcom_violations = _strict_count(
            xcom["violations"], "stored XCom violations"
        )
        files_scanned = _strict_count(
            logs["files_scanned"], "stored log files"
        )
        log_violations = _strict_count(
            logs["violations"], "stored log violations"
        )
    except RuntimeError:
        return False
    xcom_unsigned = {key: item for key, item in xcom.items() if key != "digest"}
    logs_unsigned = {key: item for key, item in logs.items() if key != "digest"}
    combined_unsigned = {key: item for key, item in value.items() if key != "digest"}
    return bool(
        xcom["scanner"] == "omega-shadow-xcom-metadata/v1"
        and checked_nodes > 0
        and xcom_violations == 0
        and xcom["passed"] is True
        and digest(xcom_unsigned) == xcom["digest"]
        and logs["scanner"] == "omega-shadow-local-log/v1"
        and files_scanned > 0
        and log_violations == 0
        and logs["passed"] is True
        and logs["status"] == "complete"
        and digest(logs_unsigned) == logs["digest"]
        and value["passed"] is True
        and digest(combined_unsigned) == value["digest"]
    )


def evaluate_promotion_gate(
    records: list[dict[str, Any]],
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    dataset_id: str,
    now: datetime | None = None,
    minimum_heads: int = 10,
) -> dict[str, Any]:
    """Evaluate saved seven-day evidence; this never changes serving config."""
    tenant_id = str(tenant_id or "").strip().lower()
    workspace_id = str(workspace_id or "").strip().lower()
    project_id = str(project_id or "").strip()
    dataset_id = str(dataset_id or "").strip()
    if (
        not _UUID.fullmatch(tenant_id)
        or not _UUID.fullmatch(workspace_id)
        or not _GCP_ID.fullmatch(project_id)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,1023}", dataset_id)
    ):
        raise RuntimeError("promotion gate scope is invalid")
    if (
        isinstance(minimum_heads, bool)
        or not isinstance(minimum_heads, int)
        or minimum_heads < 10
    ):
        raise RuntimeError("promotion gate requires at least ten distinct heads")
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise RuntimeError("promotion gate clock must include a timezone")
    clock = clock.astimezone(timezone.utc)
    oldest_allowed = clock - timedelta(days=8)
    eligible: list[dict[str, Any]] = []
    invalid_records = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        if (
            str(record.get("tenant_id") or "").lower() != tenant_id
            or str(record.get("workspace_id") or "").lower() != workspace_id
            or str(record.get("project_id") or "") != project_id
        ):
            continue
        # The registry contains many pipeline families.  Scope the evidence
        # set before judging its contract so unrelated DAGs/entities/modes (or
        # another environment's explicitly named dataset) cannot either count
        # toward or veto this promotion decision.  Missing/malformed dataset
        # metadata on an otherwise in-scope shadow record remains fail-closed.
        if (
            record.get("dag_id") != "bigquery_talent_9box_shadow"
            or record.get("cartridge_id") != "sap_successfactors"
            or record.get("entity") != "Talent9BoxShadow"
            or record.get("mode") != "shadow"
        ):
            continue
        extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
        explicit_dataset = str(extra.get("bigquery_dataset") or "")
        if (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,1023}", explicit_dataset)
            and explicit_dataset != dataset_id
        ):
            continue
        stamp = record.get("finished_at")
        if isinstance(stamp, str):
            try:
                stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                invalid_records += 1
                continue
        if not isinstance(stamp, datetime) or stamp.tzinfo is None:
            invalid_records += 1
            continue
        stamp = stamp.astimezone(timezone.utc)
        if stamp > clock:
            invalid_records += 1
            continue
        if stamp < oldest_allowed:
            continue
        audit = extra.get("pii_audit") if isinstance(extra.get("pii_audit"), dict) else {}
        try:
            source_run_id = str(uuid.UUID(str(extra.get("source_run_id") or "")))
            uuid.UUID(str(extra.get("receipt_id") or ""))
            head_generation = _strict_count(
                extra.get("head_generation"), "promotion head generation"
            )
            if head_generation <= 0:
                raise RuntimeError("promotion head generation is invalid")
            object_version = str(extra.get("object_version") or "")
            if not object_version.isdigit() or int(object_version) <= 0:
                raise RuntimeError("promotion object version is invalid")
            maximum = _strict_count(
                extra.get("maximum_bytes_billed"), "promotion byte cap"
            )
            dry_bytes = _strict_count(
                extra.get("dry_run_bytes"), "promotion dry-run bytes"
            )
            query_bytes = _strict_count(
                extra.get("query_bytes"), "promotion query bytes"
            )
            billed = _strict_count(
                extra.get("bytes_billed"), "promotion billed bytes"
            )
            record_count = _strict_count(
                record.get("record_count"), "promotion population"
            )
            digest_fields = (
                "source_checksum",
                "schema_digest",
                "evidence_digest",
                "baseline_digest",
                "pre_load_verification_digest",
                "post_query_verification_digest",
                "query_digest",
                "aggregate_digest",
                "comparison_digest",
            )
            digests_valid = all(
                _SHA256.fullmatch(str(extra.get(field) or ""))
                for field in digest_fields
            )
            identities_valid = all(
                _RUN_REF.fullmatch(str(extra.get(field) or ""))
                for field in ("load_job_id", "dry_run_job_id", "query_job_id")
            )
            audit_valid = _stored_pii_audit_is_valid(audit)
            valid = (
                record.get("status") == "success"
                and record.get("run_id")
                == f"bigquery_talent_9box_shadow:{source_run_id}"
                and record.get("airflow_dag_run_id") == f"shadow__{source_run_id}"
                and record.get("storage_uri")
                == (
                    f"bigquery://{project_id}.{dataset_id}."
                    f"talent_9box_{source_run_id.replace('-', '')}"
                )
                and record.get("bytes_written") is None
                and record.get("error_message") is None
                and record_count >= 0
                and extra.get("shadow_contract") == "talent-9box-parity/v1"
                and extra.get("contract_verified") is True
                and extra.get("source_run_id") == source_run_id
                and extra.get("source_dataset")
                == "sap_successfactors_talent_9box"
                and extra.get("bigquery_dataset") == dataset_id
                and _RUN_REF.fullmatch(
                    str(extra.get("source_pipeline_run_id") or "")
                )
                is not None
                and str(extra.get("source_pipeline_run_id") or "").startswith(
                    "dataset_refresh_chain:"
                )
                and extra.get("parity") is True
                and extra.get("difference_components") == []
                and extra.get("contains_pii") is False
                and audit_valid
                and digests_valid
                and extra.get("pre_load_verification_digest")
                == extra.get("post_query_verification_digest")
                and identities_valid
                and 0 < maximum <= TEN_GIB
                and dry_bytes <= maximum
                and query_bytes <= maximum
                and billed <= maximum
            )
        except (RuntimeError, TypeError, ValueError):
            valid = False
        if valid:
            eligible.append(record)
        else:
            invalid_records += 1
    heads = {
        str((record.get("extra") or {}).get("source_run_id") or "")
        for record in eligible
        if _UUID.fullmatch(
            str((record.get("extra") or {}).get("source_run_id") or "")
        )
    }
    timestamps = []
    for record in eligible:
        stamp = record.get("finished_at")
        if isinstance(stamp, str):
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        timestamps.append(stamp)
    observation_span_seconds = (
        int((max(timestamps) - min(timestamps)).total_seconds())
        if timestamps
        else 0
    )
    covers_seven_days = bool(
        timestamps
        and observation_span_seconds >= int(timedelta(days=7).total_seconds())
        and max(timestamps) >= clock - timedelta(days=1)
    )
    decision = {
        "eligible": (
            len(heads) >= minimum_heads
            and covers_seven_days
            and invalid_records == 0
        ),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "dataset": dataset_id,
        "source_dataset": "sap_successfactors_talent_9box",
        "distinct_gold_heads": len(heads),
        "invalid_records": invalid_records,
        "minimum_heads": minimum_heads,
        "window_days": 7,
        "observation_span_seconds": observation_span_seconds,
        "covers_seven_days": covers_seven_days,
        "serving_backend": "postgres_gold",
    }
    return {**decision, "digest": digest(decision)}


__all__ = (
    "BOX_KEYS",
    "ShadowRunConfig",
    "audit_no_pii_payload",
    "combined_pii_audit",
    "compare_aggregates",
    "dry_run_aggregate_query",
    "evaluate_promotion_gate",
    "execute_aggregate_query",
    "fetch_postgres_baseline",
    "google_clients",
    "load_immutable_table",
    "record_shadow_evidence",
    "scan_airflow_logs",
    "validate_baseline",
    "verify_gcs_object",
)
