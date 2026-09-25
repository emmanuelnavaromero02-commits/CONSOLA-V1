from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_78 = REPO_ROOT / "infra" / "init" / "78_sap_hcm_alignment.sql"
VALID_RULES = {"plain", "masked", "shadowed", "encrypted"}


def _yaml_entities(cartridge: str) -> list[dict]:
    path = REPO_ROOT / "cartridges" / cartridge / "app" / "config" / "entities.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("entities", [])


def _migration_rows() -> dict[str, str]:
    sql = MIGRATION_78.read_text(encoding="utf-8")
    return dict(re.findall(r"\('sap_hcm',\s*'([^']+)',\s*'([^']+)'", sql))


def test_hcm_entities_have_odata_entity_and_valid_protection():
    entities = _yaml_entities("sap_hcm")
    assert entities, "sap_hcm entities.yaml is empty"
    for entry in entities:
        assert entry.get("odata_entity"), f"{entry.get('entity')} missing odata_entity"
        for field, rule in (entry.get("protection") or {}).items():
            assert rule in VALID_RULES, f"{entry['entity']}.{field}: invalid rule {rule!r}"


def test_hcm_shared_entityset_entities_carry_a_filter():
    shared = [e for e in _yaml_entities("sap_hcm") if e.get("odata_entity", "").endswith("HRP1000Set")]
    filters = [e.get("odata_filter") for e in shared]
    assert len(shared) >= 3
    assert all(filters), "an HRP1000Set entity is missing odata_filter"
    assert len(set(filters)) == len(filters), "HRP1000Set entities share an odata_filter"


def test_migration_78_matches_yaml():
    mig = _migration_rows()
    yaml_map = {e["entity"]: e["odata_entity"] for e in _yaml_entities("sap_hcm")}
    assert set(mig) == set(yaml_map), "migration entities differ from entities.yaml"
    for entity, odata in mig.items():
        assert odata == yaml_map[entity], f"{entity}: {odata!r} != yaml {yaml_map[entity]!r}"


def test_migration_78_is_scoped_and_registered():
    sql = MIGRATION_78.read_text(encoding="utf-8")
    insert_carts = set(re.findall(r"\('(\w+)',\s*'[^']+',\s*'[^']+'", sql))
    assert insert_carts == {"sap_hcm"}, f"migration writes other cartridges: {insert_carts}"
    delete_carts = set(re.findall(r"cartridge_id\s*=\s*'(\w+)'", sql))
    assert delete_carts <= {"sap_hcm"}
    assert "'replicon'" not in sql, "migration must not target Replicon"
    assert "ADD COLUMN IF NOT EXISTS odata_filter" in sql
    assert "'78_sap_hcm_alignment.sql'" in sql


