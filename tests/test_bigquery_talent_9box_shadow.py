from __future__ import annotations

import ast
import hashlib
import inspect
import io
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from airflow.dags import _bigquery_talent_9box_shadow_runtime as runtime
from airflow.dags.dataset_refresh_bigquery_shadow import trigger_talent_9box_shadow
from console.app.services.gold_publication_relation import PublishedGoldRelation
from console.app.services.intelligence import bigquery_shadow
from console.app.services.intelligence.talent_population_backend import (
    public_talent_population_backend,
)
from console.app.routers import intelligence as intelligence_router


TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
SOURCE_RUN = "33333333-3333-5333-8333-333333333333"
PIPELINE_RUN = "dataset_refresh_chain:scheduled__2026-09-01T00:00:00Z"
OBJECT_BYTES = b"verified immutable parquet fixture"
CHECKSUM = hashlib.sha256(OBJECT_BYTES).hexdigest()


def _config() -> runtime.ShadowRunConfig:
    return runtime.ShadowRunConfig(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        cartridge_id="sap_successfactors",
        source_dataset="sap_successfactors_talent_9box",
        pipeline_run_id=PIPELINE_RUN,
        source_run_id=SOURCE_RUN,
        project_id="omega-shadow-pilot",
        bigquery_dataset="omega_staging_talent_shadow",
        location="us-central1",
        service_account="shadow@omega-shadow-pilot.iam.gserviceaccount.com",
        gold_bucket="omega-gold",
        maximum_bytes_billed=runtime.TEN_GIB,
        console_url="http://console:8000",
        mcp_infra_url="http://mcp-infra:8010",
    )


def _runtime_env() -> dict[str, str]:
    return {
        "BIGQUERY_TALENT_9BOX_SHADOW_ENABLED": "true",
        "TALENT_POPULATION_BACKEND": "postgres_gold",
        "BIGQUERY_TALENT_9BOX_SHADOW_TENANT_ALLOWLIST": TENANT,
        "BIGQUERY_TALENT_9BOX_SHADOW_WORKSPACE_ALLOWLIST": WORKSPACE,
        "BIGQUERY_TALENT_9BOX_SHADOW_PROJECT_ID": "omega-shadow-pilot",
        "BIGQUERY_TALENT_9BOX_SHADOW_DATASET": "omega_staging_talent_shadow",
        "BIGQUERY_TALENT_9BOX_SHADOW_LOCATION": "us-central1",
        "BIGQUERY_TALENT_9BOX_SHADOW_SERVICE_ACCOUNT": (
            "shadow@omega-shadow-pilot.iam.gserviceaccount.com"
        ),
        "BIGQUERY_TALENT_9BOX_SHADOW_GOLD_BUCKET": "omega-gold",
        "BIGQUERY_TALENT_9BOX_SHADOW_MAX_BYTES_BILLED": str(runtime.TEN_GIB),
    }


def _dag_conf() -> dict[str, str]:
    return {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "cartridge_id": "sap_successfactors",
        "dataset": "sap_successfactors_talent_9box",
        "pipeline_run_id": PIPELINE_RUN,
        "source_run_id": SOURCE_RUN,
    }


