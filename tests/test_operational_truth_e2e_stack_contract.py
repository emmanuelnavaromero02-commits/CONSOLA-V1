from __future__ import annotations

from pathlib import Path

import yaml

from tests.operational_truth_e2e.evidence import control_room_diagnostic_count


REPO = Path(__file__).resolve().parents[1]
E2E = REPO / "infra/e2e"


def _services() -> dict:
    merged = {}
    for path in E2E.glob("compose.*.yml"):
        merged.update(
            yaml.safe_load(path.read_text(encoding="utf-8")).get("services", {})
        )
    return merged


def test_stack_has_only_real_isolated_services_and_healthchecks():
    services = _services()
    required = {
        "postgres",
        "postgres_gold",
        "redis",
        "minio",
        "refinement",
        "mcp-infra",
        "console",
        "airflow-init",
        "airflow",
        "airflow-scheduler",
        "e2e-test",
    }
    assert set(services) == required
    for name, service in services.items():
        assert "container_name" not in service
        assert "ports" not in service
        assert service.get("restart") == "no"
        assert service.get("mem_limit")
        assert service.get("cpus")
        assert service.get("pids_limit")
        if name not in {"airflow-init", "e2e-test"}:
            assert service.get("healthcheck"), name

    memory_cap_mib = sum(
        int(service["mem_limit"].removesuffix("m"))
        for name, service in services.items()
        if name != "airflow-init"
    )
    assert memory_cap_mib == 4000
    assert services["minio"]["environment"]["GOMEMLIMIT"] == "256MiB"
    assert services["refinement"]["environment"]["DUCKDB_MEMORY_LIMIT"] == "256MB"
    assert (
        services["airflow"]["environment"]["AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION"]
        == "true"
    )
    assert services["airflow"]["healthcheck"]["start_period"] == "600s"
    scheduler_probe = " ".join(services["airflow-scheduler"]["healthcheck"]["test"])
    assert '"scheduler"]["status"] == "healthy"' in scheduler_probe
    assert "airflow jobs check" not in scheduler_probe
    assert services["postgres"]["healthcheck"]["start_period"] == (
        "${E2E_POSTGRES_START_PERIOD:-300s}"
    )
    assert services["postgres"]["tmpfs"] == [
        "/var/lib/postgresql/data:rw,nosuid,noexec,size=256m"
    ]
    assert services["postgres_gold"]["tmpfs"] == [
        "/var/lib/postgresql/data:rw,nosuid,noexec,size=128m"
    ]
    assert services["minio"]["tmpfs"] == ["/data:rw,nosuid,noexec,size=128m"]


def test_stack_versions_and_runtime_credentials_are_fail_closed():
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in E2E.glob("compose.*.yml")
    )
    for version in (
        "pgvector/pgvector:0.8.0-pg15",
        "postgres:15.18-bookworm",
        "redis:7.4.5-alpine",
        "minio/minio:RELEASE.2024-12-18T13-15-44Z",
        "apache/airflow:2.10.5",
        "python:3.12.11-slim-bookworm",
    ):
        if version.startswith(("apache/", "python:", "pgvector/", "postgres:")):
            dockerfiles = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (
                    REPO / "infra/airflow/Dockerfile",
                    REPO / "console/Dockerfile",
                    REPO / "refinement/Dockerfile",
                    REPO / "mcp-infra/Dockerfile",
                    E2E / "Dockerfile",
                    E2E / "postgres-console.Dockerfile",
                    E2E / "postgres-gold.Dockerfile",
                )
            )
            assert version in dockerfiles
        else:
            assert version in source
    assert ":latest" not in source
    assert "APP_ENV: production" in source
    assert 'ALLOW_RCE_TOOLS: "false"' in source
    assert "ALLOWED_ORIGINS: http://e2e.invalid.local" in source
    assert 'ALLOWED_ORIGINS: "*"' not in source
    assert "${SECURITY_CONTEXT_SIGNING_KEY:?required}" in source
    assert "${INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT:?required}" in source
    assert "../../cartridges:/registry/cartridges:ro" in source
    assert "AWS_ACCESS_KEY_ID" not in source
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in source


