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
BASELINE_SHA = "b3b984fb88f48a5d75e172196e1981a45b54aba90eb3365d7e5afbcdac929221"
RELEASE_SHA = "aefda14599840b6ee419eb6b76878478e6be64e2706a80363d7cfac19cdf79a6"


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

        # Gold committed only its checksum attestation before the operational
        # failure.  This is the only accepted cross-database retry state.
        gold_after_failure = _psql(
            compose,
            project,
            "modecissions_gold",
            "SELECT count(*), count(checksum), count(checksum_attested_at) FROM schema_migrations",
        ).stdout.strip()
        assert gold_after_failure == "12|12|12"

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
        assert "operational=200 gold=12 checksums=attested" in first.stdout
        final = _psql(
            compose,
            project,
            "modecissions",
            """
SELECT count(*), count(checksum), count(checksum_source_ref),
       count(checksum_manifest_sha256), count(checksum_attested_at),
       count(*) FILTER (WHERE filename IN (
         '99zzt_analytic_app_dataset_grants.sql',
         '99zzu_analytic_app_manifest_registry.sql'))
FROM schema_migrations;
""",
        ).stdout.strip()
        assert final == "200|200|200|200|200|2"
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