def _load_protection(cartridge: str):
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    os.environ.setdefault("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    path = REPO_ROOT / "cartridges" / cartridge / "app" / "services" / "protection_service.py"
    spec = importlib.util.spec_from_file_location(f"{cartridge}_protection_svc", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hcm_protection_applies_rules_to_personal_data():
    mod = _load_protection("sap_hcm")
    rows = [{"Pernr": "00012345", "Vorna": "Ana", "Nachn": "Garcia", "Gbdat": "1990-01-01", "Gesch": "2"}]
    out = mod.apply_protection_for_entity("PersonalData", rows)[0]

    assert out["Pernr"] != "00012345" and len(out["Pernr"]) == 64
    assert "*" in out["Nachn"] and out["Nachn"].endswith("rcia")
    assert out["Gbdat"] not in ("1990-01-01", None)
    assert out["Gesch"] == "2"


def _seed_entity_config_rows(cartridge: str) -> dict[str, str]:
    sql = (REPO_ROOT / "cartridges" / cartridge / "config" / "seed.sql").read_text(encoding="utf-8")
    block = re.search(r"INSERT INTO entity_config\b.*?VALUES(.*?)ON CONFLICT", sql, re.DOTALL)
    assert block, f"{cartridge} seed.sql has no entity_config INSERT block"
    return dict(re.findall(rf"\('{cartridge}',\s*'([^']+)',\s*'([^']+)'", block.group(1)))


def test_s4hana_entities_have_odata_entity_and_valid_protection():
    entities = _yaml_entities("sap_s4hana")
    assert len(entities) == 25, f"expected 25 ERP entities, got {len(entities)}"
    for entry in entities:
        assert entry.get("odata_entity"), f"{entry.get('entity')} missing odata_entity"
        for field, rule in (entry.get("protection") or {}).items():
            assert rule in VALID_RULES, f"{entry['entity']}.{field}: invalid rule {rule!r}"


def test_s4hana_sensitive_entities_carry_protection():
    by_name = {e["entity"]: e for e in _yaml_entities("sap_s4hana")}
    for name in ("BusinessPartner", "Customer", "Supplier", "BusinessPartnerAddress", "SupplierInvoice"):
        assert by_name[name].get("protection"), f"{name} should declare protection"


def test_seed_s4hana_matches_yaml():
    seed = _seed_entity_config_rows("sap_s4hana")
    yaml_map = {e["entity"]: e["odata_entity"] for e in _yaml_entities("sap_s4hana")}
    assert set(seed) == set(yaml_map), "seed entities differ from entities.yaml"
    for entity, odata in seed.items():
        assert odata == yaml_map[entity], f"{entity}: seed {odata!r} != yaml {yaml_map[entity]!r}"


def test_seed_s4hana_has_no_stale_hcm_rows():
    seed = _seed_entity_config_rows("sap_s4hana")
    for hcm_entity in ("EmployeeMaster", "PersonalData", "ContractData", "WorkSchedule", "LeaveAbsence"):
        assert hcm_entity not in seed, f"stale HCM row {hcm_entity} still in seed"


def test_seed_s4hana_leaves_replicon_and_others_untouched():
    sql = (REPO_ROOT / "cartridges" / "sap_s4hana" / "config" / "seed.sql").read_text(encoding="utf-8")
    cart_ids = set(re.findall(r"\('(\w+)',\s*'[^']+'", sql))
    assert cart_ids == {"sap_s4hana"}, f"seed references other cartridges: {cart_ids}"
    assert "replicon" not in sql.lower()


def test_s4hana_protection_applies_to_customer():
    mod = _load_protection("sap_s4hana")
    rows = [{
        "Customer": "0000123456",
        "TaxNumber1": "ESB12345678",
        "IBAN": "ES9121000418450200051332",
        "BankAccount": "0200051332",
        "CompanyCode": "1000",
    }]
    out = mod.apply_protection_for_entity("Customer", rows)[0]

    assert out["Customer"] != "0000123456" and len(out["Customer"]) == 64
    assert "*" in out["TaxNumber1"] and out["TaxNumber1"].endswith("5678")
    assert out["IBAN"] != "ES9121000418450200051332"
    assert out["BankAccount"] != "0200051332"
    assert out["CompanyCode"] == "1000"


MIGRATION_79 = REPO_ROOT / "infra" / "init" / "79_sap_successfactors_alignment.sql"


def test_sf_entities_contract():
    entities = _yaml_entities("sap_successfactors")
    assert entities, "sap_successfactors entities.yaml is empty"
    names = {e["entity"] for e in entities}
    for new in ("Candidate", "JobRequisition", "GoalPlan", "PerformanceReview", "LearningItem", "EmpJob_History"):
        assert new in names, f"{new} missing from entities.yaml"
    for entry in entities:
        assert entry.get("mode"), f"{entry.get('entity')} missing mode"
        for field, rule in (entry.get("protection") or {}).items():
            assert rule in VALID_RULES, f"{entry['entity']}.{field}: invalid rule {rule!r}"


def test_sf_business_name_entities_carry_odata_entity():
    by_name = {e["entity"]: e for e in _yaml_entities("sap_successfactors")}
    expected = {"GoalPlan": "Goal", "PerformanceReview": "FormHeader",
                "LearningItem": "Item", "EmpJob_History": "EmpJobRelationships"}
    for name, entityset in expected.items():
        assert by_name[name].get("odata_entity") == entityset, f"{name} should map to {entityset}"


def test_sf_seed_entities_exist_in_yaml():
    seed = _seed_entity_config_rows("sap_successfactors")
    yaml_names = {e["entity"] for e in _yaml_entities("sap_successfactors")}
    missing = set(seed) - yaml_names
    assert not missing, f"seed entities with no entities.yaml home (phantoms): {missing}"
    for bad in ("Department", "Division", "Location", "CostCenter"):
        assert bad not in seed, f"misnamed FO row {bad} still in seed"
    for good in ("FODepartment", "FODivision", "FOLocation", "FOCostCenter"):
        assert good in seed, f"{good} missing from seed"


def test_sf_kbs_reference_aligned_entities():
    kb = (REPO_ROOT / "cartridges" / "sap_successfactors" / "app" / "config" / "knowledge_bits.yaml").read_text(encoding="utf-8")
    refs = set(re.findall(r"raw/sap_successfactors/(\w+)/", kb))
    yaml_names = {e["entity"] for e in _yaml_entities("sap_successfactors")}
    orphans = refs - yaml_names
    assert not orphans, f"KB bronze paths with no entities.yaml entry: {orphans}"
    assert "Department" not in refs and "Location" not in refs, "KB still reads pre-rename FO folders"
    assert "FODepartment" in refs and "FOLocation" in refs, "FO rename did not reach the KBs"


def test_migration_79_scoped_and_registered():
    sql = MIGRATION_79.read_text(encoding="utf-8")
    block = re.search(r"INSERT INTO entity_config\b.*?VALUES(.*?)ON CONFLICT", sql, re.DOTALL)
    assert block, "migration 79 has no entity_config INSERT block"
    insert_carts = set(re.findall(r"\('(\w+)',\s*'[^']+',\s*'[^']+'", block.group(1)))
    assert insert_carts == {"sap_successfactors"}, f"migration writes other cartridges: {insert_carts}"
    delete_carts = set(re.findall(r"cartridge_id\s*=\s*'(\w+)'", sql))
    assert delete_carts <= {"sap_successfactors"}
    assert "ADD COLUMN IF NOT EXISTS odata_entity" in sql
    assert "'79_sap_successfactors_alignment.sql'" in sql
    for bad in ("'Department'", "'Division'", "'Location'", "'CostCenter'"):
        assert bad in sql, f"migration should drop misnamed FO row {bad}"


def test_sf_protection_applies_to_user():
    mod = _load_protection("sap_successfactors")
    rows = [{
        "userId": "USR000123",
        "firstName": "Ana",
        "lastName": "Garcia",
        "email": "ana.garcia@example.com",
        "status": "active",
    }]
    out = mod.apply_protection_for_entity("User", rows)[0]

    assert out["userId"] != "USR000123" and len(out["userId"]) == 64
    assert "*" in out["lastName"] and out["lastName"].endswith("rcia")
    assert "*" in out["email"]
    assert out["status"] == "active"