def _baseline() -> dict:
    object_key = (
        "gold/sap_successfactors/sap_successfactors_talent_9box/"
        f"tenant_id={TENANT}/workspace_id={WORKSPACE}/_snapshots/_pending/"
        f"{uuid.UUID(SOURCE_RUN).hex}/{CHECKSUM}.parquet"
    )
    cells = {
        key: {
            "employee_count": 0,
            "ready_count": 0,
            "benchmark_count": 0,
            "blocked_count": 0,
            "box_status": "empty",
        }
        for key in runtime.BOX_KEYS
    }
    cells["estrella"] = {
        "employee_count": 1,
        "ready_count": 1,
        "benchmark_count": 0,
        "blocked_count": 0,
        "box_status": "ready",
    }
    cells["core"] = {
        "employee_count": 1,
        "ready_count": 0,
        "benchmark_count": 0,
        "blocked_count": 1,
        "box_status": "blocked",
    }
    envelope = {
        "manifest": {
            "pipeline_run_id": PIPELINE_RUN,
            "source_run_id": SOURCE_RUN,
            "head_generation": 7,
            # Deliberately larger than JavaScript's safe integer range: the
            # publication contract keeps the GCS generation as a string.
            "object_version": "9223372036854775808",
            "receipt_id": "44444444-4444-4444-8444-444444444444",
            "uri": f"gs://omega-gold/{object_key}",
            "bucket": "omega-gold",
            "object_key": object_key,
            "checksum": CHECKSUM,
            "schema_digest": "b" * 64,
            "evidence_digest": "c" * 64,
            "row_count": 3,
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
        "nine_box_counts": cells,
        "cohorts": {"high": 1, "medium": 1, "low": 0},
        "totals": {
            "population": 3,
            "assigned_to_cell": 2,
            "classified": 1,
            "unclassified": 2,
        },
        "blockers": {"not_classified": 2, "assigned_cell_not_ready": 1},
        "verification_context": {
            "ruleset": "successfactors_talent_population/v1",
            "benchmark_authority_valid": False,
            "benchmark_head": "",
            "relation_schema_digest": "b" * 64,
        },
    }
    return {**envelope, "digest": runtime.digest(envelope)}


def test_shadow_config_is_disabled_and_postgres_locked_by_default() -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        runtime.ShadowRunConfig.load({}, {})
    env = {
        "BIGQUERY_TALENT_9BOX_SHADOW_ENABLED": "true",
        "TALENT_POPULATION_BACKEND": "bigquery",
    }
    with pytest.raises(RuntimeError, match="PostgreSQL Gold"):
        runtime.ShadowRunConfig.load({}, env)


def test_shadow_config_rejects_static_keys_and_non_allowlisted_workspace() -> None:
    env = {
        "BIGQUERY_TALENT_9BOX_SHADOW_ENABLED": "true",
        "TALENT_POPULATION_BACKEND": "postgres_gold",
        "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/key.json",
    }
    with pytest.raises(RuntimeError, match="static Google"):
        runtime.ShadowRunConfig.load({}, env)

    env = _runtime_env()
    env["BIGQUERY_TALENT_9BOX_SHADOW_TENANT_ALLOWLIST"] = (
        "ffffffff-ffff-4fff-8fff-ffffffffffff"
    )
    with pytest.raises(RuntimeError, match="tenant is not"):
        runtime.ShadowRunConfig.load(_dag_conf(), env)
    assert runtime.ShadowRunConfig.load(_dag_conf(), _runtime_env()) == _config()


def test_baseline_contract_is_scope_bound_and_preserves_gcs_version_string() -> None:
    baseline = _baseline()
    runtime.validate_baseline(baseline, _config())
    assert isinstance(baseline["manifest"]["object_version"], str)

    poisoned = _baseline()
    poisoned["manifest"]["object_key"] = poisoned["manifest"]["object_key"].replace(
        f"workspace_id={WORKSPACE}", "workspace_id=ffffffff-ffff-4fff-8fff-ffffffffffff"
    )
    unsigned = {key: value for key, value in poisoned.items() if key != "digest"}
    poisoned["digest"] = runtime.digest(unsigned)
    with pytest.raises(RuntimeError, match="not exact"):
        runtime.validate_baseline(poisoned, _config())


def test_postgres_baseline_materializes_one_physical_population_scan() -> None:
    source = inspect.getsource(bigquery_shadow.build_talent_9box_baseline)
    assert source.count("WITH scoped AS MATERIALIZED") == 1
    assert source.count("aggregate_rows = await gold_conn.fetch(") == 1
    assert "FROM scoped" in source
    assert "FROM cells" in source
    assert "FROM cohorts" in source


class _Blob:
    def __init__(self, *, generation: int):
        self.generation = generation
        self.size = 123
        self.metadata = {"omega-sha256": CHECKSUM}
        self.precondition = None

    def reload(self, **kwargs):
        self.precondition = kwargs.get("if_generation_match")
        if self.precondition != self.generation:
            raise RuntimeError("precondition failed")

    def open(self, _mode, **kwargs):
        if kwargs.get("if_generation_match") != self.generation:
            raise RuntimeError("generation changed")
        return io.BytesIO(OBJECT_BYTES)


class _Storage:
    def __init__(self, blob):
        self._blob = blob
        self.requested = None

    def bucket(self, name):
        assert name == "omega-gold"
        return self

    def blob(self, name):
        # No generation is passed here: this must resolve the live head.
        self.requested = name
        return self._blob


class _Conflict(Exception):
    pass


class _ScalarQueryParameter:
    def __init__(self, name, kind, value):
        self.name = name
        self.kind = kind
        self.value = value

    def representation(self):
        return {"name": self.name, "kind": self.kind, "value": self.value}


class _LoadJobConfig:
    def __init__(self, **kwargs):
        self.source_format = kwargs["source_format"]
        self.autodetect = kwargs["autodetect"]
        self.create_disposition = kwargs["create_disposition"]
        self.write_disposition = kwargs["write_disposition"]
        self.labels = {}

    def to_api_repr(self):
        return {
            "configuration": {
                "load": {
                    "sourceFormat": self.source_format,
                    "autodetect": self.autodetect,
                    "createDisposition": self.create_disposition,
                    "writeDisposition": self.write_disposition,
                }
            }
        }


class _QueryJobConfig:
    def __init__(self, **kwargs):
        self.dry_run = kwargs["dry_run"]
        self.use_legacy_sql = kwargs["use_legacy_sql"]
        self.use_query_cache = kwargs["use_query_cache"]
        self.maximum_bytes_billed = kwargs["maximum_bytes_billed"]
        self.query_parameters = kwargs["query_parameters"]
        self.labels = kwargs["labels"]

    def to_api_repr(self):
        return {
            "configuration": {
                "query": {
                    "queryParameters": [
                        item.representation() for item in self.query_parameters
                    ],
                    "maximumBytesBilled": self.maximum_bytes_billed,
                    "useLegacySql": self.use_legacy_sql,
                    "useQueryCache": self.use_query_cache,
                }
            }
        }


class _LoadJob:
    def __init__(self, job_id, config, qualified_table, uri):
        project, dataset, table = qualified_table.split(".")
        self.job_id = job_id
        self.output_rows = 3
        self.labels = dict(config.labels)
        self.source_uris = [uri]
        self.destination = types.SimpleNamespace(
            project=project, dataset_id=dataset, table_id=table
        )
        self._config = config

    def result(self, **_kwargs):
        return self

    def to_api_repr(self):
        return self._config.to_api_repr()


class _QueryJob:
    def __init__(self, job_id, config, sql, rows):
        self.job_id = job_id
        self.dry_run = config.dry_run
        self.labels = dict(config.labels)
        self.query = sql
        self.total_bytes_processed = 100
        self.total_bytes_billed = 100
        self._config = config
        self._rows = rows

    def result(self, **_kwargs):
        return self._rows

    def to_api_repr(self):
        return self._config.to_api_repr()


class _FakeBigQueryClient:
    def __init__(self, *, conflict_retries: bool):
        self.conflict_retries = conflict_retries
        self.jobs = {}
        self.load_uri = ""
        self.query_configs = []
        self.table = types.SimpleNamespace(
            schema=[types.SimpleNamespace(name=name) for name in runtime._QUERY_FIELDS],
            expires=None,
        )

    def load_table_from_uri(
        self, uri, qualified_table, *, job_id, location, job_config
    ):
        assert location == "us-central1"
        self.load_uri = uri
        job = _LoadJob(job_id, job_config, qualified_table, uri)
        self.jobs[job_id] = job
        if self.conflict_retries:
            raise _Conflict("existing load")
        return job

    def query(self, sql, *, job_config, location, job_id=None):
        assert location == "us-central1"
        self.query_configs.append(job_config)
        resolved_job_id = job_id or "unexpected-missing-job-id"
        if job_config.dry_run:
            job = _QueryJob(resolved_job_id, job_config, sql, [])
            self.jobs[resolved_job_id] = job
            if self.conflict_retries:
                raise _Conflict("existing dry run")
            return job
        rows = [
            {
                "kind": "population",
                "bucket": "",
                "employee_count": 3,
                "ready_count": 0,
                "benchmark_count": 0,
                "blocked_count": 0,
            },
            {
                "kind": "cell",
                "bucket": "estrella",
                "employee_count": 1,
                "ready_count": 1,
                "benchmark_count": 0,
                "blocked_count": 0,
            },
            {
                "kind": "cell",
                "bucket": "core",
                "employee_count": 1,
                "ready_count": 0,
                "benchmark_count": 0,
                "blocked_count": 1,
            },
            {
                "kind": "cohort",
                "bucket": "high",
                "employee_count": 1,
                "ready_count": 0,
                "benchmark_count": 0,
                "blocked_count": 0,
            },
            {
                "kind": "cohort",
                "bucket": "medium",
                "employee_count": 1,
                "ready_count": 0,
                "benchmark_count": 0,
                "blocked_count": 0,
            },
        ]
        job = _QueryJob(resolved_job_id, job_config, sql, rows)
        self.jobs[resolved_job_id] = job
        if self.conflict_retries:
            raise _Conflict("existing query")
        return job

    def get_job(self, job_id, **_kwargs):
        return self.jobs[job_id]

    def get_table(self, _qualified_table):
        return self.table

    def update_table(self, table, fields):
        assert fields == ["expires"]
        return table


def _install_fake_bigquery(monkeypatch: pytest.MonkeyPatch) -> None:
    bigquery = types.ModuleType("google.cloud.bigquery")
    bigquery.LoadJobConfig = _LoadJobConfig
    bigquery.QueryJobConfig = _QueryJobConfig
    bigquery.ScalarQueryParameter = _ScalarQueryParameter
    bigquery.SourceFormat = types.SimpleNamespace(PARQUET="PARQUET")
    bigquery.CreateDisposition = types.SimpleNamespace(
        CREATE_IF_NEEDED="CREATE_IF_NEEDED"
    )
    bigquery.WriteDisposition = types.SimpleNamespace(WRITE_EMPTY="WRITE_EMPTY")
    exceptions = types.ModuleType("google.api_core.exceptions")
    exceptions.Conflict = _Conflict
    api_core = types.ModuleType("google.api_core")
    api_core.exceptions = exceptions
    cloud = types.ModuleType("google.cloud")
    cloud.bigquery = bigquery
    google = types.ModuleType("google")
    google.api_core = api_core
    google.cloud = cloud
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.api_core", api_core)
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", exceptions)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bigquery)


def test_gcs_verification_reads_live_head_with_generation_precondition() -> None:
    blob = _Blob(generation=9223372036854775808)
    storage = _Storage(blob)
    result = runtime.verify_gcs_object(_config(), _baseline(), storage)
    assert result["object_version"] == "9223372036854775808"
    assert blob.precondition == 9223372036854775808

    with pytest.raises(RuntimeError, match="live generation"):
        runtime.verify_gcs_object(
            _config(), _baseline(), _Storage(_Blob(generation=9))
        )


@pytest.mark.parametrize("conflict_retries", [False, True])
def test_fake_bigquery_e2e_load_dry_run_query_conflict_and_source_recheck(
    monkeypatch: pytest.MonkeyPatch,
    conflict_retries: bool,
) -> None:
    _install_fake_bigquery(monkeypatch)
    config = _config()
    baseline = _baseline()
    storage = _Storage(_Blob(generation=9223372036854775808))
    source_before = runtime.verify_gcs_object(config, baseline, storage)
    client = _FakeBigQueryClient(conflict_retries=conflict_retries)

    loaded = runtime.load_immutable_table(config, baseline, client)
    dry_run = runtime.dry_run_aggregate_query(config, baseline, loaded, client)
    query = runtime.execute_aggregate_query(
        config, baseline, loaded, dry_run, client
    )
    source_after = runtime.verify_gcs_object(config, baseline, storage)
    comparison = runtime.compare_aggregates(baseline, query)

    assert source_before == source_after
    assert loaded["load_job_id"] == runtime._load_job_id(config)
    assert dry_run["dry_run_job_id"] == runtime._dry_run_job_id(
        config, baseline, dry_run["query_digest"]
    )
    assert query["query_job_id"] == runtime._query_job_id(
        config, baseline, dry_run["query_digest"]
    )
    assert comparison["parity"] is True
    assert client.load_uri == baseline["manifest"]["uri"]
    assert all(
        item.maximum_bytes_billed == runtime.TEN_GIB
        for item in client.query_configs
    )

    xcom = runtime.audit_no_pii_payload(
        baseline,
        source_before,
        loaded,
        dry_run,
        query,
        source_after,
        comparison,
    )
    log_unsigned = {
        "scanner": "omega-shadow-local-log/v1",
        "files_scanned": 7,
        "violations": 0,
        "passed": True,
        "status": "complete",
    }
    logs = {**log_unsigned, "digest": runtime.digest(log_unsigned)}
    audit_unsigned = {"xcom": xcom, "logs": logs, "passed": True}
    pii_audit = {**audit_unsigned, "digest": runtime.digest(audit_unsigned)}
    recorded, captured = _record_artifacts(
        monkeypatch,
        {
            "baseline": baseline,
            "source_before": source_before,
            "loaded": loaded,
            "dry_run": dry_run,
            "bigquery_result": query,
            "source_after": source_after,
            "comparison": comparison,
            "pii_audit": pii_audit,
        },
    )
    assert recorded["status"] == "success"
    assert captured["args"]["extra"]["contract_verified"] is True

    with pytest.raises(RuntimeError, match="live generation"):
        runtime.verify_gcs_object(
            config,
            baseline,
            _Storage(_Blob(generation=9223372036854775809)),
        )


def test_query_plan_uses_real_9box_contract_and_never_selects_person_fields() -> None:
    loaded = {
        "query_fields": {field: True for field in runtime._QUERY_FIELDS},
    }
    sql = runtime._query_plan(_config(), loaded)["sql"]
    for field in (
        "box_key",
        "box_status",
        "invalid_score_input",
        "performance_score",
        "potential_score",
    ):
        assert f"`{field}`" in sql
    for field in ("full_name", "email", "pernr", "user_id"):
        assert field not in sql
    assert "SELECT *" not in sql

    poisoned = {
        "query_fields": {
            **{field: True for field in runtime._QUERY_FIELDS},
            "full_name": True,
        }
    }
    with pytest.raises(RuntimeError, match="query-field contract"):
        runtime._query_plan(_config(), poisoned)


def test_compare_and_pii_scanner_fail_closed() -> None:
    baseline = _baseline()
    result = {
        key: baseline[key]
        for key in ("nine_box_counts", "cohorts", "totals", "blockers")
    }
    assert runtime.compare_aggregates(baseline, result)["parity"] is True
    result["totals"] = {**result["totals"], "population": 4}
    assert runtime.compare_aggregates(baseline, result)["parity"] is False
    assert runtime.audit_no_pii_payload({"nested": {"email": "x@example.com"}})[
        "passed"
    ] is False


def _shadow_artifacts() -> dict[str, dict]:
    config = _config()
    baseline = _baseline()
    source_unsigned = {
        "object_version": baseline["manifest"]["object_version"],
        "checksum": CHECKSUM,
        "content_length": len(OBJECT_BYTES),
        "verified": True,
    }
    source = {
        **source_unsigned,
        "verification_digest": runtime.digest(source_unsigned),
    }
    loaded = {
        "table_id": config.table_id,
        "load_job_id": runtime._load_job_id(config),
        "output_rows": baseline["manifest"]["row_count"],
        "query_fields": {field: True for field in runtime._QUERY_FIELDS},
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }
    plan = runtime._query_plan(config, loaded)
    dry_run = {
        **plan,
        "dry_run_job_id": runtime._dry_run_job_id(
            config, baseline, plan["query_digest"]
        ),
        "dry_run_bytes": 100,
    }
    aggregate = {
        key: baseline[key]
        for key in ("nine_box_counts", "cohorts", "totals", "blockers")
    }
    query = {
        **aggregate,
        "aggregate_digest": runtime.digest(aggregate),
        "query_job_id": runtime._query_job_id(
            config, baseline, plan["query_digest"]
        ),
        "query_bytes": 100,
        "bytes_billed": 100,
    }
    comparison = runtime.compare_aggregates(baseline, query)
    payloads = [
        baseline,
        source,
        loaded,
        dry_run,
        query,
        source,
        comparison,
    ]
    xcom = runtime.audit_no_pii_payload(*payloads)
    log_unsigned = {
        "scanner": "omega-shadow-local-log/v1",
        "files_scanned": 7,
        "violations": 0,
        "passed": True,
        "status": "complete",
    }
    logs = {**log_unsigned, "digest": runtime.digest(log_unsigned)}
    audit_unsigned = {"xcom": xcom, "logs": logs, "passed": True}
    pii_audit = {**audit_unsigned, "digest": runtime.digest(audit_unsigned)}
    return {
        "baseline": baseline,
        "source_before": source,
        "loaded": loaded,
        "dry_run": dry_run,
        "bigquery_result": query,
        "source_after": dict(source),
        "comparison": comparison,
        "pii_audit": pii_audit,
    }


class _RegistryResponse:
    status_code = 200

    def __init__(self, status: str):
        self._status = status

    def json(self):
        return {"result": {"status": self._status}}


def _record_artifacts(monkeypatch: pytest.MonkeyPatch, artifacts: dict) -> tuple[dict, dict]:
    captured: dict = {}
    security_module = types.ModuleType("runtime_security_context")
    security_module.build_pipeline_run_context = lambda args: {"bound": args["run_id"]}
    monkeypatch.setitem(sys.modules, "runtime_security_context", security_module)
    monkeypatch.setenv(
        "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA",
        "airflow-mcp-pair-key-long-enough-for-production",
    )

    def fake_post(_url, **kwargs):
        captured.update(kwargs["json"])
        return _RegistryResponse(kwargs["json"]["args"]["status"])

    monkeypatch.setattr(runtime.requests, "post", fake_post)
    result = runtime.record_shadow_evidence(
        _config(),
        airflow_dag_run_id=f"shadow__{SOURCE_RUN}",
        **artifacts,
    )
    return result, captured


def test_recorder_revalidates_every_artifact_and_persists_exact_digests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _shadow_artifacts()
    result, captured = _record_artifacts(monkeypatch, artifacts)

    assert result["status"] == "success"
    args = captured["args"]
    assert args["status"] == "success"
    assert args["record_count"] == 3
    extra = args["extra"]
    assert extra["contract_verified"] is True
    assert extra["pre_load_verification_digest"] == artifacts["source_before"][
        "verification_digest"
    ]
    assert extra["post_query_verification_digest"] == artifacts["source_after"][
        "verification_digest"
    ]
    assert extra["query_digest"] == artifacts["dry_run"]["query_digest"]
    assert extra["aggregate_digest"] == artifacts["bigquery_result"][
        "aggregate_digest"
    ]


@pytest.mark.parametrize(
    "poison",
    (
        "missing_post_verification",
        "source_mutation",
        "missing_job_id",
        "null_load_rows",
        "null_dry_run_bytes",
        "null_query_bytes",
        "excess_bytes",
        "bad_digest",
    ),
)
def test_recorder_fails_closed_on_incomplete_or_altered_evidence(
    monkeypatch: pytest.MonkeyPatch,
    poison: str,
) -> None:
    artifacts = _shadow_artifacts()
    if poison == "missing_post_verification":
        artifacts["source_after"] = None
    elif poison == "source_mutation":
        unsigned = {
            **artifacts["source_after"],
            "content_length": artifacts["source_after"]["content_length"] + 1,
        }
        unsigned.pop("verification_digest")
        artifacts["source_after"] = {
            **unsigned,
            "verification_digest": runtime.digest(unsigned),
        }
    elif poison == "missing_job_id":
        artifacts["loaded"]["load_job_id"] = ""
    elif poison == "null_load_rows":
        artifacts["loaded"]["output_rows"] = None
    elif poison == "null_dry_run_bytes":
        artifacts["dry_run"]["dry_run_bytes"] = None
    elif poison == "null_query_bytes":
        artifacts["bigquery_result"]["query_bytes"] = None
    elif poison == "excess_bytes":
        artifacts["bigquery_result"]["query_bytes"] = runtime.TEN_GIB + 1
    else:
        artifacts["bigquery_result"]["aggregate_digest"] = "0" * 64

    result, captured = _record_artifacts(monkeypatch, artifacts)

    assert result["status"] == "failed"
    assert captured["args"]["status"] == "failed"
    assert captured["args"]["record_count"] == 0
    assert captured["args"]["extra"]["contract_verified"] is False


def test_local_airflow_log_audit_scans_only_current_run(tmp_path: Path) -> None:
    run_id = f"shadow__{SOURCE_RUN}"
    log = (
        tmp_path
        / "dag_id=bigquery_talent_9box_shadow"
        / f"run_id={run_id}"
        / "task_id=compare_with_postgres"
        / "attempt=1.log"
    )
    log.parent.mkdir(parents=True)
    log.write_text("parity=true comparison_digest=abc\n", encoding="utf-8")
    assert runtime.scan_airflow_logs(
        str(tmp_path), dag_id="bigquery_talent_9box_shadow", run_id=run_id
    )["passed"] is True
    log.write_text('payload={"email":"person@example.com"}\n', encoding="utf-8")
    assert runtime.scan_airflow_logs(
        str(tmp_path), dag_id="bigquery_talent_9box_shadow", run_id=run_id
    )["passed"] is False


def _gate_record(source_run: str, stamp: datetime) -> dict:
    xcom_unsigned = {
        "scanner": "omega-shadow-xcom-metadata/v1",
        "checked_nodes": 100,
        "violations": 0,
        "passed": True,
    }
    xcom = {**xcom_unsigned, "digest": runtime.digest(xcom_unsigned)}
    logs_unsigned = {
        "scanner": "omega-shadow-local-log/v1",
        "files_scanned": 7,
        "violations": 0,
        "passed": True,
        "status": "complete",
    }
    logs = {**logs_unsigned, "digest": runtime.digest(logs_unsigned)}
    audit_unsigned = {"xcom": xcom, "logs": logs, "passed": True}
    audit = {**audit_unsigned, "digest": runtime.digest(audit_unsigned)}
    return {
        "run_id": f"bigquery_talent_9box_shadow:{source_run}",
        "dag_id": "bigquery_talent_9box_shadow",
        "cartridge_id": "sap_successfactors",
        "entity": "Talent9BoxShadow",
        "airflow_dag_run_id": f"shadow__{source_run}",
        "mode": "shadow",
        "status": "success",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "project_id": "omega-shadow-pilot",
        "record_count": 3,
        "bytes_written": None,
        "storage_uri": (
            "bigquery://omega-shadow-pilot.omega_staging_talent_shadow."
            f"talent_9box_{source_run.replace('-', '')}"
        ),
        "error_message": None,
        "finished_at": stamp.isoformat(),
        "extra": {
            "shadow_contract": "talent-9box-parity/v1",
            "contract_verified": True,
            "source_pipeline_run_id": PIPELINE_RUN,
            "source_run_id": source_run,
            "source_dataset": "sap_successfactors_talent_9box",
            "bigquery_dataset": "omega_staging_talent_shadow",
            "head_generation": 7,
            "object_version": "9223372036854775808",
            "receipt_id": "44444444-4444-4444-8444-444444444444",
            "source_checksum": "1" * 64,
            "schema_digest": "2" * 64,
            "evidence_digest": "3" * 64,
            "baseline_digest": "4" * 64,
            "pre_load_verification_digest": "5" * 64,
            "post_query_verification_digest": "5" * 64,
            "load_job_id": "omega_talent_load_0123456789abcdef01234567",
            "dry_run_job_id": "omega_talent_dryrun_0123456789abcdef01234567",
            "query_digest": "6" * 64,
            "query_job_id": "omega_talent_query_0123456789abcdef01234567",
            "aggregate_digest": "7" * 64,
            "comparison_digest": "8" * 64,
            "parity": True,
            "difference_components": [],
            "contains_pii": False,
            "pii_audit": audit,
            "dry_run_bytes": 100,
            "query_bytes": 100,
            "bytes_billed": 100,
            "maximum_bytes_billed": 1000,
        },
    }


def test_promotion_gate_requires_ten_heads_and_full_seven_day_coverage() -> None:
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    compact = [
        _gate_record(str(uuid.uuid5(uuid.NAMESPACE_URL, f"head:{i}")), now)
        for i in range(10)
    ]
    gate_args = {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "project_id": "omega-shadow-pilot",
        "dataset_id": "omega_staging_talent_shadow",
        "now": now,
    }
    assert runtime.evaluate_promotion_gate(compact, **gate_args)["eligible"] is False
    covered = [
        {**record, "finished_at": (now - timedelta(days=7) + timedelta(hours=i)).isoformat()}
        for i, record in enumerate(compact)
    ]
    covered[-1]["finished_at"] = now.isoformat()
    assert runtime.evaluate_promotion_gate(covered, **gate_args)["eligible"] is True
    covered[-1]["extra"]["pii_audit"]["logs"]["passed"] = False
    assert runtime.evaluate_promotion_gate(covered, **gate_args)["eligible"] is False


def test_promotion_gate_is_scope_bound_and_rejects_any_bad_in_scope_run() -> None:
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    records = [
        _gate_record(
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"head:{index}")),
            now - timedelta(days=7) + timedelta(hours=index * 18),
        )
        for index in range(10)
    ]
    records[-1]["finished_at"] = now.isoformat()
    foreign = _gate_record(str(uuid.uuid4()), now)
    foreign["workspace_id"] = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    foreign_dataset = _gate_record(str(uuid.uuid4()), now)
    foreign_dataset["extra"]["bigquery_dataset"] = "omega_prod_talent_shadow"
    foreign_dataset["storage_uri"] = foreign_dataset["storage_uri"].replace(
        "omega_staging_talent_shadow", "omega_prod_talent_shadow"
    )
    foreign_dag = _gate_record(str(uuid.uuid4()), now)
    foreign_dag["dag_id"] = "unrelated_pipeline"
    args = {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "project_id": "omega-shadow-pilot",
        "dataset_id": "omega_staging_talent_shadow",
        "now": now,
    }
    assert runtime.evaluate_promotion_gate(
        [*records, foreign, foreign_dataset, foreign_dag], **args
    )["eligible"] is True

    bad = _gate_record(str(uuid.uuid4()), now)
    bad["extra"]["query_bytes"] = bad["extra"]["maximum_bytes_billed"] + 1
    decision = runtime.evaluate_promotion_gate([*records, bad], **args)
    assert decision["eligible"] is False
    assert decision["invalid_records"] == 1

    incomplete = _gate_record(str(uuid.uuid4()), now)
    incomplete["extra"].pop("bigquery_dataset")
    decision = runtime.evaluate_promotion_gate([*records, incomplete], **args)
    assert decision["eligible"] is False
    assert decision["invalid_records"] == 1


