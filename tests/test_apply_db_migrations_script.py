"""Sprint v1.32 — existing DB volumes need an explicit migration path."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_makefile_exposes_migrate_target():
    makefile = (REPO_ROOT / "Makefile").read_text()

    assert ".PHONY: help up down nuke logs ps test smoke migrate rotate-keys" in makefile
    assert "migrate:" in makefile
    assert "scripts/apply_db_migrations.sh" in makefile


def test_apply_db_migrations_tracks_schema_migrations_and_pgoptions():
    script = (REPO_ROOT / "scripts/apply_db_migrations.sh").read_text()

    assert "schema_migrations" in script
    assert "/docker-entrypoint-initdb.d/${filename}" in script
    assert "infra/init_gold/[0-9][0-9]_*.sql" in script
    assert "PSQL_GOLD" in script
    assert "docker compose -f" in script
    assert "PGOPTIONS=" in script
    assert "app.omega_vault_password" in script
    assert "app.omega_refinement_gold_password" in script


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
