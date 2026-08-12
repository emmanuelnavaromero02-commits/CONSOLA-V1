"""Sprint v1.32 — existing DB volumes need an explicit migration path."""
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


# ── Checkpoint 5.5: migration-release contract (drift + lock + timeouts) ─────
#
# main already skips files already in schema_migrations (forward-only) and wraps
# each apply in a BEGIN/COMMIT with ON_ERROR_STOP (atomic). The 5.5 delta closes
# two real gaps: the ``checksum`` column was declared but never populated (no
# drift detection), and there was no lock preventing two concurrent appliers.

def _migration_script() -> str:
    return (REPO_ROOT / "scripts/apply_db_migrations.sh").read_text()


def test_migration_records_a_checksum_per_applied_file():
    """Every applied migration must persist sha256(file) into the ledger so a
    later run can detect that a historical migration was edited."""
    script = _migration_script()
    # A portable sha256 helper (Linux coreutils or BSD/macOS shasum).
    assert "sha256sum" in script and "shasum -a 256" in script
    # The ledger row now carries the checksum on insert.
    assert "INSERT INTO schema_migrations (filename, checksum, applied_at)" in script
    assert ":'checksum'" in script


def test_migration_detects_and_fails_closed_on_drift():
    """A file already recorded whose bytes changed on disk is drift; the runner
    must stop, not silently skip it."""
    script = _migration_script()
    assert "assert_no_drift_and_backfill" in script
    assert "DRIFT" in script
    # The recorded checksum is compared against the on-disk hash and the run
    # exits non-zero when they disagree.
    assert "recorded" in script and "!= \"${disk}\"" in script
    # A legacy row with a NULL checksum is backfilled, not treated as drift.
    assert "UPDATE schema_migrations SET checksum" in script


def test_migration_is_forward_only_and_idempotent():
    """Files already in the ledger are skipped: forward-only + idempotent."""
    script = _migration_script()
    assert "SELECT 1 FROM schema_migrations WHERE filename" in script
    assert "skip ${filename}" in script


def test_migration_uses_transaction_scoped_advisory_lock():
    """Split-brain prevention: a second concurrent applier cannot interleave a
    migration; the advisory lock is transaction-scoped so it releases on commit."""
    script = _migration_script()
    assert "pg_advisory_xact_lock" in script
    # Host-level single-writer guard as defence-in-depth (best-effort; the DB
    # advisory lock is the real guard where flock is unavailable).
    assert "flock" in script


def test_migration_bounds_statements_with_set_local_timeouts():
    """A migration must not be able to hang forever holding locks."""
    script = _migration_script()
    assert "SET LOCAL lock_timeout" in script
    assert "SET LOCAL statement_timeout" in script
    assert "SET LOCAL idle_in_transaction_session_timeout" in script


def test_migration_lock_and_advisory_are_inside_the_apply_transaction():
    """The advisory lock and timeouts must sit between BEGIN and the \\i so they
    protect the actual apply, not a stray session."""
    script = _migration_script()
    # BEGIN ... SET LOCAL ... advisory lock ... \i ... INSERT ... COMMIT ordering.
    begin = script.index("BEGIN;")
    lock = script.index("pg_advisory_xact_lock", begin)
    include = script.index("\\i /docker-entrypoint-initdb.d/${filename}", begin)
    commit = script.index("COMMIT;", begin)
    assert begin < lock < include < commit
