"""Sprint v1.32 — existing DB volumes need an explicit migration path."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_makefile_exposes_migrate_target():
    makefile = (REPO_ROOT / "Makefile").read_text()

    phony_lines = [line for line in makefile.splitlines() if line.startswith(".PHONY:")]
    phony_text = "\n".join(phony_lines)
    for target in (
        "help",
        "up",
        "up-core",
        "down",
        "test",
        "smoke",
        "migrate",
        "rotate-keys",
        "verify-release",
    ):
        assert target in phony_text
    assert "migrate:" in makefile
    assert "scripts/apply_db_migrations.sh" in makefile


def test_apply_db_migrations_tracks_schema_migrations_without_secret_argv():
    script = (REPO_ROOT / "scripts/apply_db_migrations.sh").read_text()
    guard = (REPO_ROOT / "scripts/migration_guard.py").read_text()

    assert "schema_migrations" in script
    assert "/docker-entrypoint-initdb.d/{filename}" in guard
    assert "99zzt_analytic_app_dataset_grants.sql" in guard
    assert "99zzu_analytic_app_manifest_registry.sql" in guard
    assert "both databases are inspected" in script
    assert "checksum_manifest_sha256" in guard
    assert "checksum_evidence_kind" in guard
    assert "checksum_guarded_at" in guard
    assert "baseline_expected" in guard
    assert "guarded_transaction" in guard
    assert "PSQL_GOLD" in script
    assert "docker compose" in script
    assert 'source "${ENV_FILE}"' not in script
    assert "PGOPTIONS=" not in script
    assert "PASSWORD:-" not in script
    assert "env-validate" in script
    assert "--forbid-prefix OMEGA_MIGRATION_" in script
    assert '--env-file "$ENV_FILE"' in script
    assert "COMPOSE_DISABLE_ENV_FILE=1" in script
    assert 'exec -T -e "PGOPTIONS=' not in script


def test_apply_db_migrations_rejects_control_plane_or_executable_dotenv(
    tmp_path: Path,
):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OMEGA_MIGRATION_CANDIDATE_REF=" + "1" * 40 + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    environment = {
        **os.environ,
        "OMEGA_MIGRATION_COMPOSE_FILE": str(compose),
        "OMEGA_MIGRATION_ENV_FILE": str(env_file),
    }
    forbidden = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts/apply_db_migrations.sh")],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert forbidden.returncode != 0
    assert "forbidden control-plane prefix" in forbidden.stderr

    marker = tmp_path / "must-not-exist"
    env_file.write_text(f"SAFE_VALUE=$(touch {marker})\n", encoding="utf-8")
    executable = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts/apply_db_migrations.sh")],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert executable.returncode != 0
    assert "executable/interpolated syntax" in executable.stderr
    assert not marker.exists()


def test_gold_role_migration_exists_for_fresh_gold_volumes():
    sql = (REPO_ROOT / "infra/init_gold/34_postgres_gold_role.sql").read_text()

    assert "CREATE ROLE omega_refinement_gold" in sql
    assert "GRANT USAGE, CREATE ON SCHEMA public TO omega_refinement_gold" in sql
    assert "app.omega_refinement_gold_password" in sql


def test_migration_46_picked_up_by_runner_glob():
    """v1.43.3: the runner uses ``infra/init/[0-9][0-9]_*.sql`` and
    bash globs lexicographically. Migration 46 must be the strictly
    largest filename under that pattern after this hotfix lands, so
    fresh boots apply 45's CASCADE→RESTRICT swap BEFORE 46 fixes the
    jobs ownership that 45 doesn't touch. If anyone later adds a 47+
    that re-touches jobs, this test still passes — we only assert
    "46 follows 45".
    """
    init = REPO_ROOT / "infra/init"
    matched = sorted(p.name for p in init.glob("[0-9][0-9]_*.sql"))
    assert "45_cascade_to_restrict.sql" in matched
    assert "46_sap_jobs_permissions.sql" in matched
    assert matched.index("45_cascade_to_restrict.sql") < matched.index(
        "46_sap_jobs_permissions.sql"
    )