def test_console_postgres_image_batches_only_canonical_sql_sources():
    dockerfile = (E2E / "postgres-console.Dockerfile").read_text(encoding="utf-8")
    assert "COPY infra/init/ /opt/omega-init/" in dockerfile
    assert "03_canonical_migrations.sql" in dockerfile
    assert "find /opt/omega-init" in dockerfile
    assert 'cat "$source"' in dockerfile
    assert "storage_uri" not in dockerfile


def test_runner_cleans_only_its_project_and_emits_clean_junit_evidence():
    runner = (REPO / "scripts/run_operational_truth_e2e.sh").read_text(encoding="utf-8")
    assert '--project-name "$project"' in runner
    assert "down --volumes --remove-orphans" in runner
    assert "trap cleanup EXIT INT TERM" in runner
    assert "trap - EXIT INT TERM" in runner
    assert '"skipped": 0' in runner
    assert '"failures": 0' in runner
    assert '"errors": 0' in runner
    assert "docker stats --no-stream" in runner
    assert "ps --all --quiet" in runner
    assert "docker stats --no-stream --format" in runner
    assert "images --format json" in runner
    assert 'docker cp "$scheduler_id:/opt/airflow/logs/."' in runner
    assert 'wait_timeout="${E2E_WAIT_TIMEOUT_SECONDS:-420}"' in runner
    assert '--wait-timeout "$wait_timeout"' in runner
    assert '"$wait_timeout" refinement' in runner
    assert '"$wait_timeout" mcp-infra' in runner
    assert '"$wait_timeout" console' in runner
    assert '"$wait_timeout" airflow' in runner
    assert "airflow-scheduler" in runner
    assert "--no-deps -d --wait" in runner
    assert '"${compose[@]}" create e2e-test' in runner
    assert 'docker start --attach "$test_id"' in runner
    assert ".State.ExitCode" in runner
    assert "assert_no_oom" in runner
    assert ".State.OOMKilled" in runner
    assert "/sys/fs/cgroup/memory.events" in runner
    assert "oom_kill" in runner
    assert "OMEGA_E2E_DAGS_DIR" in runner
    assert "dataset_refresh_chain.py" in runner
    assert "file_ingest.py" in runner
    assert "mock" not in runner.lower()


def test_critical_e2e_does_not_substitute_runtime_boundaries():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO / "tests/operational_truth_e2e").glob("*.py")
    )
    for forbidden in (
        "_TestGoldEngine",
        "monkeypatch",
        "FROM (VALUES",
        "unittest.mock",
        "skip_intelligence",
    ):
        assert forbidden not in source
    for required in (
        "/api/v1/dags/file_ingest/dagRuns",
        "/mcp/invoke",
        "/api/control-room/diagnostics",
        "omega_airflow_dag",
        "silver_lineage",
        "data_catalog",
        "intelligence_signals",
    ):
        assert required in source or required in (E2E / "compose.test.yml").read_text(
            encoding="utf-8"
        )
    assert '"Cookie": f"csrf_token={csrf}"' in source


def test_real_failure_canaries_cross_airflow_and_finish_failed():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO / "tests/operational_truth_e2e").glob("*.py")
    )
    for required in (
        "temporarily_unavailable_table",
        '"silver_lineage"',
        '"data_catalog"',
        '"intelligence_runs"',
        "seed_completed_run_without_signal",
        'expected_state="failed"',
        'assert_chain_status(scope, run_id, "failed")',
    ):
        assert required in source
    airflow = _services()["airflow"]
    assert airflow["environment"]["DATASET_REFRESH_RETRIES"] == "0"


def test_control_room_evidence_reads_the_public_diagnostics_contract():
    assert (
        control_room_diagnostic_count(
            {
                "schema_version": "control-room-diagnostics/v1",
                "diagnostic_items": [{"kind": "signal", "title": "Revenue risk"}],
            }
        )
        == 1
    )


def test_canonical_gate_runs_and_requires_the_real_stack():
    workflow = (REPO / ".github/workflows/control-room-postgres-rls.yml").read_text(
        encoding="utf-8"
    )
    assert "operational-truth-e2e:" in workflow
    assert "bash scripts/run_operational_truth_e2e.sh" in workflow
    assert "OPERATIONAL_TRUTH_E2E_RESULT" in workflow
    assert 'e2e" != "success"' in workflow
    assert "timeout-minutes: 30" in workflow
