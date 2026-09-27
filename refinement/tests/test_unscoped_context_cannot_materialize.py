from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from refinement.app import security_scope
from refinement.app.duckdb_engine import DuckDBEngine


REPO_ROOT = Path(__file__).resolve().parents[2]
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"
UNSCOPED_ADMIN = {
    "role": "super_admin",
    "tenant_id": None,
    "workspace_id": None,
    "_server_trusted_context": True,
    security_scope.UNSCOPED_ADMIN_FLAG: True,
}


def _engine() -> DuckDBEngine:
    engine = DuckDBEngine()
    engine._conn = MagicMock(side_effect=AssertionError("no DuckDB work for an unscoped run"))
    engine._pg_gold_attach = MagicMock(side_effect=AssertionError("no gold access for an unscoped run"))
    return engine


@pytest.mark.parametrize(
    "dataset",
    [
        {"name": "hubspot_deals_latest", "cartridge": "hubspot", "layer": "silver"},
        {"name": "hubspot_pipeline", "cartridge": "hubspot", "layer": "gold"},
        {"name": "banxico_fx_daily", "cartridge": "banxico", "layer": "gold"},
        {"name": "sec_edgar_company_facts", "cartridge": "sec_edgar", "layer": "silver"},
    ],
)
@pytest.mark.parametrize(
    "user_context",
    [
        None,
        {},
        {"tenant_id": TENANT},
        {"workspace_id": WORKSPACE},
        {"role": "admin", "_server_trusted_context": True},
        UNSCOPED_ADMIN,
    ],
    ids=["none", "empty", "tenant-only", "workspace-only", "admin-no-scope", "unscoped-admin"],
)
def test_unscoped_contexts_never_materialize(dataset, user_context):
    ds = {**dataset, "sources": ["raw/hubspot/deals"], "sql_def": "SELECT 1 AS one"}
    with pytest.raises(ValueError, match="requires tenant_id and workspace_id"):
        _engine().materialize(ds, user_context)


def test_unscoped_non_admin_storage_paths_are_rejected():
    engine = DuckDBEngine()
    for user_context in (None, {}, {"role": "admin", "_server_trusted_context": True}):
        with pytest.raises(ValueError, match="outside the caller tenant/workspace scope"):
            engine._validate_scoped_storage_sql(
                "SELECT * FROM read_parquet('s3://lakehouse/raw/hubspot/deals/**/*.parquet')",
                user_context,
            )
        with pytest.raises(ValueError, match="unapproved bucket"):
            engine._validate_scoped_storage_sql(
                "SELECT * FROM read_parquet('s3://other/raw/hubspot/deals/x.parquet')", user_context
            )
        engine._validate_scoped_storage_sql("SELECT * FROM pggold.gold_sales", user_context)


def test_only_a_verified_unscoped_admin_skips_storage_scope():
    engine = DuckDBEngine()
    sql = "SELECT * FROM read_parquet('s3://other/raw/hubspot/deals/**/*.parquet')"
    engine._validate_scoped_storage_sql(sql, UNSCOPED_ADMIN)
    forged = {**UNSCOPED_ADMIN, "_server_trusted_context": "true"}
    with pytest.raises(ValueError):
        engine._validate_scoped_storage_sql(sql, forged)
    with pytest.raises(ValueError):
        engine._validate_scoped_storage_sql(sql, {**UNSCOPED_ADMIN, security_scope.UNSCOPED_ADMIN_FLAG: False})
    with pytest.raises(ValueError):
        engine._validate_scoped_storage_sql(sql, {**UNSCOPED_ADMIN, "role": "analyst"})


def test_engine_user_context_marks_only_verified_wildcard_admins(monkeypatch):
    import refinement.app.main as main

    monkeypatch.setattr(main, "_security_context", lambda body: body["security_context"])
    wildcard = {"trusted": True, "role": "owner", "allowed_cartridges": ["*"]}
    narrow = {"trusted": True, "role": "admin", "allowed_cartridges": ["hubspot"]}
    scoped = {**wildcard, "tenant_id": TENANT, "workspace_id": WORKSPACE}
    flag = security_scope.UNSCOPED_ADMIN_FLAG
    assert main._trusted_user_context({"security_context": wildcard}, {})[flag] is True
    assert main._trusted_user_context({"security_context": narrow}, {})[flag] is False
    assert main._trusted_user_context({"security_context": scoped}, {})[flag] is False
    assert flag not in main._trusted_user_context({"security_context": {}}, {})
    moved = main._is_unscoped_admin_security_context
    assert moved.__module__.endswith("security_scope"), "main re-exports the moved helper"


def test_airflow_materialize_context_is_bounded_by_exact_storage_scope(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "refinement-materialize-scope-test-key-000001")
    monkeypatch.syspath_prepend(str(REPO_ROOT / "airflow" / "dags"))
    sys.modules.pop("runtime_security_context", None)
    from runtime_security_context import build_materialize_context

    context = build_materialize_context(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        cartridge_id="hubspot",
        dataset_name="hubspot_deals_latest",
        run_id="run-1",
    )
    assert "raw/hubspot/" in context["allowed_prefixes"], "raw prefix is intentionally cartridge-wide"
    engine = DuckDBEngine()
    user_context = {"tenant_id": context["tenant_id"], "workspace_id": context["workspace_id"]}
    scope = f"tenant_id={TENANT}/workspace_id={WORKSPACE}"
    engine._validate_scoped_storage_sql(
        f"SELECT * FROM read_parquet('s3://lakehouse/raw/hubspot/deals/{scope}/load_date=2026-09-01/x.parquet')",
        user_context,
    )
    for key in (
        f"raw/hubspot/deals/tenant_id={OTHER}/workspace_id={WORKSPACE}/x.parquet",
        f"raw/hubspot/{scope}/x.parquet",
        f"raw/hubspot/deals/load_date=2026-09-01/{scope}/x.parquet",
        f"raw/hubspot/*/{scope}/x.parquet",
        "raw/hubspot/deals/load_date=2026-09-01/batch_id=1/x.parquet",
    ):
        with pytest.raises(ValueError, match="outside the caller tenant/workspace scope"):
            engine._validate_scoped_storage_sql(
                f"SELECT * FROM read_parquet('s3://lakehouse/{key}')", user_context
            )