def test_source_trigger_is_allowlisted_idempotent_and_nonblocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIGQUERY_TALENT_9BOX_SHADOW_ENABLED", "true")
    monkeypatch.setenv("BIGQUERY_TALENT_9BOX_SHADOW_TENANT_ALLOWLIST", TENANT)
    monkeypatch.setenv("BIGQUERY_TALENT_9BOX_SHADOW_WORKSPACE_ALLOWLIST", WORKSPACE)
    called = {}

    def fake_trigger(**kwargs):
        called.update(kwargs)

    module = types.ModuleType("airflow.api.common.trigger_dag")
    module.trigger_dag = fake_trigger
    monkeypatch.setitem(sys.modules, "airflow.api.common.trigger_dag", module)
    result = trigger_talent_9box_shadow(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        cartridge_id="sap_successfactors",
        pipeline_run_id=PIPELINE_RUN,
        materialization_status="success",
        results=[
            {
                "name": "sap_successfactors_talent_9box",
                "layer": "gold",
                "ok": True,
                "publication_run_id": SOURCE_RUN,
            }
        ],
    )
    assert result == {"status": "triggered", "reused": False, "source_run_id": SOURCE_RUN}
    assert called["run_id"] == f"shadow__{SOURCE_RUN}"
    assert called["conf"]["source_run_id"] == SOURCE_RUN


