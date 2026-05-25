"""Phase 2 Block A — SAP cartridge foundation alignment (sap_hcm).

sap_hcm now keys entities.yaml by the business names the rest of the platform
uses, mapping each to its technical OData path via ``odata_entity`` (+ an
``odata_filter`` for the OrgUnit / Position / JobCode trio that shares
HRP1000Set). Migration 78 seeds entity_config to match.

These static checks pin the alignment (no live DB) plus a real protection_service
regression. S/4HANA and SuccessFactors are aligned in their own PRs.
"""
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


# ── entities.yaml contract ───────────────────────────────────────────────────

def test_hcm_entities_have_odata_entity_and_valid_protection():
    entities = _yaml_entities("sap_hcm")
    assert entities, "sap_hcm entities.yaml is empty"
    for entry in entities:
        assert entry.get("odata_entity"), f"{entry.get('entity')} missing odata_entity"
        for field, rule in (entry.get("protection") or {}).items():
            assert rule in VALID_RULES, f"{entry['entity']}.{field}: invalid rule {rule!r}"


def test_hcm_shared_entityset_entities_carry_a_filter():
    # OrgUnit / Position / JobCode all resolve to HRP1000Set; each must declare a
    # distinct odata_filter so they don't overwrite each other in bronze.
    shared = [e for e in _yaml_entities("sap_hcm") if e.get("odata_entity", "").endswith("HRP1000Set")]
    filters = [e.get("odata_filter") for e in shared]
    assert len(shared) >= 3
    assert all(filters), "an HRP1000Set entity is missing odata_filter"
    assert len(set(filters)) == len(filters), "HRP1000Set entities share an odata_filter"


# ── migration 78 <-> entities.yaml alignment ────────────────────────────────

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
    # No other cartridge appears as a quoted SQL literal target (comments may
    # mention them in prose).
    assert "'replicon'" not in sql, "migration must not target Replicon"
    assert "ADD COLUMN IF NOT EXISTS odata_filter" in sql
    assert "'78_sap_hcm_alignment.sql'" in sql


# ── protection_service regression ────────────────────────────────────────────

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

    assert out["Pernr"] != "00012345" and len(out["Pernr"]) == 64       # shadowed (sha256 hex)
    assert "*" in out["Nachn"] and out["Nachn"].endswith("rcia")        # masked (last 4 kept)
    assert out["Gbdat"] not in ("1990-01-01", None)                     # encrypted (Fernet token)
    assert out["Gesch"] == "2"                                          # unlisted field untouched
