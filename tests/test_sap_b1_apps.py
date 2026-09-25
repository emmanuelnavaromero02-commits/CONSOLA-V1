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


INDICATORS = REPO_ROOT / "cartridges" / "sap_b1" / "app" / "config" / "indicators.yaml"
REMOVED_DATASETS = ("sap_b1_margin_by_customer_month", "sap_b1_margin_by_item_month", "sap_b1_margin_by_company_month")
CDN_SCRIPT = re.compile(r"<script\b[^>]*\bsrc=\"(https://cdn\.jsdelivr\.net/[^\"]+)\"[^>]*>", re.S)


@pytest.mark.parametrize("name", APP_NAMES)
def test_chart_js_is_pinned_with_subresource_integrity(name):
    html = (APPS / f"{name}.html").read_text(encoding="utf-8")
    tags = [match.group(0) for match in CDN_SCRIPT.finditer(html)]
    assert len(tags) == 1
    tag = tags[0]
    assert re.search(r"/npm/chart\.js@\d+\.\d+\.\d+/dist/chart\.umd\.min\.js\"", tag)
    assert re.search(r"\bintegrity=\"sha384-[A-Za-z0-9+/]{64}\"", tag)
    assert 'crossorigin="anonymous"' in tag


@pytest.mark.parametrize("name", APP_NAMES)
def test_every_chart_is_drawn_behind_the_library_guard(name):
    html = (APPS / f"{name}.html").read_text(encoding="utf-8")
    assert html.count("new Chart(") == 1
    guard = html.index("typeof Chart === 'undefined'")
    assert guard < html.index("new Chart(")


@pytest.mark.parametrize("name", APP_NAMES)
def test_apps_do_not_rely_on_what_the_viewer_sandbox_blocks(name):
    # the app frame is sandboxed with allow-scripts only: no top navigation, no downloads
    html = (APPS / f"{name}.html").read_text(encoding="utf-8")
    assert 'target="_top"' not in html and "window.top" not in html
    assert ".download =" not in html and "createObjectURL" not in html


@pytest.mark.parametrize("name", APP_NAMES)
def test_markup_has_no_inline_event_handlers(name):
    html = (APPS / f"{name}.html").read_text(encoding="utf-8")
    assert not re.search(r"<[a-zA-Z][^>]*\son[a-z]+\s*=", html)


@pytest.mark.parametrize("name", APP_NAMES)
def test_kpi_cards_carry_the_catalog_formula(name):
    import yaml

    html = (APPS / f"{name}.html").read_text(encoding="utf-8")
    used = set(json.loads((APPS / f"{name}.json").read_text(encoding="utf-8"))["datasets_used"])
    catalog = yaml.safe_load(INDICATORS.read_text(encoding="utf-8"))["indicators"]
    shown = [item for item in catalog if item["dataset"] in used]
    assert shown
    for item in shown:
        assert json.dumps(item["formula"], ensure_ascii=False) in html, item["id"]


def test_removed_margin_datasets_are_gone_from_the_apps():
    for path in sorted(APPS.iterdir()):
        text = path.read_text(encoding="utf-8")
        for dataset in REMOVED_DATASETS:
            assert dataset not in text, (path.name, dataset)
