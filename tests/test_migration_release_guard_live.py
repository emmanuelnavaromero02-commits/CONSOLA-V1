from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_db_migrations.py"
FIXTURE = ROOT / "tests" / "fixtures" / "app_grants_schema.sql"
BASELINE_REF = "6b12883c5b5ea0537120279ccbee4947137998a2"
BASELINE_MANIFEST = (
    ROOT / "infra" / "migrations" / "manifests" / (f"gcp-live-{BASELINE_REF}.json")
)
RELEASE_MANIFEST = ROOT / "infra" / "migrations" / "manifests" / "v1.45.207-beta.json"
BACKEND_CONTROL = ROOT / "scripts" / "migration_backend_control.py"
BASELINE_SHA = "b6cb33c9b1a0f93e13fe2eb68f2e8fff1fdeedb2979bbfb22840a2a35d2e4a18"
RELEASE_SHA = "79a607d045b853fba26812311b930b21d84e676d6cbde43f05ad4ec8150700b2"


BOOTSTRAP_COMPOSE = """
services:
  postgres:
    image: postgres:15.18
    environment:
      POSTGRES_DB: modecissions
      POSTGRES_HOST_AUTH_METHOD: trust
    volumes:
      - operational_data:/var/lib/postgresql/data
  postgres_gold:
    image: postgres:15.18
    command: ["postgres", "-p", "5433"]
    environment:
      POSTGRES_DB: modecissions_gold
      POSTGRES_HOST_AUTH_METHOD: trust
    volumes:
      - gold_data:/var/lib/postgresql/data
volumes:
  operational_data:
  gold_data:
"""


COMPOSE = """
services:
  postgres:
    image: postgres:15.18
    environment:
      POSTGRES_DB: modecissions
      POSTGRES_HOST_AUTH_METHOD: trust
    volumes:
      - operational_data:/var/lib/postgresql/data
      - {operational_init}:/docker-entrypoint-initdb.d:ro
  postgres_gold:
    image: postgres:15.18
    command: ["postgres", "-p", "5433"]
    environment:
      POSTGRES_DB: modecissions_gold
      POSTGRES_HOST_AUTH_METHOD: trust
    volumes:
      - gold_data:/var/lib/postgresql/data
      - {gold_init}:/docker-entrypoint-initdb.d:ro
volumes:
  operational_data:
  gold_data:
"""


FRESH_COMPOSE = """
services:
  postgres:
    image: pgvector/pgvector:pg15
    environment:
      POSTGRES_DB: modecissions
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: test-operational
      PGOPTIONS: >-
        -c app.omega_console_password=console-test
        -c app.omega_outcome_binder_password=outcome-test
        -c app.omega_refinement_password=refinement-test
        -c app.omega_vault_password=vault-test
        -c app.omega_workspace_password=workspace-test
        -c app.omega_mcp_infra_password=mcp-test
        -c app.omega_cartridge_sap_hcm_password=sap-hcm-test
        -c app.omega_cartridge_sap_s4_password=sap-s4-test
        -c app.omega_cartridge_sap_sf_password=sap-sf-test
        -c app.omega_airflow_dag_password=airflow-dag-test
        -c app.omega_airflow_meta_password=airflow-meta-test
        -c app.omega_superset_meta_password=superset-test
        -c app.omega_cartridge_replicon_password=replicon-test
        -c app.omega_cartridge_salesforce_password=salesforce-test
        -c app.omega_cartridge_hubspot_password=hubspot-test
        -c app.omega_cartridge_banxico_password=banxico-test
        -c app.omega_cartridge_inegi_password=inegi-test
        -c app.omega_cartridge_sec_edgar_password=sec-edgar-test
    volumes:
      - {operational_init}:/docker-entrypoint-initdb.d:ro
  postgres_gold:
    image: postgres:15.18
    command: ["postgres", "-p", "5433"]
    environment:
      POSTGRES_DB: modecissions_gold
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: test-gold
      PGOPTIONS: >-
        -c app.omega_refinement_gold_password=gold-reader-test
        -c app.omega_gold_publisher_password=gold-publisher-test
        -c app.omega_gold_verifier_password=gold-verifier-test
    volumes:
      - {gold_init}:/docker-entrypoint-initdb.d:ro
"""