def test_source_dag_wrapper_catches_all_optional_shadow_failures() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "airflow/dags/dataset_refresh_chain.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "trigger_bigquery_shadow"
    )
    guarded = next(node for node in function.body if isinstance(node, ast.Try))
    assert any(
        isinstance(handler.type, ast.Name) and handler.type.id == "Exception"
        for handler in guarded.handlers
    )
    handler = guarded.handlers[0]
    assert not any(isinstance(node, ast.Raise) for node in ast.walk(handler))
    assert "from dataset_refresh_bigquery_shadow import" in ast.unparse(guarded)


def test_console_manifest_requires_content_addressed_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIGQUERY_TALENT_9BOX_MAX_SNAPSHOT_AGE_SECONDS", "86400")
    relation = PublishedGoldRelation(
        schema="omega_publication_gold",
        table=f"run_{uuid.UUID(SOURCE_RUN).hex}",
        run_id=SOURCE_RUN,
        generation=7,
        receipt_id="44444444-4444-4444-8444-444444444444",
        object_checksum=CHECKSUM,
        evidence_digest="c" * 64,
        object_uri=(
            "gs://omega-gold/gold/sap_successfactors/sap_successfactors_talent_9box/"
            f"tenant_id={TENANT}/workspace_id={WORKSPACE}/_snapshots/_pending/"
            f"{uuid.UUID(SOURCE_RUN).hex}/{CHECKSUM}.parquet"
        ),
        object_version="9223372036854775808",
        schema_digest="b" * 64,
        row_count=3,
        published_at=datetime.now(timezone.utc),
    )
    manifest = bigquery_shadow._manifest(
        relation,
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        pipeline_run_id=PIPELINE_RUN,
    )
    assert manifest["source_run_id"] == SOURCE_RUN
    assert manifest["object_version"] == "9223372036854775808"
    relation = PublishedGoldRelation(**{**relation.__dict__, "schema": "public"})
    with pytest.raises(Exception, match="legacy"):
        bigquery_shadow._manifest(
            relation,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            pipeline_run_id=PIPELINE_RUN,
        )


