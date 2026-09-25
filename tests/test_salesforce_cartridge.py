from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CART = REPO / "cartridges" / "salesforce"
INIT_SEED = REPO / "infra" / "init" / "94_salesforce_seed.sql"
CART_SEED = CART / "config" / "seed.sql"
ENTITIES_YAML = CART / "app" / "config" / "entities.yaml"

AGENT_SLUGS = (
    "salesforce_pipeline_forecaster",
    "salesforce_deal_risk_sentinel",
    "salesforce_quota_watchdog",
    "salesforce_margin_analyst",
    "salesforce_ops_liaison",
)


def _entities_from_yaml() -> set[str]:
    data = yaml.safe_load(ENTITIES_YAML.read_text(encoding="utf-8"))
    return {e["entity"] for e in data["entities"]}


def test_init_seed_exists_and_registers_cartridge():
    assert INIT_SEED.exists(), f"missing fresh-install seed {INIT_SEED}"
    src = INIT_SEED.read_text(encoding="utf-8")
    assert "INSERT INTO cartridges" in src
    assert "'salesforce'" in src
    assert "ON CONFLICT" in src


def test_init_seed_registers_every_entity_from_yaml():
    src = INIT_SEED.read_text(encoding="utf-8")
    entities = _entities_from_yaml()
    assert len(entities) == 14, f"expected 14 Sales Cloud entities, got {len(entities)}"
    for entity in entities:
        assert f"'{entity}'" in src, f"entity_config missing {entity!r} in fresh-install seed"


def test_init_seed_registers_all_five_agents():
    src = INIT_SEED.read_text(encoding="utf-8")
    assert "INSERT INTO agents" in src
    for slug in AGENT_SLUGS:
        assert f"'{slug}'" in src, f"agent {slug!r} not seeded on fresh install"


def test_init_seed_body_matches_cartridge_seed():
    def _body(text: str) -> str:
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
        return "\n".join(lines).strip()

    assert _body(INIT_SEED.read_text(encoding="utf-8")) == _body(
        CART_SEED.read_text(encoding="utf-8")
    ), "infra/init/94_salesforce_seed.sql drifted from cartridges/salesforce/config/seed.sql"


def test_apps_reference_existing_datasets():
    for app_json in sorted((CART / "apps").glob("*.json")):
        meta = json.loads(app_json.read_text(encoding="utf-8"))
        assert meta["name"] == app_json.stem, f"{app_json.name}: name != filename stem"
        for ds in meta.get("datasets_used", []):
            assert (CART / "datasets" / f"{ds}.sql").exists(), (
                f"{app_json.name} references missing dataset {ds!r}"
            )


def test_all_entities_have_silver_datasets():
    data = yaml.safe_load(ENTITIES_YAML.read_text(encoding="utf-8"))
    entities = data["entities"]
    assert len(entities) == 14, f"expected 14 entities, got {len(entities)}"
    datasets_dir = CART / "datasets"
    for e in entities:
        name = e["entity"]
        expected = datasets_dir / f"salesforce_{name.lower()}_latest.sql"
        assert expected.exists(), (
            f"Entity {name!r} declared in entities.yaml but missing silver dataset "
            f"{expected.name}. Create the file to complete the medallion stack."
        )


def test_agent_gold_and_kb_references_exist():
    seed = CART_SEED.read_text(encoding="utf-8")
    kb_ids = {
        kb["id"]
        for kb in yaml.safe_load(
            (CART / "app" / "config" / "knowledge_bits.yaml").read_text(encoding="utf-8")
        )["knowledge_bits"]
    }
    for gold in set(re.findall(r"gold_(salesforce_[a-z0-9_]+)", seed)):
        assert (CART / "datasets" / f"{gold}.sql").exists(), (
            f"agent references gold {gold!r} with no dataset file"
        )
    for kb in set(re.findall(r"(kb_salesforce_[a-z0-9_]+)", seed)):
        assert kb in kb_ids, f"agent references undefined knowledge bit {kb!r}"
