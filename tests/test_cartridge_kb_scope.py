from __future__ import annotations

import pandas as pd
import pytest

from tests.conftest import load_cartridge_app


SCOPED_KB_CASES = [
    ("replicon", "TimeEntry"),
    ("hubspot", "deals"),
    ("sap_hcm", "EmployeeMaster"),
    ("sap_s4hana", "BusinessPartner"),
    ("sap_successfactors", "User"),
    ("salesforce", "Opportunity"),
]


def _ctx() -> dict:
    return {"trusted": True, "tenant_id": "tenant-1", "workspace_id": "ws-1"}


@pytest.mark.parametrize("cartridge,entity", SCOPED_KB_CASES)
def test_kb_sql_paths_are_scoped_when_context_is_forwarded(cartridge, entity):
    load_cartridge_app(cartridge)
    from app.services import kb_service

    sql = (
        "SELECT * FROM read_parquet("
        f"'s3://{{bucket}}/raw/{cartridge}/{entity}/**/*.parquet', "
        "hive_partitioning=true)"
    )

    scoped = kb_service._scope_kb_sql(sql, _ctx())
    legacy = kb_service._scope_kb_sql(sql, None)

    assert f"raw/{cartridge}/{entity}/tenant_id=tenant-1/workspace_id=ws-1/" in scoped
    assert "{bucket}" not in scoped
    assert "tenant_id=" not in legacy
    assert f"raw/{cartridge}/{entity}/**/*.parquet" in legacy


@pytest.mark.parametrize("cartridge,entity", SCOPED_KB_CASES)
def test_kb_output_parquet_path_uses_forwarded_tenant_workspace_scope(
    cartridge,
    entity,
    monkeypatch,
):
    load_cartridge_app(cartridge)
    from app.services import duckdb_service

    uploads: list[str] = []
    monkeypatch.setattr(
        duckdb_service,
        "upload_file_to_minio",
        lambda *, local_path, object_name: uploads.append(object_name),
    )

    uri = duckdb_service.write_kb_parquet(
        pd.DataFrame([{"id": "1", "entity": entity}]),
        output_path=f"silver/{cartridge}/kb_test",
        kb_id="kb_test",
        run_id="run-1",
        security_context=_ctx(),
    )

    expected = (
        f"silver/{cartridge}/kb_test/tenant_id=tenant-1/workspace_id=ws-1/load_date="
    )
    assert uploads
    assert uploads[0].startswith(expected)
    assert uri.startswith(f"s3://lakehouse/{expected}")
