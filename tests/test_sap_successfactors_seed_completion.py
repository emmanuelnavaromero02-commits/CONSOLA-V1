"""Phase 1 / P0 — SAP SuccessFactors seed completion (15 -> 30 entities).

Migration 79 seeded only 15 of the 30 entities declared in entities.yaml, so the
live DB catalog diverged from the YAML and test_sap_entities_seeded failed
(sap_successfactors >= 30, got 15). Migration 89 UPSERTs all 30; the cartridge
config/seed.sql is completed to 30; and catalog_service tops up entity_config on
every startup (ON CONFLICT DO NOTHING) so partial states self-heal.

These are static checks (runnable without Postgres); the live count test in
tests/test_sap_entities_seeded.py passes in CI with migration 89 applied.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_89 = REPO_ROOT / "infra" / "init" / "89_sap_successfactors_seed_completion.sql"
ENTITIES_YAML = REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "config" / "entities.yaml"
CARTRIDGE_SEED = REPO_ROOT / "cartridges" / "sap_successfactors" / "config" / "seed.sql"
CATALOG_SERVICE = REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "services" / "catalog_service.py"


def _yaml_entities() -> set[str]:
    data = yaml.safe_load(ENTITIES_YAML.read_text(encoding="utf-8")) or {}
    return {e["entity"] for e in data.get("entities", [])}


def _entities_in_block(sql: str) -> list[str]:
    block = re.search(r"INSERT INTO entity_config.*?ON CONFLICT", sql, re.DOTALL)
    assert block, "no entity_config insert block found"
    return re.findall(r"\('sap_successfactors',\s*'([A-Za-z0-9_]+)'", block.group(0))


def test_yaml_declares_30_entities():
    assert len(_yaml_entities()) == 30, "entities.yaml must declare 30 entities"


def test_migration_89_seeds_all_30_yaml_entities():
    rows = _entities_in_block(MIGRATION_89.read_text(encoding="utf-8"))
    assert len(rows) == 30, f"migration 89 must seed 30 entities, got {len(rows)}"
    assert len(rows) == len(set(rows)), "migration 89 has duplicate entity rows"
    assert set(rows) == _yaml_entities(), "migration 89 entities != entities.yaml set"


def test_migration_89_includes_the_15_previously_missing():
    rows = set(_entities_in_block(MIGRATION_89.read_text(encoding="utf-8")))
    previously_missing = {
        "PerPerson", "PerPersonal", "PerEmail", "PerPhone", "PerAddressDEFLT",
        "PerNationalId", "EmpPayCompRecurring", "EmpPayCompNonRecurring",
        "EmpEmploymentTermination", "FOCompany", "FOBusinessUnit", "FOJobCode",
        "EmployeeTime", "TimeAccount", "WorkSchedule",
    }
    assert previously_missing <= rows, f"missing: {previously_missing - rows}"


def test_migration_89_is_idempotent_and_scoped():
    sql = MIGRATION_89.read_text(encoding="utf-8")
    # Idempotent UPSERT, registers itself, never deletes existing rows.
    assert "ON CONFLICT (cartridge_id, entity) DO UPDATE" in sql
    assert "89_sap_successfactors_seed_completion.sql" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql
    assert re.search(r"\bDELETE\b", sql, re.IGNORECASE) is None, "must not delete rows"
    # entity_config has no workspace_id — must not appear in the INSERT column list.
    cols = re.search(r"INSERT INTO entity_config\s*\(([^)]+)\)", sql, re.DOTALL)
    assert cols and "workspace_id" not in cols.group(1)


def test_migration_89_parses_with_sqlglot():
    import pytest
    sqlglot = pytest.importorskip("sqlglot")
    stmts = [s for s in sqlglot.parse(MIGRATION_89.read_text(encoding="utf-8"), read="postgres") if s]
    assert len(stmts) == 2  # entity_config upsert + schema_migrations


def test_cartridge_seed_completed_to_30():
    rows = _entities_in_block(CARTRIDGE_SEED.read_text(encoding="utf-8"))
    assert set(rows) == _yaml_entities(), "config/seed.sql entity_config != entities.yaml set"


def test_catalog_service_always_tops_up_not_gated_on_empty():
    src = CATALOG_SERVICE.read_text(encoding="utf-8")
    # The entity seeding must no longer be gated on an empty table.
    assert "if count == 0:" not in src, "entity seeding is still gated on count == 0"
    # Self-heal contract: insert from YAML with a non-clobbering conflict policy.
    assert "for e in _yaml_entities():" in src
    assert "ON CONFLICT (cartridge_id, entity) DO NOTHING" in src
