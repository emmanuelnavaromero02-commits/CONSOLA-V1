"""The seed must stay byte-identical to the cartridge sources.

Migration 87 embeds the app HTML inline so a fresh database can serve the apps
before the console reconciles from /registry. That copy had silently drifted
from the cartridge files, so a stale dashboard shipped on first boot. The seed
is generated now, and this test is what keeps it honest.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "infra" / "init" / "87_sap_successfactors_apps_seed.sql"
APPS_DIR = REPO / "cartridges" / "sap_successfactors" / "apps"
GENERATOR = REPO / "scripts" / "generate_successfactors_apps_seed.py"
APP_NAMES = (
    "sap_successfactors_workforce_overview",
    "sap_successfactors_talent_health",
)


def _generator():
    spec = importlib.util.spec_from_file_location("sf_seed_generator", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_seed_matches_the_generator_output():
    assert SEED.read_text(encoding="utf-8") == _generator().render(), (
        "87_sap_successfactors_apps_seed.sql is stale; "
        "run scripts/generate_successfactors_apps_seed.py"
    )


@pytest.mark.parametrize("name", APP_NAMES)
def test_seed_embeds_the_current_html(name):
    sql = SEED.read_text(encoding="utf-8")
    html = (APPS_DIR / f"{name}.html").read_text(encoding="utf-8")
    assert html in sql, f"{name}: embedded HTML differs from the cartridge file"


@pytest.mark.parametrize("name", APP_NAMES)
def test_seed_metadata_matches_the_sidecar(name):
    sql = SEED.read_text(encoding="utf-8")
    meta = json.loads((APPS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert f"$seed${meta['title']}$seed$" in sql
    assert f"$seed${meta['description']}$seed$" in sql
    assert "{" + ",".join(meta["datasets_used"]) + "}" in sql


def test_seed_registers_exactly_the_two_published_apps():
    sql = SEED.read_text(encoding="utf-8")
    inserted = re.findall(r"VALUES \(\$seed\$([a-z_]+)\$seed\$", sql)
    assert inserted == list(APP_NAMES), "the seed must not invent or drop apps"
