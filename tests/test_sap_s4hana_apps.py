"""Phase 2 Block D2 — SAP S/4HANA analytic apps (HTML dashboards).

2 standalone Chart.js dashboards in cartridges/sap_s4hana/apps/, registered in the
analytic_apps table via migration 85 (mirroring HCM migration 83) and reconciled at
console startup by seed_packaged_apps.py. analytic_apps has NO workspace_id column —
apps are cartridge-scoped; isolation is delegated to the workspace-scoped datasets.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APPS_DIR = REPO_ROOT / "cartridges" / "sap_s4hana" / "apps"
MIGRATION_81 = REPO_ROOT / "infra" / "init" / "81_sap_s4hana_datasets_seed.sql"
MIGRATION_85 = REPO_ROOT / "infra" / "init" / "85_sap_s4hana_apps_seed.sql"

APP_NAMES = ["sap_s4hana_sales_overview", "sap_s4hana_finance_dashboard"]


def _html(name: str) -> str:
    return (APPS_DIR / f"{name}.html").read_text(encoding="utf-8")


def _meta(name: str) -> dict:
    return json.loads((APPS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _gold_dataset_names() -> set[str]:
    sql = MIGRATION_81.read_text(encoding="utf-8")
    pat = r"\$seed\$([A-Za-z0-9_]+)\$seed\$,\s*\$seed\$(silver|gold)\$seed\$"
    return {n for n, layer in re.findall(pat, sql) if layer == "gold"}


# 1. Both HTML files exist and parse.
@pytest.mark.parametrize("name", APP_NAMES)
def test_html_files_exist_and_parse(name):
    path = APPS_DIR / f"{name}.html"
    assert path.is_file(), f"missing {path}"
    html = path.read_text(encoding="utf-8")
    HTMLParser().feed(html)
    assert "<canvas" in html, f"{name}: no chart canvas"
    assert "cdn.jsdelivr.net/npm/chart.js" in html, f"{name}: Chart.js CDN not loaded"


# 2. Metadata header declares cartridge sap_s4hana.
@pytest.mark.parametrize("name", APP_NAMES)
def test_html_metadata_cartridge(name):
    header = _html(name).split("-->", 1)[0]
    assert "cartridge: sap_s4hana" in header, f"{name}: header missing cartridge sap_s4hana"
    assert f"app_id: {name}" in header, f"{name}: header app_id mismatch"


# 3. Datasets referenced exist as gold datasets in migration 81.
def test_referenced_datasets_are_real_golds():
    golds = _gold_dataset_names()
    assert len(golds) == 8
    for name in APP_NAMES:
        for ds in _meta(name)["datasets_used"]:
            assert ds in golds, f"{name}: dataset {ds!r} not a seeded gold (migration 81)"


# 4. Migration 85 parses with sqlglot.
def test_migration_85_parses():
    sqlglot = pytest.importorskip("sqlglot")
    sql = MIGRATION_85.read_text(encoding="utf-8")
    stmts = [s for s in sqlglot.parse(sql, read="postgres") if s]
    assert len(stmts) == 3, f"expected 2 app inserts + 1 schema_migrations, got {len(stmts)}"


# 5. Migration 85 has NO workspace_id in the INSERT column list (lesson #195).
def test_migration_85_no_workspace_id_column():
    sql = MIGRATION_85.read_text(encoding="utf-8")
    cols = re.findall(r"INSERT INTO analytic_apps\s*\(([^)]+)\)", sql)
    assert cols, "no analytic_apps insert"
    for col_list in cols:
        names = {c.strip() for c in col_list.split(",")}
        assert "workspace_id" not in names, "analytic_apps has no workspace_id column"
        assert {"name", "title", "html", "cartridge_id", "visibility", "datasets_used"} <= names


# 6. Migration 85 embeds HTML as $seed$...$seed$ dollar-quoted literals.
def test_migration_85_embeds_html():
    sql = MIGRATION_85.read_text(encoding="utf-8")
    assert sql.count("$seed$<!DOCTYPE html>") == len(APP_NAMES), "HTML not embedded per app"
    assert sql.count("$seed$sap_s4hana$seed$") == len(APP_NAMES), "cartridge_id per app"
    assert sql.count("$seed$shared$seed$") == len(APP_NAMES), "shared visibility per app"


# 7. JSON sidecars valid with the required keys.
@pytest.mark.parametrize("name", APP_NAMES)
def test_json_sidecar_valid(name):
    meta = _meta(name)
    assert meta["name"] == name
    assert meta["title"] and meta["description"]
    assert isinstance(meta["datasets_used"], list) and meta["datasets_used"]


# 8. Alignment: each app in migration 85 has matching .html + .json files.
def test_migration_apps_have_files():
    sql = MIGRATION_85.read_text(encoding="utf-8")
    inserted = set(re.findall(r"VALUES \(\$seed\$([a-z0-9_]+)\$seed\$", sql))
    assert inserted == set(APP_NAMES), f"{inserted} != {set(APP_NAMES)}"
    for name in inserted:
        assert (APPS_DIR / f"{name}.html").is_file()
        assert (APPS_DIR / f"{name}.json").is_file()


# 9. fetch URLs are literal /api/data/<name> (no concatenation), matching declared datasets.
@pytest.mark.parametrize("name", APP_NAMES)
def test_fetch_urls_literal_and_aligned(name):
    html = _html(name)
    fetched = set(re.findall(r"/api/data/([a-z0-9_]+)", html))
    declared = set(_meta(name)["datasets_used"])
    assert fetched == declared, f"{name}: fetched {fetched} != declared {declared}"
    assert "'/api/data/' +" not in html and "`/api/data/${" not in html, \
        f"{name}: uses concatenated fetch URL"
    assert "gold_" not in "".join(re.findall(r"/api/data/[a-z0-9_]+", html))


# 10/11. Naming prefix + scope: only sap_s4hana apps, migration touches only apps tables.
def test_app_filenames_prefixed():
    for path in APPS_DIR.glob("*.html"):
        assert path.stem.startswith("sap_s4hana_"), f"{path.name} not prefixed sap_s4hana_"


def test_migration_85_scope_is_only_apps():
    sqlglot = pytest.importorskip("sqlglot")
    from sqlglot import exp

    sql = MIGRATION_85.read_text(encoding="utf-8")
    targets = set()
    for stmt in (s for s in sqlglot.parse(sql, read="postgres") if s):
        assert isinstance(stmt, exp.Insert), f"non-INSERT: {type(stmt).__name__}"
        tbl = stmt.find(exp.Table)
        targets.add(tbl.name if tbl else None)
    assert targets == {"analytic_apps", "schema_migrations"}, f"unexpected targets: {targets}"
