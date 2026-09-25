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
    src = _src()
    here = {p.name for p in INIT_DIR.glob("*.sql")}
    expected = {n for n in here if re.match(r"^(?:0[0-9]|[12]\d|3[0-9]|4[0-2])_", n)}
    for name in expected:
        assert f"'{name}'" in src, (
            f"migration 43 missing backfill entry for {name!r}"
        )


def test_migration_43_self_registers():
    src = _src()
    assert "'43_backfill_schema_migrations.sql'" in src


def test_migration_43_idempotent():
    src = _src()
    assert src.count("ON CONFLICT (filename) DO NOTHING") >= 2


def test_migration_43_creates_table_if_missing():
    src = _src()
    assert "CREATE TABLE IF NOT EXISTS schema_migrations" in src
    assert "filename" in src
    assert "applied_at" in src


def test_runner_registers_every_applied_migration():
    src = RUNNER.read_text(encoding="utf-8")
    assert "INSERT INTO schema_migrations" in src
    assert "ON CONFLICT (filename) DO UPDATE" in src
    assert "COALESCE(schema_migrations.checksum, EXCLUDED.checksum)" in src
    assert "VALUES (:'filename', :'checksum', NOW())" in src


def test_schema_migrations_filename_is_TEXT_UNIQUE():
    runner_src = RUNNER.read_text(encoding="utf-8")
    mig_src = _src()
    for needle in ("filename TEXT NOT NULL UNIQUE",
                   "applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"):
        assert re.search(re.escape(needle).replace(r"\ ", r"\s+"), runner_src), (
            f"runner schema_migrations shape missing {needle!r}"
        )
        assert re.search(re.escape(needle).replace(r"\ ", r"\s+"), mig_src), (
            f"migration 43 schema_migrations shape missing {needle!r}"
        )
