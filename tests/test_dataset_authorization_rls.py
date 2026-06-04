"""Regression tests for the dataset RLS bug in refinement's authorization layer.

Bug: ``_dataset_allowed`` builds the logical prefix ``f"{layer}/{cartridge}/{name}/"``
(trailing slash -> 4 segments, last empty). ``_prefix_allowed``'s silver/gold
branch only accepted 3-segment (legacy) or 5+-segment (tenant-partitioned
physical) paths for scoped users, so every dataset in a real workspace was
rejected -> /datasets, /api/catalog and /api/lineage returned 0 datasets.

Fix: the silver/gold branch now also accepts the 4-segment logical form
``layer/cartridge/name/``. Cartridge access is gated by ``allowed_cartridges``
and workspace isolation is enforced by ``_dataset_allowed`` itself, so this is
the same logical-identity grant as the existing 3-segment form.

These tests exercise the real functions (imported, not parsed).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[1]

# The module validates INTERNAL_API_KEY at import time and constructs a
# DuckDBEngine (lazy — no connection). Provide safe defaults so the import
# succeeds in CI without touching real infra. setdefault keeps any real values.
os.environ.setdefault("INTERNAL_API_KEY", "x7Qp9zR2mK4vL8wN6tJ3sH1bD5fG0aYcE7uV2iO9kP4qZ")
os.environ.setdefault("MINIO_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")
for module_name in list(sys.modules):
    if module_name == "app" or module_name.startswith("app."):
        sys.modules.pop(module_name, None)
sys.path.insert(0, str(REPO_ROOT / "refinement"))

from app import main as refinement_main  # noqa: E402

_dataset_allowed = refinement_main._dataset_allowed
_prefix_allowed = refinement_main._prefix_allowed


def _scoped_sec(*, workspace="ws-1", cartridges=("replicon", "sap_hcm")):
    return {
        "trusted": True,
        "source": "console",
        "role": "member",
        "tenant_id": "tenant-1",
        "workspace_id": workspace,
        "allowed_cartridges": list(cartridges),
        "permissions": ["datasets.read"],
    }


def _dataset(*, cartridge="replicon", layer="silver", name="replicon_project_latest", workspace="ws-1"):
    return {"cartridge": cartridge, "layer": layer, "name": name, "workspace_id": workspace}


# 1. Happy path that used to fail: scoped user, matching workspace, allowed cartridge.
def test_scoped_user_sees_own_dataset():
    assert _dataset_allowed(_scoped_sec(), _dataset()) is True


def test_scoped_user_sees_own_gold_dataset():
    ds = _dataset(cartridge="sap_hcm", layer="gold", name="headcount_by_department")
    assert _dataset_allowed(_scoped_sec(), ds) is True


# 2. Cartridge not in the user's allowlist -> denied.
def test_scoped_user_denied_when_cartridge_not_allowed():
    ds = _dataset(cartridge="sap_s4hana", name="some_dataset")
    assert _dataset_allowed(_scoped_sec(cartridges=("replicon",)), ds) is False


def test_broad_allowed_prefixes_do_not_override_cartridge_allowlist():
    sec = _scoped_sec(cartridges=("replicon",))
    sec["allowed_prefixes"] = ["raw/", "silver/", "gold/", "cartridges/"]

    ds = _dataset(cartridge="hubspot", name="hubspot_deals_latest")

    assert _prefix_allowed(sec, "silver/hubspot/hubspot_deals_latest/") is False
    assert _dataset_allowed(sec, ds) is False


# 3. Dataset in a different workspace -> denied.
def test_scoped_user_denied_other_workspace():
    ds = _dataset(workspace="ws-OTHER")
    assert _dataset_allowed(_scoped_sec(workspace="ws-1"), ds) is False


# 4. Legacy 3-segment prefix still works (no trailing slash).
def test_legacy_three_part_prefix_allowed():
    assert _prefix_allowed(_scoped_sec(), "silver/replicon/replicon_project_latest") is True


# The fix itself: 4-segment logical prefix (trailing slash) is now accepted.
def test_four_part_logical_prefix_allowed():
    assert _prefix_allowed(_scoped_sec(), "silver/replicon/replicon_project_latest/") is True
    assert _prefix_allowed(_scoped_sec(), "gold/sap_hcm/headcount_by_department/") is True


# A 4-segment path whose last segment is NOT empty must not be treated as the
# logical form (guards against accidental over-acceptance of physical paths).
def test_four_part_nonempty_tail_not_logical_form():
    # tenant partition without the workspace partition -> not a valid 5-part
    # physical path and not the logical trailing-slash form -> denied.
    assert _prefix_allowed(_scoped_sec(), "silver/replicon/x/tenant_id=tenant-1") is False


# 5. Multi-tenant 5+-segment physical paths keep matching on tenant+workspace.
def test_multitenant_five_part_prefix_matches_and_isolates():
    sec = _scoped_sec(workspace="ws-1")
    ok = "silver/replicon/x/tenant_id=tenant-1/workspace_id=ws-1/data.parquet"
    bad = "silver/replicon/x/tenant_id=tenant-9/workspace_id=ws-9/data.parquet"
    assert _prefix_allowed(sec, ok) is True
    assert _prefix_allowed(sec, bad) is False


# 4./5. for the raw layer: untouched by the fix, still behaves as before.
def test_raw_layer_behaviour_unchanged():
    sec = _scoped_sec()
    assert _prefix_allowed(sec, "raw/replicon/Entity") is True  # legacy 3-part
    # logical 4-part raw prefix is NOT accepted (fix is silver/gold only)
    assert _prefix_allowed(sec, "raw/replicon/Entity/") is False


def test_declared_source_physical_glob_requires_scope_for_scoped_query():
    sec = _scoped_sec(cartridges=("sap_hcm",))
    with pytest.raises(HTTPException) as exc:
        refinement_main._require_sql_path_scope(
            sec,
            "s3://lakehouse/raw/sap_hcm/EmployeeMaster/**/*.parquet",
            sources=["raw/sap_hcm/EmployeeMaster"],
        )
    assert exc.value.status_code == 403

    refinement_main._require_sql_path_scope(
        sec,
        "s3://lakehouse/raw/sap_hcm/EmployeeMaster/tenant_id=tenant-1/workspace_id=ws-1/**/*.parquet",
        sources=["raw/sap_hcm/EmployeeMaster"],
    )


def test_declared_source_physical_glob_rejects_foreign_scope_for_scoped_query():
    sec = _scoped_sec(cartridges=("sap_hcm",))
    with pytest.raises(HTTPException) as exc:
        refinement_main._require_sql_path_scope(
            sec,
            "s3://lakehouse/raw/sap_hcm/EmployeeMaster/tenant_id=tenant-9/workspace_id=ws-9/**/*.parquet",
            sources=["raw/sap_hcm/EmployeeMaster"],
        )
    assert exc.value.status_code == 403


def test_registered_dataset_physical_glob_allowed_only_for_dataset_query(monkeypatch):
    sec = _scoped_sec(cartridges=("sap_hcm",))
    path = "s3://lakehouse/silver/sap_hcm/sap_hcm_employee_master_full/**/*.parquet"
    legacy_snapshot_path = "s3://lakehouse/silver/sap_hcm/sap_hcm_employee_master_full/data.parquet"
    scoped_path = (
        "s3://lakehouse/silver/sap_hcm/sap_hcm_employee_master_full/"
        "tenant_id=tenant-1/workspace_id=ws-1/**/*.parquet"
    )

    def fake_get_dataset(name: str):
        if name != "sap_hcm_employee_master_full":
            return None
        return {
            "cartridge": "sap_hcm",
            "layer": "silver",
            "name": name,
            "workspace_id": "ws-1",
        }

    monkeypatch.setattr(refinement_main.store, "get_dataset", fake_get_dataset)
    with pytest.raises(HTTPException):
        refinement_main._require_sql_path_scope(sec, path)

    with pytest.raises(HTTPException):
        refinement_main._require_sql_path_scope(sec, path, allow_registered_dataset_paths=True)

    with pytest.raises(HTTPException):
        refinement_main._require_sql_path_scope(
            sec,
            legacy_snapshot_path,
            allow_registered_dataset_paths=True,
        )

    refinement_main._require_sql_path_scope(
        sec,
        legacy_snapshot_path,
        sources=["silver/sap_hcm/sap_hcm_employee_master_full"],
        allow_registered_dataset_paths=True,
    )
    refinement_main._require_sql_path_scope(sec, scoped_path, allow_registered_dataset_paths=True)


# 4. Unscoped admin (no tenant/workspace, allowed_cartridges == ["*"]) sees everything.
def test_unscoped_admin_sees_all():
    admin = {"trusted": True, "role": "admin", "allowed_cartridges": ["*"]}
    assert _dataset_allowed(admin, _dataset(workspace="ws-ANY", cartridge="anything")) is True


# 6. Endpoint-level proxy: /datasets, /api/catalog and /api/lineage all filter
# the dataset list through _dataset_allowed. Simulate that filter and assert a
# scoped user now receives their workspace's datasets (the symptom was 0).
def test_endpoint_filter_returns_scoped_datasets():
    sec = _scoped_sec(workspace="ws-1", cartridges=("replicon", "sap_hcm"))
    catalog = [
        _dataset(cartridge="replicon", layer="silver", name="replicon_project_latest", workspace="ws-1"),
        _dataset(cartridge="sap_hcm", layer="gold", name="headcount_by_department", workspace="ws-1"),
        _dataset(cartridge="sap_s4hana", layer="silver", name="gl_account", workspace="ws-1"),  # cartridge not allowed
        _dataset(cartridge="replicon", layer="silver", name="other_ws", workspace="ws-2"),       # other workspace
    ]
    visible = [ds for ds in catalog if _dataset_allowed(sec, ds)]
    names = {ds["name"] for ds in visible}
    assert names == {"replicon_project_latest", "headcount_by_department"}


def _body(sec: dict | None = None):
    return {"security_context": sec or _scoped_sec(), "_verified_internal_service": "console"}


def test_sql_storage_scope_rejects_unregistered_table_reads():
    with pytest.raises(HTTPException) as exc:
        refinement_main._require_sql_storage_scope(_body(), "SELECT * FROM users", [])

    assert exc.value.status_code == 403
    assert "table references" in exc.value.detail


def test_sql_storage_scope_rejects_unregistered_pggold_tables(monkeypatch):
    monkeypatch.setattr(refinement_main.store, "get_dataset", lambda _name: None)

    with pytest.raises(HTTPException) as exc:
        refinement_main._require_sql_storage_scope(_body(), "SELECT * FROM pggold.billing", [])

    assert exc.value.status_code == 403
    assert "pggold table is not registered" in exc.value.detail


def test_sql_storage_scope_allows_registered_pggold_gold_tables(monkeypatch):
    def fake_get_dataset(name: str):
        if name in {"sales", "gold_sales"}:
            return {
                "cartridge": "replicon",
                "layer": "gold",
                "name": "sales",
                "workspace_id": "ws-1",
            }
        return None

    monkeypatch.setattr(refinement_main.store, "get_dataset", fake_get_dataset)

    refinement_main._require_sql_storage_scope(_body(), "SELECT * FROM pggold.gold_sales", [])


def test_sql_storage_scope_rejects_pggold_table_registered_as_silver(monkeypatch):
    def fake_get_dataset(name: str):
        if name in {"sales", "gold_sales"}:
            return {
                "cartridge": "replicon",
                "layer": "silver",
                "name": "sales",
                "workspace_id": "ws-1",
            }
        return None

    monkeypatch.setattr(refinement_main.store, "get_dataset", fake_get_dataset)

    with pytest.raises(HTTPException) as exc:
        refinement_main._require_sql_storage_scope(_body(), "SELECT * FROM pggold.gold_sales", [])

    assert exc.value.status_code == 403
    assert "pggold table is not registered" in exc.value.detail


def test_sql_storage_scope_rejects_three_part_external_pggold_reference(monkeypatch):
    def fake_get_dataset(name: str):
        if name in {"sales", "gold_sales"}:
            return {
                "cartridge": "replicon",
                "layer": "gold",
                "name": "sales",
                "workspace_id": "ws-1",
            }
        return None

    monkeypatch.setattr(refinement_main.store, "get_dataset", fake_get_dataset)

    with pytest.raises(HTTPException) as exc:
        refinement_main._require_sql_storage_scope(_body(), "SELECT * FROM other.pggold.gold_sales", [])

    assert exc.value.status_code == 403
    assert "database/schema" in exc.value.detail


def test_gold_sql_scope_allows_declared_registered_silver_source_path(monkeypatch):
    def fake_get_dataset(name: str):
        if name == "timeentry_clean":
            return {
                "cartridge": "replicon",
                "layer": "silver",
                "name": name,
                "workspace_id": "ws-1",
            }
        return None

    monkeypatch.setattr(refinement_main.store, "get_dataset", fake_get_dataset)
    sql = (
        "SELECT * FROM read_parquet('s3://lakehouse/silver/replicon/timeentry_clean/"
        "tenant_id=tenant-1/workspace_id=ws-1/data.parquet')"
    )
    broad_sql = "SELECT * FROM read_parquet('s3://lakehouse/silver/replicon/timeentry_clean/data.parquet')"

    with pytest.raises(HTTPException):
        refinement_main._require_sql_storage_scope(_body(), broad_sql, ["timeentry_clean"])

    refinement_main._require_sql_storage_scope(
        _body(),
        sql,
        ["timeentry_clean"],
        allow_registered_dataset_paths=True,
    )

    with pytest.raises(HTTPException):
        refinement_main._require_sql_storage_scope(
            _body(),
            sql,
            ["other_source"],
            allow_registered_dataset_paths=True,
        )


@pytest.mark.asyncio
async def test_generate_transform_mcp_propagates_gold_layer_for_registered_dataset(monkeypatch):
    captured = {}

    def fake_get_dataset(name: str):
        if name == "timeentry_clean":
            return {
                "cartridge": "replicon",
                "layer": "silver",
                "name": name,
                "workspace_id": "ws-1",
            }
        return None

    def fake_get_dataset_schema(_ds):
        return {"fields": [{"name": "customer_id", "type": "string"}, {"name": "amount", "type": "float"}]}

    async def fake_generate_sql(description, schemas, layer="silver"):
        captured["description"] = description
        captured["schemas"] = schemas
        captured["layer"] = layer
        return "SELECT * FROM pggold.gold_sales", "ok"

    monkeypatch.setattr(refinement_main.store, "get_dataset", fake_get_dataset)
    monkeypatch.setattr(refinement_main.engine, "get_dataset_schema", fake_get_dataset_schema)
    monkeypatch.setattr(refinement_main, "generate_sql", fake_generate_sql)

    result = await refinement_main.mcp_invoke(
        {
            "tool": "generate_transform",
            "args": {
                "description": "sales by customer",
                "sources": ["timeentry_clean"],
                "layer": "gold",
            },
            "security_context": _scoped_sec(cartridges=("replicon",)),
        },
        internal_service="console",
    )

    assert result["layer"] == "gold"
    assert captured["layer"] == "gold"
    assert captured["schemas"]["timeentry_clean"]["fields"][0]["name"] == "customer_id"
