from __future__ import annotations

import pytest
from fastapi import HTTPException

from refinement.app import main as refinement_main


def _security_context() -> dict:
    return {
        "trusted": True,
        "source": "console",
        "role": "user",
        "workspace_role": "workspace_admin",
        "permissions": ["datasets.read"],
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "allowed_buckets": ["lakehouse"],
        "allowed_cartridges": ["sap_successfactors"],
        "allowed_prefixes": [
            "raw/sap_successfactors/",
            "silver/*/tenant_id=tenant-a/workspace_id=workspace-a/",
            "gold/*/tenant_id=tenant-a/workspace_id=workspace-a/",
        ],
    }


def _body() -> dict:
    return {
        "security_context": _security_context(),
    }


class _FakeStore:
    def get_dataset(
        self,
        name: str,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict | None:
        datasets = {
            "sap_successfactors_empemployment_latest": {
                "name": name,
                "layer": "silver",
                "cartridge": "sap_successfactors",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
            },
            "sap_successfactors_employee_360": {
                "name": name,
                "layer": "gold",
                "cartridge": "sap_successfactors",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "sql_def": "SELECT * FROM read_parquet('s3://lakehouse/silver/sap_successfactors/sap_successfactors_empemployment_latest/**/*.parquet')",
                "sources": ["silver/sap_successfactors/sap_successfactors_empemployment_latest"],
            },
        }
        dataset = datasets.get(name)
        if not dataset:
            return None
        if tenant_id is not None and tenant_id != dataset["tenant_id"]:
            return None
        if workspace_id is not None and workspace_id != dataset["workspace_id"]:
            return None
        return dataset


def test_registered_legacy_silver_glob_allowed_when_declared(monkeypatch):
    monkeypatch.setattr(refinement_main, "store", _FakeStore())

    refinement_main._require_sql_path_scope(
        _security_context(),
        "s3://lakehouse/silver/sap_successfactors/sap_successfactors_empemployment_latest/**/*.parquet",
        sources=["silver/sap_successfactors/sap_successfactors_empemployment_latest"],
        allow_registered_dataset_paths=True,
    )


def test_registered_legacy_silver_glob_requires_declared_source(monkeypatch):
    monkeypatch.setattr(refinement_main, "store", _FakeStore())

    with pytest.raises(HTTPException) as exc:
        refinement_main._require_sql_path_scope(
            _security_context(),
            "s3://lakehouse/silver/sap_successfactors/sap_successfactors_empemployment_latest/**/*.parquet",
            sources=[],
            allow_registered_dataset_paths=True,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "SQL storage path not allowed"


def test_registered_dataset_path_still_rejects_foreign_scope(monkeypatch):
    monkeypatch.setattr(refinement_main, "store", _FakeStore())

    with pytest.raises(HTTPException) as exc:
        refinement_main._require_sql_path_scope(
            _security_context(),
            (
                "s3://lakehouse/silver/sap_successfactors/sap_successfactors_empemployment_latest/"
                "tenant_id=tenant-b/workspace_id=workspace-b/data.parquet"
            ),
            sources=["silver/sap_successfactors/sap_successfactors_empemployment_latest"],
            allow_registered_dataset_paths=True,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "SQL storage path not allowed"


def test_portable_raw_reader_is_scoped_before_storage_validation(monkeypatch):
    def fake_scope_sql(sql: str, sources: list[str], user_context: dict) -> str:
        assert sources == ["raw/sap_successfactors/EmpEmployment"]
        assert user_context["tenant_id"] == "tenant-a"
        assert user_context["workspace_id"] == "workspace-a"
        return (
            "select * from read_parquet("
            "'s3://lakehouse/raw/sap_successfactors/EmpEmployment/"
            "tenant_id=tenant-a/workspace_id=workspace-a/**/*.parquet'"
            ")"
        )

    monkeypatch.setattr(refinement_main.engine, "_inject_bucket", lambda sql: sql)
    monkeypatch.setattr(refinement_main.engine, "_scope_storage_sql", fake_scope_sql)
    monkeypatch.setattr(refinement_main, "_security_context", lambda _body: _security_context())

    refinement_main._require_sql_storage_scope(
        _body(),
        "select * from read_parquet('raw/sap_successfactors/EmpEmployment')",
        ["raw/sap_successfactors/EmpEmployment"],
    )


def test_infers_bronze_sources_from_packaged_s3_reader():
    sql = (
        "select * from read_parquet("
        "'s3://{bucket}/raw/sap_successfactors/Candidate/**/*.parquet', "
        "hive_partitioning=true, union_by_name=true)"
    )

    assert refinement_main._infer_bronze_sources_from_sql(sql) == [
        "raw/sap_successfactors/Candidate"
    ]


def _signed_body(tool: str, args: dict) -> dict:
    return {
        "tool": tool,
        "args": args,
        "security_context": refinement_main._sign_security_context(_security_context()),
    }


@pytest.mark.asyncio
async def test_mcp_preview_transform_infers_sources_from_packaged_silver_sql(monkeypatch):
    captured: dict = {}

    def fake_preview_sql(sql, limit=20, sources=None, user_context=None, params=None):
        captured["sql"] = sql
        captured["limit"] = limit
        captured["sources"] = sources
        captured["user_context"] = user_context
        captured["params"] = params
        return {"schema": [], "data": []}

    monkeypatch.setattr(refinement_main.engine, "preview_sql", fake_preview_sql)
    sql = (
        "select * from read_parquet("
        "'s3://{bucket}/raw/sap_successfactors/Candidate/**/*.parquet', "
        "hive_partitioning=true, union_by_name=true)"
    )

    result = await refinement_main.mcp_invoke(
        _signed_body(
            "preview_transform",
            {"sql": sql, "limit": 50, "sources": []},
        ),
        internal_service="console",
    )

    assert result == {"schema": [], "data": []}
    assert captured["sources"] == ["raw/sap_successfactors/Candidate"]
    assert captured["user_context"]["tenant_id"] == "tenant-a"
    assert captured["user_context"]["workspace_id"] == "workspace-a"


@pytest.mark.asyncio
async def test_mcp_get_schema_passes_trusted_user_context(monkeypatch):
    captured: dict = {}

    def fake_get_dataset_schema(ds: dict, user_context: dict | None = None) -> dict:
        captured["dataset"] = ds["name"]
        captured["user_context"] = user_context
        return {"name": ds["name"], "fields": [{"name": "user_id", "type": "VARCHAR"}]}

    monkeypatch.setattr(refinement_main, "store", _FakeStore())
    monkeypatch.setattr(refinement_main.engine, "get_dataset_schema", fake_get_dataset_schema)

    result = await refinement_main.mcp_invoke(
        _signed_body("get_schema", {"name": "sap_successfactors_employee_360"}),
        internal_service="console",
    )

    assert result["fields"] == [{"name": "user_id", "type": "VARCHAR"}]
    assert captured["dataset"] == "sap_successfactors_employee_360"
    assert captured["user_context"]["tenant_id"] == "tenant-a"
    assert captured["user_context"]["workspace_id"] == "workspace-a"
    assert captured["user_context"]["_server_trusted_context"] is True


def test_get_dataset_schema_scopes_registered_silver_glob_to_snapshot(monkeypatch):
    engine = type(refinement_main.engine)()
    latest = (
        "s3://lakehouse/silver/sap_successfactors/sap_successfactors_empemployment_latest/"
        "tenant_id=tenant-a/workspace_id=workspace-a/_snapshots/20260611.parquet"
    )
    captured: dict = {}

    class FakeConn:
        def execute(self, sql: str, params=None):
            captured["sql"] = sql
            captured["params"] = params
            return self

        def fetchall(self):
            return [("user_id", "VARCHAR")]

    monkeypatch.setattr(engine, "_latest_materialized_uri", lambda *_args, **_kwargs: latest)
    monkeypatch.setattr(engine, "get_rls_filters", lambda sql, _ctx: (sql, []))
    monkeypatch.setattr(engine, "_conn", lambda: FakeConn())

    result = engine.get_dataset_schema(
        _FakeStore().get_dataset("sap_successfactors_employee_360"),
        {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
    )

    assert result == {"name": "sap_successfactors_employee_360", "fields": [{"name": "user_id", "type": "VARCHAR"}]}
    assert latest in captured["sql"]
    assert "sap_successfactors_empemployment_latest/**/*.parquet" not in captured["sql"]
    assert "tenant_id=tenant-a/workspace_id=workspace-a" in captured["sql"]


def test_get_dataset_schema_missing_materialization_returns_contextual_error(monkeypatch):
    engine = type(refinement_main.engine)()

    class FakeConn:
        def execute(self, sql: str, params=None):
            raise RuntimeError(f"No files found that match the pattern in {sql}")

    monkeypatch.setattr(engine, "_latest_materialized_uri", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "get_rls_filters", lambda sql, _ctx: (sql, []))
    monkeypatch.setattr(engine, "_conn", lambda: FakeConn())

    result = engine.get_dataset_schema(
        _FakeStore().get_dataset("sap_successfactors_employee_360"),
        {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
    )

    assert result["name"] == "sap_successfactors_employee_360"
    assert "No files found" in result["error"]
    assert "tenant_id=tenant-a/workspace_id=workspace-a" in result["error"]
    assert "sap_successfactors_empemployment_latest/**/*.parquet" not in result["error"]
    assert "fields" not in result
    assert "row_count" not in result
