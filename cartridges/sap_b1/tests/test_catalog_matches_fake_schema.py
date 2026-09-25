from __future__ import annotations

import importlib
import re
from pathlib import Path

import yaml

from app.services import b1_queries as q

CARTRIDGE = Path(__file__).resolve().parents[1]
ENTITIES = CARTRIDGE / "app" / "config" / "entities.yaml"
CONNECTOR = CARTRIDGE / "app" / "config" / "connector.yaml"
KBS = CARTRIDGE / "app" / "config" / "knowledge_bits.yaml"
SEED = CARTRIDGE / "config" / "seed.sql"

b1 = importlib.import_module("sap_b1_fake.schema")


def _entities() -> list[dict]:
    return yaml.safe_load(ENTITIES.read_text(encoding="utf-8"))["entities"]


def test_every_fake_table_is_an_entity_with_exactly_its_columns():
    entities = {e["entity"]: e for e in _entities()}
    assert set(entities) == set(b1.TABLES)
    for table, entity in entities.items():
        assert entity["select_fields"] == b1.columns(table), f"{table}: select_fields drifted from the fake schema"
        assert entity["mode"] in ("full", "incremental")
        assert "protection" in entity


def test_primary_keys_match_the_fake():
    for entity in _entities():
        table = entity["entity"]
        declared = tuple(part.strip() for part in (entity.get("primary_key") or "").split(",") if part.strip())
        assert declared == b1.PRIMARY_KEYS.get(table, ()), f"{table}: primary key differs from the fake"


def test_every_entity_yields_a_plan_and_parents_exist():
    entities = {e["entity"]: e for e in _entities()}
    for entity in entities.values():
        plan = q.plan_from_config(entity)
        if plan.parent:
            parent = entities[plan.parent]
            assert plan.parent_key in parent["select_fields"], f"{plan.entity}: parent_key missing on {plan.parent}"
            assert plan.watermark_field in parent["select_fields"]
            assert plan.watermark_ts_field is None or plan.watermark_ts_field in parent["select_fields"]
            if plan.date_field:
                assert plan.date_field in parent["select_fields"], f"{plan.entity}: date_field must be a header column"
            assert parent.get("watermark_field") == plan.watermark_field
        if entity["mode"] == "incremental":
            assert plan.incremental_capable, f"{plan.entity}: incremental without a watermark"
            assert plan.primary_key, f"{plan.entity}: incremental needs a keyset key"


def test_only_the_single_row_company_table_is_read_without_a_key():
    keyless = {e["entity"] for e in _entities() if not e.get("primary_key")}
    assert keyless == {"CINF"}


def test_every_column_has_a_declared_parquet_type():
    for entity in _entities():
        types = entity["column_types"]
        assert set(types) == set(entity["select_fields"]), f"{entity['entity']}: column_types must cover select_fields"
        assert set(types.values()) <= set(q.ARROW_KINDS)
        assert q.arrow_schema(q.plan_from_config(entity)) is not None
    oinm = next(e for e in _entities() if e["entity"] == "OINM")
    assert oinm["column_types"]["TransValue"] == "decimal(19,6)" and oinm["column_types"]["DocDate"] == "timestamp"
    ibt1 = next(e for e in _entities() if e["entity"] == "IBT1")
    assert ibt1["mode"] == "incremental" and ibt1["watermark_field"] == "LogEntry"


def test_stamped_tables_are_exactly_those_with_update_columns():
    stamped = {e["entity"] for e in _entities() if e.get("watermark_format") == "b1_update_ts" and not e.get("parent")}
    with_columns = {t for t in b1.TABLES if {"UpdateDate", "UpdateTS"} <= set(b1.columns(t))}
    assert stamped == with_columns


def test_seed_sql_mirrors_entities_yaml():
    seed = SEED.read_text(encoding="utf-8")
    rows = re.findall(r"^\s+\('sap_b1', '([A-Z0-9]+)', ", seed, flags=re.MULTILINE)
    entities = _entities()
    assert rows == [e["entity"] for e in entities]
    for entity in entities:
        marker = f"('sap_b1', '{entity['entity']}', "
        line = next(line for line in seed.splitlines() if marker in line)
        assert f"'{entity['mode']}'" in line
        if entity.get("watermark_field"):
            assert f"'{entity['watermark_field']}'" in line
        assert "'sap_b1_extract'" in line and "'manual'" in line
    assert "sap_b1_extract_all" in seed


def test_connector_and_knowledge_bits_are_scoped_to_this_cartridge():
    connector = yaml.safe_load(CONNECTOR.read_text(encoding="utf-8"))["connector"]
    assert connector["id"] == "sap_b1"
    assert connector["storage"]["path_template"].startswith("raw/sap_b1/")
    assert "/tenant_id={tenant_id}/workspace_id={workspace_id}/" in connector["storage"]["path_template"]
    assert connector["database"]["companies_env"] == "SAP_B1_COMPANIES"
    kbs = yaml.safe_load(KBS.read_text(encoding="utf-8"))["knowledge_bits"]
    ids = [kb["id"] for kb in kbs]
    assert len(ids) == len(set(ids)) >= 2
    for kb in kbs:
        assert "read_parquet('s3://{bucket}/raw/sap_b1/" in kb["sql"]
        assert kb["output_path"].startswith("silver/sap_b1/")
        assert "_company" in kb["sql"]


def test_no_customer_specific_names_in_the_catalogue():
    text = "\n".join(p.read_text(encoding="utf-8") for p in (ENTITIES, CONNECTOR, KBS, SEED))
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert not re.search(r"\bSBO[_A-Z0-9-]{3,}\b", text), "a company schema name leaked into the catalogue"
    assert "hanab1" not in text.lower()
