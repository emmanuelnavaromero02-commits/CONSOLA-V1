"""Sprint v1.43.1 — Codex P1-1: schema_migrations backfill for the
files that ran via docker-entrypoint-initdb.d but never went through
scripts/apply_db_migrations.sh.

Static check: migration 43 enumerates every existing init script and
backfills the schema_migrations row with ON CONFLICT DO NOTHING so
re-applies are safe.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INIT_DIR = REPO / "infra/init"
MIGRATION = INIT_DIR / "43_backfill_schema_migrations.sql"
RUNNER = REPO / "scripts/apply_db_migrations.sh"


def _src() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_43_exists():
    assert MIGRATION.exists(), f"missing {MIGRATION}"


def test_migration_43_lists_every_init_file_up_to_42():
    """Every NN_*.sql in infra/init/ up to and including 42 must
    appear in the VALUES list. Files added after 43 do NOT need to be
    listed — the runner will register them naturally."""
    src = _src()
    here = {p.name for p in INIT_DIR.glob("*.sql")}
    expected = {n for n in here if re.match(r"^(?:0[0-9]|[12]\d|3[0-9]|4[0-2])_", n)}
    for name in expected:
        assert f"'{name}'" in src, (
            f"migration 43 missing backfill entry for {name!r}"
        )


def test_migration_43_self_registers():
    """Migration 43 must register itself too — otherwise on the next
    upgrade the runner picks it up as 'new' and applies it again,
    which is idempotent but noisy."""
    src = _src()
    assert "'43_backfill_schema_migrations.sql'" in src


def test_migration_43_idempotent():
    """Every INSERT must be ON CONFLICT (filename) DO NOTHING so the
    backfill can be re-applied without error."""
    src = _src()
    # Both inserts use the same conflict clause.
    assert src.count("ON CONFLICT (filename) DO NOTHING") >= 2


def test_migration_43_creates_table_if_missing():
    """On a fresh install, docker-entrypoint-initdb.d runs the SQL
    files in alphabetical order — migration 16 already created the
    schema_migrations-shaped tables, but 43 is also valid to run
    standalone so we re-CREATE IF NOT EXISTS to be safe."""
    src = _src()
    assert "CREATE TABLE IF NOT EXISTS schema_migrations" in src
    assert "filename" in src
    assert "applied_at" in src


def test_runner_registers_every_applied_migration():
    """Regression: the migration runner must INSERT into
    schema_migrations for every NN_*.sql it applies. Otherwise the
    drift we're backfilling here would just re-accumulate.

    Checkpoint 5.5: the ledger row now also carries the file's sha256
    checksum so a later run can detect a historical migration that was
    edited on disk (drift). The registration guarantee is unchanged."""
    src = RUNNER.read_text(encoding="utf-8")
    # The runner pattern: for each file, BEGIN, \i it, INSERT, COMMIT.
    assert "INSERT INTO schema_migrations" in src
    assert "ON CONFLICT (filename) DO UPDATE" in src
    assert "COALESCE(schema_migrations.checksum, EXCLUDED.checksum)" in src
    assert "VALUES (:'filename', :'checksum', NOW())" in src


def test_schema_migrations_filename_is_TEXT_UNIQUE():
    """Cross-check the runner's CREATE TABLE shape with migration 43's
    so a drift surfaces visibly."""
    runner_src = RUNNER.read_text(encoding="utf-8")
    mig_src = _src()
    for needle in ("filename TEXT NOT NULL UNIQUE",
                   "applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"):
        # Allow whitespace variation but the keywords must match.
        assert re.search(re.escape(needle).replace(r"\ ", r"\s+"), runner_src), (
            f"runner schema_migrations shape missing {needle!r}"
        )
        assert re.search(re.escape(needle).replace(r"\ ", r"\s+"), mig_src), (
            f"migration 43 schema_migrations shape missing {needle!r}"
        )
