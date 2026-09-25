from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_makefile_exposes_migrate_target():
    makefile = (REPO_ROOT / "Makefile").read_text()

    phony_lines = [line for line in makefile.splitlines() if line.startswith(".PHONY:")]
    phony_text = "\n".join(phony_lines)
    for target in ("help", "up", "up-core", "down", "test", "smoke", "migrate", "rotate-keys", "verify-release"):
        assert target in phony_text
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
    init = REPO_ROOT / "infra/init"
    matched = sorted(p.name for p in init.glob("[0-9][0-9]_*.sql"))
    assert "45_cascade_to_restrict.sql" in matched
    assert "46_sap_jobs_permissions.sql" in matched
    assert matched.index("45_cascade_to_restrict.sql") < matched.index(
        "46_sap_jobs_permissions.sql"
    )


def _migration_script() -> str:
    return (REPO_ROOT / "scripts/apply_db_migrations.sh").read_text()


def test_migration_ensures_checksum_column_on_preexisting_ledger():
    script = _migration_script()
    assert script.count("ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS checksum TEXT;") == 2


def test_migration_records_a_checksum_per_applied_file():
    script = _migration_script()
    assert "sha256sum" in script and "shasum -a 256" in script
    assert "INSERT INTO schema_migrations (filename, checksum, applied_at)" in script
    assert ":'checksum'" in script


def test_self_registering_migration_supports_fresh_init_and_immediate_runner():
    script = _migration_script()
    migration = (
        REPO_ROOT / "infra/init/99zzzzf_analytic_app_grant_convergence.sql"
    ).read_text()

    assert "INSERT INTO schema_migrations" in migration
    assert "'99zzzzf_analytic_app_grant_convergence.sql'" in migration
    assert "ON CONFLICT (filename) DO NOTHING" in migration
    assert "UPDATE schema_migrations SET checksum" in script
    include = script.index("\\i /docker-entrypoint-initdb.d/${filename}")
    checksum_upsert = script.index("ON CONFLICT (filename) DO UPDATE", include)
    assert include < checksum_upsert
    assert script.count("ON CONFLICT (filename) DO UPDATE") == 2
    assert script.count(
        "SET checksum = COALESCE(schema_migrations.checksum, EXCLUDED.checksum);"
    ) == 2


def test_migration_detects_and_fails_closed_on_drift():
    script = _migration_script()
    assert "assert_no_drift_and_backfill" in script
    assert "DRIFT" in script
    assert "recorded" in script and "!= \"${disk}\"" in script
    assert "UPDATE schema_migrations SET checksum" in script


def test_migration_is_forward_only_and_idempotent():
    script = _migration_script()
    assert "SELECT 1 FROM schema_migrations WHERE filename" in script
    assert "skip ${filename}" in script


def test_migration_uses_transaction_scoped_advisory_lock():
    script = _migration_script()
    assert "pg_advisory_xact_lock" in script
    assert "flock" in script


def test_migration_bounds_statements_with_set_local_timeouts():
    script = _migration_script()
    assert "SET LOCAL lock_timeout" in script
    assert "SET LOCAL statement_timeout" in script
    assert "SET LOCAL idle_in_transaction_session_timeout" in script


def test_migration_lock_and_advisory_are_inside_the_apply_transaction():
    script = _migration_script()
    begin = script.index("BEGIN;")
    lock = script.index("pg_advisory_xact_lock", begin)
    include = script.index("\\i /docker-entrypoint-initdb.d/${filename}", begin)
    commit = script.index("COMMIT;", begin)
    assert begin < lock < include < commit