def test_public_backend_can_never_select_bigquery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TALENT_POPULATION_BACKEND", "bigquery")
    with pytest.raises(RuntimeError, match="cannot be selected"):
        public_talent_population_backend()


def test_internal_route_and_terraform_contract_are_wired() -> None:
    root = Path(__file__).resolve().parents[1]
    route = (root / "console/app/routers/intelligence.py").read_text()
    assert '@internal_router.post("/bigquery-shadow/talent-9box-baseline")' in route
    assert 'internal_service != "airflow"' in route
    assert "BigQueryTalent9BoxBaselineRequest(_StrictModel)" in route
    assert "app.include_router(intelligence_router.internal_router)" in (
        root / "console/app/main.py"
    ).read_text()

    terraform = (root / "infra/terraform-gcp/bigquery_shadow.tf").read_text()
    assert "default_table_expiration_ms = 86400000" in terraform
    assert "max_time_travel_hours       = 48" in terraform
    assert 'resource "google_service_account_key"' not in terraform
    assert "roles/iam.serviceAccountTokenCreator" in terraform
    assert "roles/bigquery.jobUser" in terraform
    assert "storage.buckets.get" in terraform
    assert f"tenant_id=${{lower(var.bigquery_shadow_main_tenant_id)}}" in terraform
    assert 'override_value = tostring(var.bigquery_shadow_daily_query_quota_mib)' in terraform
    assert 'urlencode("/d/project")' in terraform

    compose = (root / "infra/docker-compose.yml").read_text()
    for service, next_service in (
        ("console", "workspace"),
        ("airflow", "airflow-scheduler"),
        ("airflow-scheduler", "postgres_data"),
    ):
        section = compose.split(f"\n  {service}:\n", 1)[1].split(
            f"\n  {next_service}:\n", 1
        )[0]
        assert "BIGQUERY_TALENT_9BOX_SHADOW_ENABLED" in section
        assert "BIGQUERY_TALENT_9BOX_SHADOW_TENANT_ALLOWLIST" in section
        assert "BIGQUERY_TALENT_9BOX_SHADOW_WORKSPACE_ALLOWLIST" in section


