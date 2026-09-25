import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "infra" / "init" / "77_sap_entity_alignment.sql"
YAML_PATH = REPO_ROOT / "cartridges" / "sap_s4hana" / "app" / "config" / "entities.yaml"


def _yaml_entities() -> dict[str, str]:
    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {}
    return {
        e["entity"]: e.get("odata_entity")
        for e in data.get("entities", [])
        if e.get("entity")
    }


def _migration_rows() -> dict[str, str]:
    sql = MIGRATION.read_text(encoding="utf-8")
    return dict(re.findall(r"\('sap_s4hana',\s*'([^']+)',\s*'([^']+)'", sql))


def test_migration_exists_alters_column_and_registers():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TABLE entity_config ADD COLUMN IF NOT EXISTS odata_entity" in sql
    assert "DELETE FROM entity_config" in sql
    assert "'77_sap_entity_alignment.sql'" in sql
    assert "INSERT INTO schema_migrations" in sql


def test_every_s4hana_entity_maps_to_a_real_odata_entity():
    yaml_entities = _yaml_entities()
    rows = _migration_rows()
    assert rows, "no sap_s4hana INSERT rows parsed from the migration"
    for entity, odata in rows.items():
        assert entity in yaml_entities, f"{entity} is not declared in entities.yaml"
        assert odata == yaml_entities[entity], (
            f"{entity}: odata_entity {odata!r} != entities.yaml {yaml_entities[entity]!r}"
        )


def test_migration_covers_every_yaml_entity():
    assert set(_migration_rows()) == set(_yaml_entities())


def test_migration_only_touches_sap_s4hana():
    sql = MIGRATION.read_text(encoding="utf-8")
    insert_cartridges = set(re.findall(r"\('(\w+)',\s*'[^']+',\s*'[^']+'", sql))
    assert insert_cartridges == {"sap_s4hana"}
    delete_cartridges = set(re.findall(r"cartridge_id\s*=\s*'(\w+)'", sql))
    assert delete_cartridges <= {"sap_s4hana"}
    assert "'sap_hcm'" not in sql
    assert "'sap_successfactors'" not in sql
    assert "'replicon'" not in sql
