from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
NAME = "sap_successfactors_talent_succession_coverage"
GOLD = ROOT / "cartridges/sap_successfactors/datasets" / f"{NAME}.sql"
MIGRATION = ROOT / "infra/init/99zzzzzzc_talent_succession_coverage.sql"
ORDERS = ROOT / "cartridges/sap_successfactors/app/config/gold_dataset_orders.json"
MANIFEST = ROOT / "console/app/services/control_room/data_readiness_manifest.yaml"


def _migration() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_sorts_after_the_talent_seed_migrations():
    assert MIGRATION.name > "99zzzzzzb_"
    assert MIGRATION.name > "99zzzzzza_talent_attrition_exposure_seed.sql"


def test_migration_registers_the_gold_idempotently():
    sql = _migration()
    assert "WHERE NOT EXISTS" in sql
    assert "ON CONFLICT (workspace_id, name) DO NOTHING;" in sql
    assert f"('{NAME}', 'gold', 'sap_successfactors'," in sql
    assert f"WHERE name = '{NAME}'" in sql
    sources = re.search(r"^-- sources:\s*(\[.*\])\s*$", GOLD.read_text(encoding="utf-8"), re.M)
    assert f"'{json.dumps(json.loads(sources.group(1)))}'::jsonb" in sql
    assert "INSERT INTO schema_migrations (filename, applied_at)" in sql
    assert f"VALUES ('{MIGRATION.name}', NOW())" in sql


def test_migration_only_rewinds_the_position_watermark():
    sql = _migration()
    update = sql[sql.index("UPDATE entity_watermarks"):]
    update = update[: update.index(";")]
    assert "SET last_watermark_value = NULL" in update
    assert "cartridge_id = 'sap_successfactors'" in update
    assert "entity_name = 'Position'" in update
    assert sql.count("UPDATE entity_watermarks") == 1
    statements = re.sub(r"\$sql\$.*?\$sql\$", "", sql, flags=re.DOTALL).upper()
    for forbidden in ("DELETE", "TRUNCATE", "DROP ", "ALTER TABLE"):
        assert forbidden not in statements


def test_gold_runs_in_the_talent_cascade_after_its_silver_inputs():
    orders = json.loads(ORDERS.read_text(encoding="utf-8"))
    for key in ("talent_order", "talent_operational_order"):
        order = orders[key]
        assert order.count(NAME) == 1
        assert order.index(NAME) < order.index("sap_successfactors_talent_operational_features")
        assert order[-2:] == [
            "sap_successfactors_talent_operational_features",
            "sap_successfactors_talent_simulation_inputs",
        ]
    assert NAME not in orders["talent_contract_order"]
    assert NAME not in orders["foundation_order"]


def test_readiness_manifest_states_the_real_blockers():
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["datasets"] if item["dataset"] == NAME)
    assert entry["cartridge"] == "sap_successfactors"
    assert entry["readiness"] == "partial"
    blockers = " ".join(entry["blockers"])
    assert "criticality" in blockers
    assert "SuccessionNomination" in blockers
    assert f"infra/init/{MIGRATION.name}" in entry["evidence"]


def test_packaged_catalog_pin_matches_the_repository():
    sys.path.insert(0, str(ROOT / "console"))
    from app.services import seed_packaged_datasets  # noqa: PLC0415
    from app.services.seed_packaged_catalog import dataset_files  # noqa: PLC0415

    packaged = dataset_files(
        ROOT / "cartridges",
        expected_files=seed_packaged_datasets._EXPECTED_CATALOG_FILES,
        expected_digest=seed_packaged_datasets._EXPECTED_CATALOG_DIGEST,
    )
    assert GOLD in packaged["sap_successfactors"]


def test_startup_seed_carries_the_current_gold_sql_independently_of_the_migration():
    sys.path.insert(0, str(ROOT / "console"))
    from app.services.seed_packaged_catalog import load_packaged_manifest  # noqa: PLC0415

    manifest = load_packaged_manifest({"sap_successfactors": [GOLD]})
    entry = manifest["sap_successfactors"][0]
    assert entry["name"] == NAME
    assert entry["layer"] == "gold"
    assert entry["sql"] == GOLD.read_text(encoding="utf-8")
    assert entry["sources"] == [
        "silver/sap_successfactors/sap_successfactors_position_latest",
        "silver/sap_successfactors/sap_successfactors_successionnomination_latest",
    ]