def test_bigquery_shadow_dataset_acl_is_explicit_and_excludes_project_defaults() -> None:
    root = Path(__file__).resolve().parents[1]
    terraform = (root / "infra/terraform-gcp/bigquery_shadow.tf").read_text()
    dataset = terraform.split(
        'resource "google_bigquery_dataset" "talent_shadow" {', 1
    )[1].split('\n}\n\nresource "google_service_account" "talent_shadow"', 1)[0]

    assert dataset.count("access {") == 2
    assert 'role          = "OWNER"' in dataset
    assert 'special_group = "projectOwners"' in dataset
    assert 'role          = "WRITER"' in dataset
    assert "user_by_email = google_service_account.talent_shadow[0].email" in dataset
    assert 'special_group = "projectReaders"' not in dataset
    assert 'special_group = "projectWriters"' not in dataset
    assert 'resource "google_bigquery_dataset_iam_member"' not in terraform


def test_internal_baseline_http_requires_the_airflow_pair_key_before_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    airflow_key = "airflow-console-pair-key-which-is-long-enough-for-production"
    workspace_key = "workspace-console-pair-key-which-is-also-long-enough"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "INTERNAL_API_KEY", "shared-internal-key-present-but-not-used-in-production"
    )
    monkeypatch.setenv("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE", airflow_key)
    monkeypatch.setenv("INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE", workspace_key)
    called = []

    async def fake_baseline(**kwargs):
        called.append(kwargs)
        return {"verified": True}

    monkeypatch.setattr(
        intelligence_router.bigquery_shadow,
        "build_talent_9box_baseline",
        fake_baseline,
    )
    app = FastAPI()
    app.include_router(intelligence_router.internal_router)
    client = TestClient(app)
    payload = {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "cartridge_id": "sap_successfactors",
        "dataset": "sap_successfactors_talent_9box",
        "pipeline_run_id": PIPELINE_RUN,
        "source_run_id": SOURCE_RUN,
    }
    endpoint = "/internal/intelligence/bigquery-shadow/talent-9box-baseline"

    assert client.post(endpoint, json=payload).status_code == 403
    assert client.post(
        endpoint,
        json=payload,
        headers={"X-Internal-Service": "airflow", "X-API-Key": "wrong"},
    ).status_code == 403
    assert client.post(
        endpoint,
        json=payload,
        headers={
            "X-Internal-Service": "workspace",
            "X-API-Key": workspace_key,
        },
    ).status_code == 403
    response = client.post(
        endpoint,
        json=payload,
        headers={"X-Internal-Service": "airflow", "X-API-Key": airflow_key},
    )
    assert response.status_code == 200
    assert response.json() == {"verified": True}
    assert len(called) == 1
