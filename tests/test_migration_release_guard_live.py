from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "apply_db_migrations.sh"
FIXTURE = ROOT / "tests" / "fixtures" / "app_grants_schema.sql"
BASELINE_REF = "6b12883c5b5ea0537120279ccbee4947137998a2"
BASELINE_MANIFEST = (
    ROOT / "infra" / "migrations" / "manifests" / (f"gcp-live-{BASELINE_REF}.json")
)
RELEASE_MANIFEST = ROOT / "infra" / "migrations" / "manifests" / "v1.45.207-beta.json"
BASELINE_SHA = "b6cb33c9b1a0f93e13fe2eb68f2e8fff1fdeedb2979bbfb22840a2a35d2e4a18"
RELEASE_SHA = "fbc2db83f82eecf9733a60a23fae7c9982d0f9aba52e474f41ba909acbaedc93"


COMPOSE = """
services:
  postgres:
    image: postgres:15.18
    environment:
      POSTGRES_DB: modecissions
      POSTGRES_HOST_AUTH_METHOD: trust
  postgres_gold:
    image: postgres:15.18
    command: ["postgres", "-p", "5433"]
    environment:
      POSTGRES_DB: modecissions_gold
      POSTGRES_HOST_AUTH_METHOD: trust
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
    raise AssertionError("temporary PostgreSQL 15 services did not become ready")


def _wait_for_fresh_entrypoints(compose: Path, project: str) -> None:
    """Wait past the entrypoint's temporary bootstrap server.

    ``pg_isready`` becomes true while init SQL is still running, so readiness
    alone is not evidence that the reviewed 176/9 ledgers are complete.
    """

    deadline = time.monotonic() + 180
    last = ("unavailable", "unavailable")
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
        if operational.returncode == gold.returncode == 0 and last == ("176", "9"):
            return
        time.sleep(1)
    raise AssertionError(f"fresh entrypoints did not reach exact 176/9 ledgers: {last}")


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
        "OMEGA_MIGRATION_COMPOSE_PROJECT_NAME": project,
        "OMEGA_MIGRATION_REQUIRE_EXPLICIT_CONTRACT": "1",
        "OMEGA_MIGRATION_OLD_REF": BASELINE_REF,
        "OMEGA_MIGRATION_CANDIDATE_REF": candidate,
        "OMEGA_MIGRATION_RELEASE_VERSION": "1.45.207-beta",
        "OMEGA_MIGRATION_BASELINE_MANIFEST": str(BASELINE_MANIFEST),
        "OMEGA_MIGRATION_BASELINE_MANIFEST_SHA256": BASELINE_SHA,
        "OMEGA_MIGRATION_RELEASE_MANIFEST": str(RELEASE_MANIFEST),
        "OMEGA_MIGRATION_RELEASE_MANIFEST_SHA256": RELEASE_SHA,
    }


def test_real_postgres15_atomic_pair_rollback_retry_and_idempotency(
    tmp_path: Path,
) -> None:
    assert subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    compose = tmp_path / "compose.yml"
    compose.write_text(COMPOSE, encoding="utf-8")
    project = f"omega_migrate_{uuid.uuid4().hex[:10]}"
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
        for filename in (
            "99zzt_analytic_app_dataset_grants.sql",
            "99zzu_analytic_app_manifest_registry.sql",
        ):
            _run(
                compose,
                project,
                "cp",
                str(ROOT / "infra" / "init" / filename),
                f"postgres:/docker-entrypoint-initdb.d/{filename}",
            )

        # A filename-complete 200-row ledger with NULL checksum/evidence is
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
            ["bash", str(RUNNER)],
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
        assert operational_unchanged == "200|0|t"
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

        tampered = tmp_path / "99zzu_analytic_app_manifest_registry.sql"
        tampered.write_text("SELECT 1;\n", encoding="utf-8")
        _run(
            compose,
            project,
            "cp",
            str(tampered),
            "postgres:/docker-entrypoint-initdb.d/99zzu_analytic_app_manifest_registry.sql",
        )
        wrong_mount = subprocess.run(
            ["bash", str(RUNNER)],
            cwd=ROOT,
            env=_runner_env(compose, project),
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert wrong_mount.returncode == 15
        assert "container migration bytes do not match" in wrong_mount.stderr
        assert (
            _psql(
                compose,
                project,
                "modecissions_gold",
                "SELECT count(checksum) FROM schema_migrations",
            ).stdout.strip()
            == "0"
        )
        _run(
            compose,
            project,
            "cp",
            str(ROOT / "infra" / "init" / "99zzu_analytic_app_manifest_registry.sql"),
            "postgres:/docker-entrypoint-initdb.d/99zzu_analytic_app_manifest_registry.sql",
        )

        # Pre-create 99zzt's objects, then remove one function.  On the guarded
        # run 99zzt recreates it, while this trigger makes 99zzu fail.  The
        # function must remain absent if both files really share one rollback.
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
            "-f",
            "/docker-entrypoint-initdb.d/99zzt_analytic_app_dataset_grants.sql",
        )
        _psql(
            compose,
            project,
            "modecissions",
            """
