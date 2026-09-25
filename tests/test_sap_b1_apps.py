from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APPS = REPO_ROOT / "cartridges" / "sap_b1" / "apps"
DATASETS = REPO_ROOT / "cartridges" / "sap_b1" / "datasets"
APP_NAMES = sorted(path.stem for path in APPS.glob("*.html"))


def test_every_app_has_its_manifest():
    assert "sap_b1_margen" in APP_NAMES
    assert sorted(path.stem for path in APPS.glob("*.json")) == APP_NAMES


@pytest.mark.parametrize("name", APP_NAMES)
def test_app_parses_declares_its_cartridge_and_reads_only_declared_gold(name):
    html = (APPS / f"{name}.html").read_text(encoding="utf-8")
    meta = json.loads((APPS / f"{name}.json").read_text(encoding="utf-8"))
    HTMLParser().feed(html)
    header = html.split("-->", 1)[0]
    assert f"app_id: {name}" in header and "cartridge: sap_b1" in header
    assert meta["name"] == name and meta["datasets_used"] == sorted(meta["datasets_used"])
    used = set(re.findall(r"['\"]([a-z0-9_]+)['\"]", html)) & {path.stem for path in DATASETS.glob("*.sql")}
    assert used == set(meta["datasets_used"])
    for dataset in meta["datasets_used"]:
        assert "(gold)" in (DATASETS / f"{dataset}.sql").read_text(encoding="utf-8").splitlines()[0], dataset


@pytest.mark.parametrize("name", APP_NAMES)
def test_app_never_injects_raw_values_into_markup(name):
    html = (APPS / f"{name}.html").read_text(encoding="utf-8")
    assert "function esc(" in html
    assert not re.search(r"\$\{\s*r\.[A-Za-z_]+\s*\}", html)
    assert not re.search(r"\$\{\s*(c|p|k)\s*\}", html)
    assert "eval(" not in html and "document.write" not in html