def _run(
    compose: Path,
    project: str,
    *args: str,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            project,
            "-f",
            str(compose),
            *args,
        ],
        cwd=ROOT,
        text=True,
        input=input_text,
        capture_output=True,
        timeout=240,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(f"compose failed: {result.stdout}\n{result.stderr}")
    return result


def _psql(
    compose: Path,
    project: str,
    database: str,
    sql: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    service = "postgres_gold" if database == "modecissions_gold" else "postgres"
    port = "5433" if service == "postgres_gold" else "5432"
    return _run(
        compose,
        project,
        "exec",
        "-T",
        service,
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        "-U",
        "postgres",
        "-h",
        "127.0.0.1",
        "-p",
        port,
        "-d",
        database,
        "-c",
        sql,
        check=check,
    )


def _wait(compose: Path, project: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        operational = _psql(compose, project, "modecissions", "SELECT 1", check=False)
        gold = _psql(compose, project, "modecissions_gold", "SELECT 1", check=False)
        if operational.returncode == gold.returncode == 0:
            return
        time.sleep(1)
    raise AssertionError("final PostgreSQL 15 TCP servers did not become ready")


def _pid1_is_final_postgres(compose: Path, project: str, service: str) -> bool:
    executable = _run(
        compose,
        project,
        "exec",
        "-T",
        service,
        "cat",
        "/proc/1/comm",
        check=False,
    )
    return executable.returncode == 0 and executable.stdout == "postgres\n"


def _wait_for_fresh_entrypoints(
    compose: Path,
    project: str,
    *,
    timeout_seconds: float = 180,
    interval_seconds: float = 1,
) -> None:
    """Wait past the entrypoint's temporary bootstrap server.

    ``pg_isready`` becomes true while init SQL is still running, so readiness
    alone is not evidence that the reviewed 176/9 ledgers are complete.
    """

    deadline = time.monotonic() + timeout_seconds
    last = ("unavailable", "unavailable")
    stable_readings = 0
    while time.monotonic() < deadline:
        operational = _psql(
            compose,
            project,
            "modecissions",
            "SELECT count(*) FROM schema_migrations",
            check=False,
        )
        gold = _psql(
            compose,
            project,
            "modecissions_gold",
            "SELECT count(*) FROM schema_migrations",
            check=False,
        )
        last = (operational.stdout.strip(), gold.stdout.strip())
        running = [
            _run(compose, project, "ps", "-q", service, check=False)
            for service in ("postgres", "postgres_gold")
        ]
        if any(
            result.returncode != 0 or not result.stdout.strip() for result in running
        ):
            logs = _run(compose, project, "logs", "--no-color", check=False).stdout[
                -12_000:
            ]
            raise AssertionError(
                "fresh PostgreSQL entrypoints exited during bootstrap:\n" + logs
            )
        final_pid1 = _pid1_is_final_postgres(
            compose, project, "postgres"
        ) and _pid1_is_final_postgres(compose, project, "postgres_gold")
        if (
            final_pid1
            and operational.returncode == gold.returncode == 0
            and last == ("176", "9")
        ):
            stable_readings += 1
            if stable_readings >= 2:
                return
        else:
            stable_readings = 0
        time.sleep(interval_seconds)
    logs = _run(compose, project, "logs", "--no-color", check=False).stdout[-12_000:]
    raise AssertionError(
        f"fresh entrypoints did not reach exact 176/9 ledgers: {last}\n{logs}"
    )


def test_fresh_wait_rejects_temporary_entrypoint_even_at_exact_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exact = subprocess.CompletedProcess([], 0, stdout="176\n", stderr="")
    gold = subprocess.CompletedProcess([], 0, stdout="9\n", stderr="")

    def fake_psql(
        _compose: Path,
        _project: str,
        database: str,
        _sql: str,
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del check
        return gold if database == "modecissions_gold" else exact

    monkeypatch.setattr(f"{__name__}._psql", fake_psql)
    monkeypatch.setattr(f"{__name__}._pid1_is_final_postgres", lambda *_args: False)
    monkeypatch.setattr(
        f"{__name__}._run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, stdout="a" * 64 + "\n", stderr=""
        ),
    )
    with pytest.raises(AssertionError, match="did not reach exact"):
        _wait_for_fresh_entrypoints(
            tmp_path / "compose.yml",
            "temporary-entrypoint",
            timeout_seconds=0.005,
            interval_seconds=0,
        )


def _ledger_sql(manifest: dict[str, object], database: str) -> str:
    rows = manifest["databases"][database]
    values = ",".join("('" + filename.replace("'", "''") + "')" for filename in rows)
    return f"""
CREATE TABLE schema_migrations (
  id BIGSERIAL PRIMARY KEY,
  filename TEXT NOT NULL UNIQUE,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  checksum TEXT
);
INSERT INTO schema_migrations(filename) VALUES {values};
"""


def _runner_env(compose: Path, project: str) -> dict[str, str]:
    candidate = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    return {
        **os.environ,
        "OMEGA_MIGRATION_COMPOSE_FILE": str(compose),
        "OMEGA_MIGRATION_ENV_FILE": str(compose.parent / "absent.env"),
        "OMEGA_MIGRATION_COMPOSE_PROJECT_NAME": project,
        "OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT": "0",
        "OMEGA_MIGRATION_BOOTSTRAP_MODE": "0",
        "OMEGA_MIGRATION_ALLOW_BOOTSTRAP_LEDGER": "0",
        "OMEGA_MIGRATION_ENVIRONMENT": "test",
        "OMEGA_MIGRATION_OLD_REF": BASELINE_REF,
        "OMEGA_MIGRATION_CANDIDATE_REF": candidate,
        "OMEGA_MIGRATION_RELEASE_VERSION": "1.45.207-beta",
        "OMEGA_MIGRATION_BASELINE_MANIFEST": str(BASELINE_MANIFEST),
        "OMEGA_MIGRATION_BASELINE_MANIFEST_SHA256": BASELINE_SHA,
        "OMEGA_MIGRATION_RELEASE_MANIFEST": str(RELEASE_MANIFEST),
        "OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256": RELEASE_SHA,
    }


def _prove_server_side_backend_cancellation(compose: Path, project: str) -> None:
    run_id = "omega_migration_987654_123456789"
    _psql(
        compose,
        project,
        "modecissions",
        "CREATE TABLE migration_interrupt_canary(value integer NOT NULL)",
    )
    operational_id = _run(compose, project, "ps", "-q", "postgres").stdout.strip()
    gold_id = _run(compose, project, "ps", "-q", "postgres_gold").stdout.strip()
    assert len(operational_id) == len(gold_id) == 64
    client = subprocess.Popen(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGAPPNAME={run_id}",
            operational_id,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            "/var/run/postgresql",
            "-U",
            "postgres",
            "-d",
            "modecissions",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert client.stdin is not None
    client.stdin.write(
        "BEGIN; INSERT INTO migration_interrupt_canary VALUES (1); "
        "SELECT pg_sleep(120); COMMIT;\n"
    )
    client.stdin.close()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        active = _psql(
            compose,
            project,
            "modecissions",
            "SELECT count(*) FROM pg_stat_activity "
            f"WHERE application_name = '{run_id}'",
        ).stdout.strip()
        if active == "1":
            break
        time.sleep(0.2)
    else:
        client.kill()
        client.wait(timeout=10)
        raise AssertionError("tagged server-side migration backend never appeared")

    client.terminate()
    client.wait(timeout=10)
    # Killing the host-side Docker client is not cancellation authority: the
    # psql process and transaction are still executing inside the container.
    assert (
        _psql(
            compose,
            project,
            "modecissions",
            "SELECT count(*) FROM pg_stat_activity "
            f"WHERE application_name = '{run_id}'",
        ).stdout.strip()
        == "1"
    )
    cancelled = subprocess.run(
        [
            "python3",
            str(BACKEND_CONTROL),
            "--run-id",
            run_id,
            "--container-id",
            operational_id,
            "--container-id",
            gold_id,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert cancelled.returncode == 0, cancelled.stderr
    assert "terminated and verified" in cancelled.stdout
    assert (
        _psql(
            compose,
            project,
            "modecissions",
            "SELECT count(*) FROM pg_stat_activity "
            f"WHERE application_name = '{run_id}'",
        ).stdout.strip()
        == "0"
    )
    assert (
        _psql(
            compose,
            project,
            "modecissions",
            "SELECT count(*) FROM migration_interrupt_canary",
        ).stdout.strip()
        == "0"
    )
    _psql(compose, project, "modecissions", "DROP TABLE migration_interrupt_canary")


def test_real_postgres15_atomic_pair_rollback_retry_and_idempotency(
    tmp_path: Path,
) -> None:
    assert subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    compose = tmp_path / "compose.yml"
    compose.write_text(BOOTSTRAP_COMPOSE, encoding="utf-8")
    project = f"omega_migrate_{uuid.uuid4().hex[:10]}"
    try:
        _run(compose, project, "up", "-d")
        _wait(compose, project)
        _run(compose, project, "down")
    except BaseException:
        _run(compose, project, "down", "-v", check=False)
        raise
    compose.write_text(
        COMPOSE.format(
            operational_init=ROOT / "infra/init",
            gold_init=ROOT / "infra/init_gold",
        ),
        encoding="utf-8",
    )
    baseline = json.loads(BASELINE_MANIFEST.read_text(encoding="utf-8"))
    release = json.loads(RELEASE_MANIFEST.read_text(encoding="utf-8"))
    try:
        _run(compose, project, "up", "-d")
        _wait(compose, project)

        _run(
            compose,
            project,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "modecissions",
            input_text=FIXTURE.read_text(encoding="utf-8"),
        )
        _prove_server_side_backend_cancellation(compose, project)
        # A filename-complete 201-row ledger with NULL checksum/evidence is
        # not a historical execution receipt. Production mode must reject it
        # before either database receives a write.
        _psql(
            compose,
            project,
            "modecissions",
            _ledger_sql(release, "operational"),
        )
        _psql(
            compose,
            project,
            "modecissions_gold",
            _ledger_sql(baseline, "gold"),
        )
        null_release = subprocess.run(
            [str(RUNNER)],
            cwd=ROOT,
            env=_runner_env(compose, project),
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert null_release.returncode != 0
        assert "guarded transaction receipt" in null_release.stderr
        operational_unchanged = _psql(
            compose,
            project,
            "modecissions",
            """
SELECT count(*), count(checksum),
       NOT EXISTS (
         SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='schema_migrations'
            AND column_name='checksum_evidence_kind')
FROM schema_migrations;
""",
        ).stdout.strip()
        gold_unchanged = _psql(
            compose,
            project,
            "modecissions_gold",
            """
SELECT count(*), count(checksum),
       NOT EXISTS (
         SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='schema_migrations'
            AND column_name='checksum_evidence_kind')
FROM schema_migrations;
""",
        ).stdout.strip()
        assert operational_unchanged == "201|0|t"
        assert gold_unchanged == "12|0|t"

        _psql(compose, project, "modecissions", "DROP TABLE schema_migrations")
        _psql(
            compose,
            project,
            "modecissions_gold",
            "DROP TABLE schema_migrations",
        )
        _psql(
            compose,
            project,
            "modecissions",
            _ledger_sql(baseline, "operational"),
        )
        _psql(
            compose,
            project,
            "modecissions_gold",
            _ledger_sql(baseline, "gold"),
        )

        # A cartridge role with CREATE on public could pre-seed a relation and
        # become its owner. The migration must reject rather than adopt it; all
        # authority objects and the dedicated role created earlier in 99zzt
        # must roll back as one guarded transaction.
        _psql(
            compose,
            project,
            "modecissions",
            "CREATE TABLE public.analytic_app_manifests(attacker text)",
        )

        failed = subprocess.run(
            [str(RUNNER)],
            cwd=ROOT,
            env=_runner_env(compose, project),
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert failed.returncode != 0
        assert "analytic app authority object already exists" in failed.stderr
        rolled_back = _psql(
            compose,
            project,
            "modecissions",
            """
SELECT count(*), count(checksum),
       NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname='omega_app_grants_owner'),
       to_regclass('public.analytic_app_manifest_datasets') IS NULL,
       to_regclass('public.analytic_app_manifests') IS NOT NULL,
       NOT EXISTS (
         SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='schema_migrations'
            AND column_name='checksum_source_ref')
FROM schema_migrations;
""",
        ).stdout.strip()
        assert rolled_back == "198|0|t|t|t|t"

        # Gold committed only source-inferred expected-byte evidence before
        # the operational failure. It did not mint a historical receipt.
        gold_after_failure = _psql(
            compose,
            project,
            "modecissions_gold",
            """
SELECT count(*), count(checksum), count(checksum_guarded_at),
       count(*) FILTER (WHERE checksum_evidence_kind = 'baseline_expected')
FROM schema_migrations
""",
        ).stdout.strip()
        assert gold_after_failure == "12|12|0|12"

        _psql(
            compose,
            project,
            "modecissions",
            "DROP TABLE public.analytic_app_manifests",
        )

        # CREATE OR REPLACE would retain an attacker-controlled explicit ACL on
        # this exact SECURITY DEFINER signature. Refuse the pre-seeded function
        # and roll back every authority object instead of adopting its grant.
        _psql(
            compose,
            project,
            "modecissions",
            "CREATE FUNCTION public.reconcile_analytic_app_dataset_grants(text,text) "
            "RETURNS TABLE(dataset_name text, action text) LANGUAGE sql AS "
            "$$ SELECT 'attacker'::text, 'granted'::text $$; "
            "GRANT EXECUTE ON FUNCTION "
            "public.reconcile_analytic_app_dataset_grants(text,text) "
            "TO omega_mcp_infra",
        )
        function_failed = subprocess.run(
            [str(RUNNER)],
            cwd=ROOT,
            env=_runner_env(compose, project),
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert function_failed.returncode != 0
        assert "analytic app authority function already exists" in function_failed.stderr
        function_rollback = _psql(
            compose,
            project,
            "modecissions",
            """
SELECT count(*), count(checksum),
       NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname='omega_app_grants_owner'),
       to_regclass('public.analytic_app_manifests') IS NULL,
       has_function_privilege(
           'omega_mcp_infra',
           'public.reconcile_analytic_app_dataset_grants(text,text)',
           'EXECUTE'),
       NOT EXISTS (
         SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='schema_migrations'
            AND column_name='checksum_source_ref')
FROM schema_migrations;
""",
        ).stdout.strip()
        assert function_rollback == "198|0|t|t|t|t"
        _psql(
            compose,
            project,
            "modecissions",
            "DROP FUNCTION public.reconcile_analytic_app_dataset_grants(text,text)",
        )
        first = subprocess.run(
            [str(RUNNER)],
            cwd=ROOT,
            env=_runner_env(compose, project),
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert first.returncode == 0, first.stdout + first.stderr
        assert (
            "operational=201 gold=12 baseline_expected=210 "
            "guarded_transaction=3" in first.stdout
        )
        final = _psql(
            compose,
            project,
            "modecissions",
            """
SELECT count(*), count(checksum), count(checksum_source_ref),
       count(checksum_manifest_sha256), count(checksum_evidence_kind),
       count(checksum_guarded_at),
       count(*) FILTER (WHERE checksum_evidence_kind = 'baseline_expected'),
       count(*) FILTER (WHERE checksum_evidence_kind = 'guarded_transaction'),
       count(*) FILTER (WHERE filename IN (
         '99zzt_analytic_app_dataset_grants.sql',
         '99zzu_analytic_app_manifest_registry.sql',
         '99zzv_sap_successfactors_apps_secure_refresh.sql'))
FROM schema_migrations;
""",
        ).stdout.strip()
        assert final == "201|201|201|201|201|3|198|3|3"
        readable = _psql(
            compose,
            project,
            "modecissions",
            "SET default_transaction_read_only=off; SET ROLE omega_console; "
            "SELECT filename, applied_at, checksum FROM schema_migrations "
            "ORDER BY filename LIMIT 1;",
        )
        assert readable.returncode == 0
        hidden_evidence = _psql(
            compose,
            project,
            "modecissions",
            "SET default_transaction_read_only=off; SET ROLE omega_console; "
            "SELECT checksum_source_ref FROM schema_migrations LIMIT 1;",
            check=False,
        )
        assert hidden_evidence.returncode != 0
        assert "permission denied" in hidden_evidence.stderr
        for statement in (
            "INSERT INTO schema_migrations(filename) VALUES ('99zzz_forbidden.sql')",
            "UPDATE schema_migrations SET checksum=repeat('0',64) WHERE false",
            "DELETE FROM schema_migrations WHERE false",
            "TRUNCATE schema_migrations",
        ):
            denied = _psql(
                compose,
                project,
                "modecissions",
                "SET default_transaction_read_only=off; SET ROLE omega_console; "
                + statement,
                check=False,
            )
            assert denied.returncode != 0
            assert "permission denied" in denied.stderr
        applied_before = _psql(
            compose,
            project,
            "modecissions",
            """
SELECT string_agg(filename || '=' || applied_at::text, ',' ORDER BY filename)
FROM schema_migrations
WHERE filename IN (
 '99zzt_analytic_app_dataset_grants.sql',
 '99zzu_analytic_app_manifest_registry.sql',
 '99zzv_sap_successfactors_apps_secure_refresh.sql');
""",
        ).stdout.strip()

        # A non-omega bridge makes pg_read_all_data inheritance indirect, so
        # the runner must reject it rather than silently broadening the exact
        # three-column console projection.
        _psql(
            compose,
            project,
            "modecissions",
            "CREATE ROLE legacy_migration_reader NOLOGIN; "
            "GRANT pg_read_all_data TO legacy_migration_reader; "
            "GRANT legacy_migration_reader TO omega_console",
        )
        inherited_read = _psql(
            compose,
            project,
            "modecissions",
            "SET ROLE omega_console; "
            "SELECT checksum_source_ref FROM schema_migrations LIMIT 1;",
        )
        assert inherited_read.returncode == 0
        rejected_bridge = subprocess.run(
            [str(RUNNER)],
            cwd=ROOT,
            env=_runner_env(compose, project),
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert rejected_bridge.returncode != 0
        assert "bypasses exact migration ledger authority" in rejected_bridge.stderr
        _psql(
            compose,
            project,
            "modecissions",
            "REVOKE legacy_migration_reader FROM omega_console; "
            "REVOKE pg_read_all_data FROM legacy_migration_reader; "
            "DROP ROLE legacy_migration_reader",
        )

        second = subprocess.run(
            [str(RUNNER)],
            cwd=ROOT,
            env=_runner_env(compose, project),
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert second.returncode == 0, second.stdout + second.stderr
        applied_after = _psql(
            compose,
            project,
            "modecissions",
            """
SELECT string_agg(filename || '=' || applied_at::text, ',' ORDER BY filename)
FROM schema_migrations
WHERE filename IN (
 '99zzt_analytic_app_dataset_grants.sql',
 '99zzu_analytic_app_manifest_registry.sql',
 '99zzv_sap_successfactors_apps_secure_refresh.sql');
""",
        ).stdout.strip()
        assert applied_after == applied_before
    finally:
        _run(compose, project, "down", "-v", check=False)


def test_real_fresh_entrypoints_are_exact_bootstrap_only_and_idempotent(
    tmp_path: Path,
) -> None:
    assert subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    compose = tmp_path / "fresh-compose.yml"
    compose.write_text(
        FRESH_COMPOSE.format(
            operational_init=ROOT / "infra/init",
            gold_init=ROOT / "infra/init_gold",
        ),
        encoding="utf-8",
    )
    project = f"omega_fresh_migrate_{uuid.uuid4().hex[:10]}"
    try:
        _run(compose, project, "up", "-d")
        _wait(compose, project)
        _wait_for_fresh_entrypoints(compose, project)

        assert (
            _psql(
                compose,
                project,
                "modecissions",
                "SELECT count(*) FROM schema_migrations",
            ).stdout.strip()
            == "176"
        )
        assert (
            _psql(
                compose,
                project,
                "modecissions_gold",
                "SELECT count(*) FROM schema_migrations",
            ).stdout.strip()
            == "9"
        )

        environment = _runner_env(compose, project)
        environment["OMEGA_MIGRATION_ENV_FILE"] = str(tmp_path / "absent.env")
        rejected = subprocess.run(
            [str(RUNNER)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert rejected.returncode != 0
        assert "partial/tampered" in rejected.stderr
        assert (
            _psql(
                compose,
                project,
                "modecissions",
                "SELECT count(*), count(checksum) FROM schema_migrations",
            ).stdout.strip()
            == "176|0"
        )
        assert (
            _psql(
                compose,
                project,
                "modecissions_gold",
                """
SELECT count(*), NOT EXISTS (
  SELECT 1 FROM information_schema.columns
   WHERE table_schema='public' AND table_name='schema_migrations'
     AND column_name='checksum'
) FROM schema_migrations;
""",
            ).stdout.strip()
            == "9|t"
        )

        # Exercise historical table/column ACL leakage and PostgreSQL 15's
        # predefined writer/read-all membership bypasses. The bootstrap
        # transaction must remove all of them before it commits evidence.
        _psql(
            compose,
            project,
            "modecissions",
            "GRANT pg_write_all_data, pg_read_all_data TO omega_console; "
            "GRANT INSERT, UPDATE, DELETE ON schema_migrations TO omega_console; "
            "GRANT UPDATE (checksum) ON schema_migrations TO omega_console",
        )
        _psql(
            compose,
            project,
            "modecissions_gold",
            "GRANT pg_write_all_data TO omega_refinement_gold; "
            "GRANT INSERT, UPDATE, DELETE ON schema_migrations "
            "TO omega_refinement_gold",
        )
        _psql(
            compose,
            project,
            "modecissions",
            "ALTER DATABASE modecissions SET default_transaction_read_only=on",
        )
        _psql(
            compose,
            project,
            "modecissions_gold",
            "ALTER DATABASE modecissions_gold SET default_transaction_read_only=on",
        )

        environment["OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT"] = "0"
        environment["OMEGA_MIGRATION_BOOTSTRAP_MODE"] = "1"
        environment["OMEGA_MIGRATION_ALLOW_BOOTSTRAP_LEDGER"] = "1"
        environment["OMEGA_MIGRATION_ENVIRONMENT"] = "test"
        first = subprocess.run(
            [str(RUNNER)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert first.returncode == 0, first.stdout + first.stderr
        assert "operational=fresh_bootstrap gold=fresh_bootstrap" in first.stdout
        assert (
            "operational=201 gold=12 baseline_expected=213 guarded_transaction=0"
            in first.stdout
        )

        assert (
            _psql(
                compose,
                project,
                "modecissions",
                """
SELECT count(*), count(checksum),
       count(*) FILTER (WHERE checksum_evidence_kind='baseline_expected'),
       count(checksum_guarded_at),
       (SELECT tableowner='postgres' FROM pg_tables
         WHERE schemaname='public' AND tablename='schema_migrations'),
       NOT has_table_privilege('omega_console','public.schema_migrations','INSERT'),
       NOT has_column_privilege('omega_console','public.schema_migrations','checksum','UPDATE'),
       NOT pg_has_role('omega_console','pg_write_all_data','MEMBER'),
       NOT pg_has_role('omega_console','pg_read_all_data','MEMBER'),
       has_table_privilege('postgres','public.schema_migrations','INSERT,UPDATE,DELETE,TRUNCATE')
  FROM schema_migrations;
""",
            ).stdout.strip()
            == "201|201|201|0|t|t|t|t|t|t"
        )
        assert (
            _psql(
                compose,
                project,
                "modecissions_gold",
                """
SELECT count(*), count(checksum),
       count(*) FILTER (WHERE checksum_evidence_kind='baseline_expected'),
       count(checksum_guarded_at),
       (SELECT tableowner='postgres' FROM pg_tables
         WHERE schemaname='public' AND tablename='schema_migrations'),
       NOT has_table_privilege('omega_refinement_gold','public.schema_migrations','INSERT'),
       has_table_privilege('postgres','public.schema_migrations','INSERT,UPDATE,DELETE,TRUNCATE')
  FROM schema_migrations;
""",
            ).stdout.strip()
            == "12|12|12|0|t|t|t"
        )

        normalized_before = _psql(
            compose,
            project,
            "modecissions",
            """
SELECT string_agg(filename || '=' || applied_at::text, ',' ORDER BY filename)
  FROM schema_migrations
 WHERE filename = ANY(ARRAY[
   '65_replicon_mejoras_seed_refresh.sql',
   '99zzt_analytic_app_dataset_grants.sql',
   '99zzu_analytic_app_manifest_registry.sql',
   '99zzv_sap_successfactors_apps_secure_refresh.sql'
 ]);
""",
        ).stdout.strip()
        second = subprocess.run(
            [str(RUNNER)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert second.returncode == 0, second.stdout + second.stderr
        assert "operational=release_expected gold=baseline_expected" in second.stdout
        normalized_after = _psql(
            compose,
            project,
            "modecissions",
            """
SELECT string_agg(filename || '=' || applied_at::text, ',' ORDER BY filename)
  FROM schema_migrations
 WHERE filename = ANY(ARRAY[
   '65_replicon_mejoras_seed_refresh.sql',
   '99zzt_analytic_app_dataset_grants.sql',
   '99zzu_analytic_app_manifest_registry.sql',
   '99zzv_sap_successfactors_apps_secure_refresh.sql'
 ]);
""",
        ).stdout.strip()
        assert normalized_after == normalized_before
    finally:
        _run(compose, project, "down", "-v", check=False)
