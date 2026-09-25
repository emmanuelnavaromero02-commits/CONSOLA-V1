import pathlib

import sqlglot

MIG = (
    pathlib.Path(__file__).resolve().parents[1]
    / "infra/init/99zz_market_context_enable_schedule.sql"
).read_text(encoding="utf-8")


def test_migration_parses_postgres():
    stmts = [s for s in sqlglot.parse(MIG, read="postgres") if s]
    kinds = [type(s).__name__ for s in stmts]
    assert kinds == ["Update", "Insert", "Insert"], kinds


def test_targets_the_three_market_cartridges_as_scheduled():
    for cartridge in ("banxico", "inegi", "sec_edgar"):
        assert cartridge in MIG, cartridge
    assert "'scheduled'" in MIG
    assert "dataset_refresh_chain" in MIG
    assert "seed_raw" in MIG


def test_scope_is_guarded_so_generic_installs_stay_manual():
    assert "EXISTS (SELECT 1 FROM tenants" in MIG
    assert "EXISTS (SELECT 1 FROM workspaces" in MIG


def test_records_migration_filename_idempotently():
    assert "99zz_market_context_enable_schedule.sql" in MIG
    assert "ON CONFLICT (filename) DO NOTHING" in MIG
