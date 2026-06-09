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
    def get_dataset(self, name: str) -> dict | None:
        if name != "sap_successfactors_empemployment_latest":
            return None
        return {
            "name": name,
            "layer": "silver",
            "cartridge": "sap_successfactors",
            "workspace_id": "workspace-a",
        }


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
