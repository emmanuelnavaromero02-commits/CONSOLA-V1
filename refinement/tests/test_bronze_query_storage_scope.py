from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from refinement.app import main as refinement_main
from refinement.app.duckdb_engine import DuckDBEngine
from refinement.app.storage_scope_policy import has_exact_storage_scope


TENANT = "tenant-a"
WORKSPACE = "workspace-a"
PAIR_CONTEXT = {
    "trusted": True,
    "source": "console",
    "role": "analyst",
    "workspace_role": "workspace_admin",
    "user_id": 41,
    "tenant_id": TENANT,
    "workspace_id": WORKSPACE,
    "permissions": ["datasets.read", "datasets.write"],
    "allowed_cartridges": ["sap_successfactors"],
    "allowed_buckets": ["lakehouse"],
    "allowed_prefixes": ["raw/", "silver/", "gold/", "uploads/"],
}
BODY = {
    "security_context": refinement_main._sign_security_context(PAIR_CONTEXT),
    "_verified_internal_service": "console",
}


@pytest.mark.parametrize(
    "key",
    [
        (
            "raw/sap_successfactors/**/"
            f"tenant_id={TENANT}/workspace_id={WORKSPACE}/*.parquet"
        ),
        (
            "silver/sap_successfactors/**/"
            f"tenant_id={TENANT}/workspace_id={WORKSPACE}/*.parquet"
        ),
        (
            "gold/sap_successfactors/**/"
            f"tenant_id={TENANT}/workspace_id={WORKSPACE}/*.parquet"
        ),
        ("uploads/*/" f"tenant_id={TENANT}/workspace_id={WORKSPACE}/*.parquet"),
    ],
)
def test_glob_before_scope_is_rejected_at_both_boundaries(key: str) -> None:
    assert not has_exact_storage_scope(key, TENANT, WORKSPACE)
    sql = f"SELECT * FROM read_parquet('s3://lakehouse/{key}')"

    with pytest.raises(HTTPException) as public_exc:
        refinement_main._require_sql_storage_scope(BODY, sql, [])
    assert public_exc.value.status_code == 403
    assert public_exc.value.detail == "SQL storage path not allowed"

    engine = object.__new__(DuckDBEngine)
    engine.minio_bucket = "lakehouse"
    engine.storage = SimpleNamespace(config=SimpleNamespace(provider="s3"))
    with pytest.raises(ValueError, match="outside"):
        engine._validate_scoped_storage_sql(
            sql,
            {"tenant_id": TENANT, "workspace_id": WORKSPACE},
        )


@pytest.mark.parametrize(
    "key",
    [
        (
            "raw/sap_successfactors/EmpEmployment/"
            f"tenant_id={TENANT}/workspace_id={WORKSPACE}/**/*.parquet"
        ),
        (
            "silver/sap_successfactors/employees/"
            f"tenant_id={TENANT}/workspace_id={WORKSPACE}/*.parquet"
        ),
        (
            "gold/sap_successfactors/headcount/"
            f"tenant_id={TENANT}/workspace_id={WORKSPACE}/*.parquet"
        ),
        (
            "uploads/sap_successfactors/"
            f"tenant_id={TENANT}/workspace_id={WORKSPACE}/*.parquet"
        ),
    ],
)
def test_glob_after_exact_scope_remains_allowed(key: str) -> None:
    assert has_exact_storage_scope(key, TENANT, WORKSPACE)
