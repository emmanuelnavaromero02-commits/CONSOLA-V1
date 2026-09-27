from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from refinement.app import main as refinement_main
from refinement.app.duckdb_engine import DuckDBEngine
from refinement.app.sql_table_function_policy import validate_table_function_query


TENANT = "tenant-a"
WORKSPACE = "workspace-a"
SOURCE = "raw/sap_successfactors/PerPerson"
CONTEXT = {
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
    "security_context": refinement_main._sign_security_context(CONTEXT),
    "_verified_internal_service": "console",
}
USER_CONTEXT = {"tenant_id": TENANT, "workspace_id": WORKSPACE, "role": "analyst"}
FILTERS = (
    "WHERE \"load_date\" = '{latest_date}' AND \"salario\" >= TRY_CAST(? AS HUGEINT) "
    "AND contains(strip_accents(lower(CAST(\"nombre\" AS VARCHAR))), strip_accents(lower(?))) "
    "ORDER BY \"nombre\" ASC NULLS LAST"
)


def _execute_sql(workspace: str = WORKSPACE) -> str:
    path = f"s3://lakehouse/{SOURCE}/tenant_id={TENANT}/workspace_id={workspace}/**/*.parquet"
    return (
        f"SELECT \"nombre\", \"salario\" FROM read_parquet('{path}', hive_partitioning=true, "
        f"union_by_name=true) {FILTERS} LIMIT 50"
    )


def _definition_sql(source: str = SOURCE) -> str:
    return (
        f"SELECT \"nombre\", \"salario\" FROM read_parquet('s3://{{bucket}}/{source}/**/*.parquet', "
        "hive_partitioning=true, union_by_name=true) WHERE \"load_date\" = '{latest_date}' "
        "AND \"salario\" >= 1500.5 AND CAST(\"nombre\" AS VARCHAR) = 'O''Reilly'"
    )


def _engine() -> DuckDBEngine:
    engine = object.__new__(DuckDBEngine)
    engine.minio_bucket = "lakehouse"
    engine.storage = SimpleNamespace(config=SimpleNamespace(provider="s3"))
    return engine


def _engine_effective_sql(engine: DuckDBEngine, sql: str, sources: list[str]) -> str:
    engine._validate_safe_sql(sql)
    effective = engine._scope_storage_sql(engine._inject_bucket(sql), sources, USER_CONTEXT)
    engine._validate_scoped_storage_sql(effective, USER_CONTEXT)
    validate_table_function_query(
        effective,
        expected_bucket="lakehouse",
        allow_bucket_placeholder=False,
        allow_server_resolved_path_list=True,
    )
    return effective


@pytest.mark.parametrize("sql", [_execute_sql(), _definition_sql()])
def test_explorer_sql_passes_both_storage_boundaries(sql: str) -> None:
    refinement_main._require_sql_storage_scope(BODY, sql, [SOURCE])
    effective = _engine_effective_sql(_engine(), sql, [SOURCE])
    assert f"{SOURCE}/tenant_id={TENANT}/workspace_id={WORKSPACE}/" in effective
    assert "{bucket}" not in effective


def test_saved_definition_depends_on_its_declared_source() -> None:
    with pytest.raises(ValueError, match="outside the caller tenant/workspace scope"):
        _engine_effective_sql(_engine(), _definition_sql(), ["raw/sap_successfactors/EmpJob"])


def test_execute_sql_for_another_workspace_is_rejected() -> None:
    sql = _execute_sql(workspace="workspace-b")
    with pytest.raises(HTTPException) as exc:
        refinement_main._require_sql_storage_scope(BODY, sql, [SOURCE])
    assert exc.value.status_code == 403
    with pytest.raises(ValueError, match="outside"):
        _engine_effective_sql(_engine(), sql, [SOURCE])
