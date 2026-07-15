"""Chequeo ESTRUCTURAL de la migración 99zz_market_context_enable_schedule.sql.

Limitación honesta: sin Postgres local no se puede ejecutar la migración; este test
valida solo estructura/invariantes (parseo, scope guardado, fuentes objetivo). La
verificación FUNCIONAL (que el entity_scheduler dispare y el gold se materialice) es
post-deploy en prod (readiness deja insufficient_data; gold_rowcounts > 0).
"""
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
    # 1a actualiza los extract; 1b agenda el refresh chain hacia el gold.
    assert "dataset_refresh_chain" in MIG
    assert "seed_raw" in MIG


def test_scope_is_guarded_so_generic_installs_stay_manual():
    # El scope FEMSA solo aplica si el tenant/workspace existen (EXISTS guard),
    # para no apuntar a un scope inexistente en installs genéricos/locales.
    assert "EXISTS (SELECT 1 FROM tenants" in MIG
    assert "EXISTS (SELECT 1 FROM workspaces" in MIG


def test_records_migration_filename_idempotently():
    assert "99zz_market_context_enable_schedule.sql" in MIG
    assert "ON CONFLICT (filename) DO NOTHING" in MIG
