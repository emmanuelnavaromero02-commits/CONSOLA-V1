"""Phase 2 Block D — SAP HCM analytic apps (HTML dashboards).

2 standalone Chart.js dashboards in cartridges/sap_hcm/apps/, registered in the
analytic_apps table via migration 83 (mirroring Replicon's seed in migration 10)
and reconciled at console startup by seed_packaged_apps.py.

Schema reality (verified against 08_workspace_ownership.sql and the Replicon seed):
analytic_apps has NO workspace_id column — apps are workspace-agnostic, scoped by
cartridge_id + visibility + the workspace-scoped datasets they reference. So these
tests assert workspace scoping is delegated to the datasets (which carry
workspace_id in migration 80), NOT a non-existent app column.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APPS_DIR = REPO_ROOT / "cartridges" / "sap_hcm" / "apps"
MIGRATION_80 = REPO_ROOT / "infra" / "init" / "80_sap_hcm_datasets_seed.sql"
MIGRATION_83 = REPO_ROOT / "infra" / "init" / "83_sap_hcm_apps_seed.sql"

APP_NAMES = ["sap_hcm_headcount_dashboard", "sap_hcm_people_quality_dashboard"]


def _html(name: str) -> str:
    return (APPS_DIR / f"{name}.html").read_text(encoding="utf-8")


def _meta(name: str) -> dict:
    return json.loads((APPS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _gold_dataset_names() -> set[str]:
    sql = MIGRATION_80.read_text(encoding="utf-8")
    pat = r"\$seed\$([a-z0-9_]+)\$seed\$,\s*\$seed\$(silver|gold)\$seed\$"
    return {n for n, layer in re.findall(pat, sql) if layer == "gold"}


# 1. Both HTML files exist and parse as HTML.
@pytest.mark.parametrize("name", APP_NAMES)
def test_html_files_exist_and_parse(name):
    path = APPS_DIR / f"{name}.html"
    assert path.is_file(), f"missing {path}"
    html = path.read_text(encoding="utf-8")
    parser = HTMLParser()
    parser.feed(html)
    parser.close()
    assert "<canvas" in html, f"{name}: no chart canvas"
    assert "cdn.jsdelivr.net/npm/chart.js" in html, f"{name}: Chart.js CDN not loaded"


# 2. Metadata comment header declares cartridge: sap_hcm.
@pytest.mark.parametrize("name", APP_NAMES)
def test_html_metadata_header_cartridge(name):
    html = _html(name)
    header = html.split("-->", 1)[0]
    assert "<!--" in header, f"{name}: no metadata comment header"
    assert "cartridge: sap_hcm" in header, f"{name}: header does not declare cartridge sap_hcm"
    assert f"app_id: {name}" in header, f"{name}: header app_id mismatch"


# 3. Datasets referenced in metadata exist as gold datasets in migration 80.
def test_referenced_datasets_are_real_golds():
    golds = _gold_dataset_names()
    assert len(golds) == 8
    for name in APP_NAMES:
        for ds in _meta(name)["datasets_used"]:
            assert ds in golds, f"{name}: dataset {ds!r} is not a seeded gold (migration 80)"


# 3b. The HTML actually fetches the datasets it declares (bridge whitelist alignment).
@pytest.mark.parametrize("name", APP_NAMES)
def test_html_fetches_declared_datasets(name):
    html = _html(name)
    fetched = set(re.findall(r"/api/data/([a-z0-9_]+)", html))
    declared = set(_meta(name)["datasets_used"])
    assert fetched == declared, f"{name}: fetched {fetched} != declared {declared}"
    # must use the bare dataset name, never the pggold table prefix
    assert "gold_" not in "".join(re.findall(r"/api/data/[a-z0-9_]+", html))


# 4. Migration 83 parses with sqlglot.
def test_migration_83_parses():
    sqlglot = pytest.importorskip("sqlglot")
    sql = MIGRATION_83.read_text(encoding="utf-8")
    stmts = [s for s in sqlglot.parse(sql, read="postgres") if s]
    assert len(stmts) == 3, f"expected 2 app inserts + 1 schema_migrations, got {len(stmts)}"


# 5. Workspace scoping: analytic_apps has no workspace_id column, so the INSERT
# column list must NOT include one; scoping is delegated to the datasets (which
# carry workspace_id in migration 80). Apps are seeded for cartridge sap_hcm with
# shared visibility.
def test_migration_83_schema_and_workspace_scoping():
    sql = MIGRATION_83.read_text(encoding="utf-8")
    cols = re.findall(r"INSERT INTO analytic_apps\s*\(([^)]+)\)", sql)
    assert cols, "no analytic_apps insert found"
    for col_list in cols:
        names = {c.strip() for c in col_list.split(",")}
        assert "workspace_id" not in names, "analytic_apps has no workspace_id column"
        assert {"name", "title", "html", "cartridge_id", "visibility", "datasets_used"} <= names
    assert sql.count("$seed$sap_hcm$seed$") == len(APP_NAMES), "cartridge_id sap_hcm per app"
    assert sql.count("$seed$shared$seed$") == len(APP_NAMES), "shared visibility per app"
    # Workspace isolation is carried by the referenced datasets in migration 80.
    golds_with_ws = MIGRATION_80.read_text(encoding="utf-8")
    assert "workspace_id" in golds_with_ws
    for name in APP_NAMES:
        for ds in _meta(name)["datasets_used"]:
            assert f"$seed${ds}$seed$" in golds_with_ws


# 6. Alignment: each app inserted in migration 83 has a matching HTML file.
def test_migration_apps_have_html_files():
    sql = MIGRATION_83.read_text(encoding="utf-8")
    inserted = set(re.findall(r"VALUES \(\$seed\$([a-z0-9_]+)\$seed\$", sql))
    assert inserted == set(APP_NAMES), f"migration apps {inserted} != {set(APP_NAMES)}"
    for name in inserted:
        assert (APPS_DIR / f"{name}.html").is_file(), f"no HTML file for seeded app {name}"
        assert (APPS_DIR / f"{name}.json").is_file(), f"no JSON sidecar for seeded app {name}"


# 6b. Naming: filenames are prefixed sap_hcm_ to avoid cross-cartridge collision.
def test_app_filenames_prefixed():
    for path in APPS_DIR.glob("*.html"):
        assert path.stem.startswith("sap_hcm_"), f"{path.name} not prefixed sap_hcm_"


# 7/8. Migration 83 only touches analytic_apps + schema_migrations — not bronze,
# datasets, kb_config or any other cartridge's data (Blocks A/B/C stay closed).
# Inspect parsed statements (not raw text) so design comments don't trip the scan.
def test_migration_83_scope_is_only_apps():
    sqlglot = pytest.importorskip("sqlglot")
    from sqlglot import exp

    sql = MIGRATION_83.read_text(encoding="utf-8")
    stmts = [s for s in sqlglot.parse(sql, read="postgres") if s]
    assert stmts, "no statements parsed"
    targets = set()
    for stmt in stmts:
        assert isinstance(stmt, exp.Insert), f"non-INSERT statement: {type(stmt).__name__}"
        for ddl in (exp.Drop, exp.Alter, exp.Create, exp.Delete, exp.Update):
            assert not stmt.find(ddl), f"unexpected {ddl.__name__} in migration"
        tbl = stmt.find(exp.Table)
        targets.add(tbl.name if tbl else None)
    assert targets == {"analytic_apps", "schema_migrations"}, f"unexpected targets: {targets}"
