from __future__ import annotations

import pytest

from tests.conftest import load_cartridge_app


@pytest.mark.parametrize(
    "cartridge,entity",
    [
        ("replicon", "TimeEntry"),
        ("sap_hcm", "EmployeeMaster"),
        ("sap_s4hana", "BusinessPartner"),
        ("sap_successfactors", "User"),
        ("sap_b1", "OINV"),
        ("hubspot", "deals"),
        ("salesforce", "Opportunity"),
    ],
)
def test_raw_parquet_upload_path_uses_forwarded_tenant_workspace_scope(cartridge, entity, monkeypatch):
    load_cartridge_app(cartridge)
    from app.core import request_context
    from app.services import parquet_service

    uploads: list[str] = []
    monkeypatch.setattr(parquet_service, "apply_protection_for_entity", lambda _entity, rows: rows)
    monkeypatch.setattr(
        parquet_service,
        "upload_file_to_minio",
        lambda *, local_path, object_name: uploads.append(object_name),
    )

    kwargs = {}
    if cartridge.startswith("sap_"):
        kwargs["expected_columns"] = ["id"]

    uri = parquet_service.write_parquet_and_upload(
        entity=entity,
        rows=[{"id": "1"}],
        run_id="run-1",
        load_type="full",
        security_context=request_context._sign_security_context(
            {
                "trusted": True,
                "source": "console",
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
            }
        ),
        **kwargs,
    )

    assert uploads
    expected = f"raw/{cartridge}/{entity}/tenant_id=tenant-1/workspace_id=ws-1/load_date="
    assert uploads[0].startswith(expected)
    assert uri.startswith(f"s3://lakehouse/{expected}")
