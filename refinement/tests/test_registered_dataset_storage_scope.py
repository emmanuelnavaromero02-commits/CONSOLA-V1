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
            "silver/*/tenant_id=tenant-a/workspace_id=workspace-a/",
            "gold/*/tenant_id=tenant-a/workspace_id=workspace-a/",
        ],
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
