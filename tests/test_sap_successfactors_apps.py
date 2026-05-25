"""Phase 2 Block D3 — SAP SuccessFactors analytic apps (HTML dashboards).

2 standalone Chart.js dashboards in cartridges/sap_successfactors/apps/, registered
in analytic_apps via migration 87 (mirroring HCM #83 / S4 #85) and reconciled at
console startup by seed_packaged_apps.py. analytic_apps has NO workspace_id column.
SF gold dataset names carry the sap_successfactors_ prefix, so fetch URLs use the full
prefixed name.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APPS_DIR = REPO_ROOT / "cartridges" / "sap_successfactors" / "apps"
MIGRATION_82 = REPO_ROOT / "infra" / "init" / "82_sap_successfactors_datasets_seed.sql"
MIGRATION_87 = REPO_ROOT / "infra" / "init" / "87_sap_successfactors_apps_seed.sql"

APP_NAMES = ["sap_successfactors_workforce_overview", "sap_successfactors_talent_health"]


def _html(name: str) -> str:
    return (APPS_DIR / f"{name}.html").read_text(encoding="utf-8")


def _meta(name: str) -> dict:
    return json.loads((APPS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _dataset_names() -> set[str]:
    sql = MIGRATION_82.read_text(encoding="utf-8")
    pat = r"\$seed\$([A-Za-z0-9_]+)\$seed\$,\s*\$seed\$(silver|gold)\$seed\$"
    return {n for n, _ in re.findall(pat, sql)}


@pytest.mark.parametrize("name", APP_NAMES)
def test_html_files_exist_and_parse(name):
    path = APPS_DIR / f"{name}.html"
    assert path.is_file(), f"missing {path}"
    html = path.read_text(encoding="utf-8")
    HTMLParser().feed(html)
    assert "<canvas" in html, f"{name}: no chart canvas"
    assert "cdn.jsdelivr.net/npm/chart.js" in html, f"{name}: Chart.js CDN not loaded"


@pytest.mark.parametrize("name", APP_NAMES)
def test_html_metadata_cartridge(name):
    header = _html(name).split("-->", 1)[0]
    assert "cartridge: sap_successfactors" in header, f"{name}: header missing cartridge"
    assert f"app_id: {name}" in header, f"{name}: header app_id mismatch"


def test_referenced_datasets_are_real():
    datasets = _dataset_names()
    for name in APP_NAMES:
        for ds in _meta(name)["datasets_used"]:
            assert ds in datasets, f"{name}: dataset {ds!r} not seeded in migration 82"


def test_migration_87_parses():
    sqlglot = pytest.importorskip("sqlglot")
    sql = MIGRATION_87.read_text(encoding="utf-8")
    stmts = [s for s in sqlglot.parse(sql, read="postgres") if s]
    assert len(stmts) == 3, f"expected 2 app inserts + 1 schema_migrations, got {len(stmts)}"


def test_migration_87_no_workspace_id_column():
    sql = MIGRATION_87.read_text(encoding="utf-8")
    cols = re.findall(r"INSERT INTO analytic_apps\s*\(([^)]+)\)", sql)
    assert cols, "no analytic_apps insert"
    for col_list in cols:
        names = {c.strip() for c in col_list.split(",")}
        assert "workspace_id" not in names, "analytic_apps has no workspace_id column"
        assert {"name", "title", "html", "cartridge_id", "visibility", "datasets_used"} <= names


def test_migration_87_embeds_html():
    sql = MIGRATION_87.read_text(encoding="utf-8")
    assert sql.count("$seed$<!DOCTYPE html>") == len(APP_NAMES)
    assert sql.count("$seed$sap_successfactors$seed$") == len(APP_NAMES)
    assert sql.count("$seed$shared$seed$") == len(APP_NAMES)


@pytest.mark.parametrize("name", APP_NAMES)
def test_json_sidecar_valid(name):
    meta = _meta(name)
    assert meta["name"] == name
    assert meta["title"] and meta["description"]
    assert isinstance(meta["datasets_used"], list) and meta["datasets_used"]


def test_migration_apps_have_files():
    sql = MIGRATION_87.read_text(encoding="utf-8")
    inserted = set(re.findall(r"VALUES \(\$seed\$([a-z0-9_]+)\$seed\$", sql))
    assert inserted == set(APP_NAMES), f"{inserted} != {set(APP_NAMES)}"
    for name in inserted:
        assert (APPS_DIR / f"{name}.html").is_file()
        assert (APPS_DIR / f"{name}.json").is_file()


@pytest.mark.parametrize("name", APP_NAMES)
def test_fetch_urls_literal_and_aligned(name):
    html = _html(name)
    fetched = set(re.findall(r"/api/data/([a-z0-9_]+)", html))
    declared = set(_meta(name)["datasets_used"])
    assert fetched == declared, f"{name}: fetched {fetched} != declared {declared}"
    assert "'/api/data/' +" not in html and "`/api/data/${" not in html, \
        f"{name}: uses concatenated fetch URL"
    # SF apps must use the full sap_successfactors_-prefixed dataset name
    for ds in fetched:
        assert ds.startswith("sap_successfactors_"), f"{name}: fetch {ds} missing prefix"


def test_app_filenames_prefixed():
    for path in APPS_DIR.glob("*.html"):
        assert path.stem.startswith("sap_successfactors_"), f"{path.name} not prefixed"


def test_migration_87_scope_is_only_apps():
    sqlglot = pytest.importorskip("sqlglot")
    from sqlglot import exp

    sql = MIGRATION_87.read_text(encoding="utf-8")
    targets = set()
    for stmt in (s for s in sqlglot.parse(sql, read="postgres") if s):
        assert isinstance(stmt, exp.Insert), f"non-INSERT: {type(stmt).__name__}"
        tbl = stmt.find(exp.Table)
        targets.add(tbl.name if tbl else None)
    assert targets == {"analytic_apps", "schema_migrations"}, f"unexpected targets: {targets}"