DROP FUNCTION public.analytic_app_granted_datasets(TEXT, TEXT);
CREATE FUNCTION public.omega_test_reject_registry() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected registry failure'; END $$;
CREATE TRIGGER omega_test_reject_registry
BEFORE INSERT OR UPDATE ON public.analytic_app_manifests
FOR EACH ROW EXECUTE FUNCTION public.omega_test_reject_registry();
""",
        )

        failed = subprocess.run(
            ["bash", str(RUNNER)],
            cwd=ROOT,
            env=_runner_env(compose, project),
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert failed.returncode != 0
        assert "injected registry failure" in failed.stderr
        rolled_back = _psql(
            compose,
            project,
            "modecissions",
            """
SELECT count(*), count(checksum),
       to_regprocedure('public.analytic_app_granted_datasets(text,text)') IS NULL,
       NOT EXISTS (
         SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='schema_migrations'
            AND column_name='checksum_source_ref')
FROM schema_migrations;
""",
        ).stdout.strip()
        assert rolled_back == "198|0|t|t"

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
            """
DROP TRIGGER omega_test_reject_registry ON public.analytic_app_manifests;
DROP FUNCTION public.omega_test_reject_registry();
""",
        )
        first = subprocess.run(
            ["bash", str(RUNNER)],
            cwd=ROOT,
            env=_runner_env(compose, project),
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert first.returncode == 0, first.stdout + first.stderr
        assert (
            "operational=200 gold=12 baseline_expected=210 "
            "guarded_transaction=2" in first.stdout
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
         '99zzu_analytic_app_manifest_registry.sql'))
FROM schema_migrations;
""",
        ).stdout.strip()
        assert final == "200|200|200|200|200|2|198|2|2"
        applied_before = _psql(
            compose,
            project,
            "modecissions",
            """
SELECT string_agg(filename || '=' || applied_at::text, ',' ORDER BY filename)
FROM schema_migrations
WHERE filename IN (
 '99zzt_analytic_app_dataset_grants.sql',
 '99zzu_analytic_app_manifest_registry.sql');
""",
        ).stdout.strip()

        second = subprocess.run(
            ["bash", str(RUNNER)],
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
 '99zzu_analytic_app_manifest_registry.sql');
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
            ["bash", str(RUNNER)],
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

        # Exercise both historical ACL leakage and the PostgreSQL 15
        # predefined writer-membership bypass.  The bootstrap transaction must
        # remove both before it can commit normalized evidence.
        _psql(
            compose,
            project,
            "modecissions",
            "GRANT pg_write_all_data TO omega_console; "
            "GRANT INSERT, UPDATE, DELETE ON schema_migrations TO omega_console",
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
            ["bash", str(RUNNER)],
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
            "operational=200 gold=12 baseline_expected=212 guarded_transaction=0"
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
       NOT pg_has_role('omega_console','pg_write_all_data','MEMBER'),
       has_table_privilege('postgres','public.schema_migrations','INSERT,UPDATE,DELETE,TRUNCATE')
  FROM schema_migrations;
""",
            ).stdout.strip()
            == "200|200|200|0|t|t|t|t"
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
   '99zzu_analytic_app_manifest_registry.sql'
 ]);
""",
        ).stdout.strip()
        second = subprocess.run(
            ["bash", str(RUNNER)],
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
   '99zzu_analytic_app_manifest_registry.sql'
 ]);
""",
        ).stdout.strip()
        assert normalized_after == normalized_before
    finally:
        _run(compose, project, "down", "-v", check=False)
