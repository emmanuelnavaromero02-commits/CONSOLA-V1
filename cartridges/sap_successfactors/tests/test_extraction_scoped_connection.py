from __future__ import annotations

import json
import os

os.environ.setdefault("FIELD_ENCRYPTION_KEY", "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")

from app.api import routes_console
from app.core import minio_client
from app.services import parquet_service
from app.services import preflight
from app.services import extraction_service


def _signed_ctx() -> dict:
    from app.core import request_context

    return request_context._sign_security_context(
        {
            "trusted": True,
            "source": "console",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        }
    )


def test_console_extract_route_preserves_conn_id_and_scope(monkeypatch):
    captured: dict = {}
    ctx = _signed_ctx()

    monkeypatch.setattr(routes_console, "_get_entity_or_404", lambda _entity: {"entity": "PerPerson"})
    def fake_preflight_for_extract(*, conn_id=None, security_context=None):
        captured["preflight_conn_id"] = conn_id
        captured["preflight_security_context"] = security_context
        return None

    monkeypatch.setattr(routes_console, "preflight_for_extract", fake_preflight_for_extract)
    monkeypatch.setattr(routes_console, "_mark_external_job", lambda *_args, **_kwargs: None)

    def fake_run_entity(config, **_kwargs):
        captured.update(config)
        return {"status": "success", "record_count": 1}

    monkeypatch.setattr(routes_console, "run_entity", fake_run_entity)

    result = routes_console.entity_extract(
        "PerPerson",
        mode="incremental",
        conn_id="femsa_sf",
        body={"security_context": ctx},
    )

    assert result["status"] == "success"
    assert captured["preflight_conn_id"] == "femsa_sf"
    assert captured["preflight_security_context"] == ctx
    assert captured["conn_id"] == "femsa_sf"
    assert captured["security_context"] == ctx


def test_extraction_service_passes_conn_id_and_scope_to_sap_client(monkeypatch):
    captured: dict = {}
    ctx = {
        "trusted": True,
        "source": "console",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
    }

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            captured["conn_id"] = conn_id
            captured["security_context"] = security_context

        def fetch_entity(self, **_kwargs):
            return []

    monkeypatch.setattr(extraction_service, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(extraction_service, "create_run", lambda **_kwargs: "run-1")
    monkeypatch.setattr(extraction_service, "finish_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "fail_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "write_parquet_and_upload", lambda **_kwargs: "s3://bronze/path")
    monkeypatch.setattr(extraction_service, "get_watermark", lambda _entity: None)
    monkeypatch.setattr(extraction_service, "update_watermark", lambda **_kwargs: None)

    result = extraction_service.run_entity({
        "entity": "PerPerson",
        "conn_id": "femsa_sf",
        "security_context": ctx,
        "mode": "full",
    })

    assert result["status"] == "success"
    assert captured["conn_id"] == "femsa_sf"
    assert json.loads(captured["security_context"]) == ctx


def test_extraction_service_uses_idempotency_key_as_run_id(monkeypatch):
    captured: dict = {}

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            return None

        def fetch_entity(self, **_kwargs):
            return []

    def fake_create_run(**kwargs):
        captured.update(kwargs)
        return kwargs["requested_run_id"]

    monkeypatch.setattr(extraction_service, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(extraction_service, "create_run", fake_create_run)
    monkeypatch.setattr(extraction_service, "finish_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "fail_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "write_parquet_and_upload", lambda **_kwargs: "s3://bronze/path")
    monkeypatch.setattr(extraction_service, "get_watermark", lambda _entity: None)
    monkeypatch.setattr(extraction_service, "update_watermark", lambda **_kwargs: None)

    result = extraction_service.run_entity({
        "entity": "PerPerson",
        "mode": "full",
        "idempotency_key": "sync_now:sap_successfactors:test:PerPerson",
        "parent_idempotency_key": "sync_now:sap_successfactors:test",
    })

    assert captured["requested_run_id"] == "sync_now:sap_successfactors:test:PerPerson"
    assert result["run_id"] == "sync_now:sap_successfactors:test:PerPerson"
    assert result["idempotency_key"] == "sync_now:sap_successfactors:test:PerPerson"
    assert result["parent_idempotency_key"] == "sync_now:sap_successfactors:test"


def test_effective_dated_entity_sends_odata_from_to_date(monkeypatch):
    captured: dict = {}

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            captured["conn_id"] = conn_id
            captured["security_context"] = security_context

        def fetch_entity(self, **kwargs):
            captured["fetch_kwargs"] = kwargs
            return []

    monkeypatch.setattr(extraction_service, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(extraction_service, "create_run", lambda **_kwargs: "run-empjob")
    monkeypatch.setattr(extraction_service, "finish_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "fail_run", lambda **_kwargs: None)
    monkeypatch.setattr(extraction_service, "write_parquet_and_upload", lambda **_kwargs: "s3://bronze/empjob")
    monkeypatch.setattr(extraction_service, "get_watermark", lambda _entity: None)
    monkeypatch.setattr(extraction_service, "update_watermark", lambda **_kwargs: None)

    result = extraction_service.run_entity({
        "entity": "EmpJob",
        "mode": "incremental",
        "effective_dated": True,
        "date_field": "startDate",
        "conn_id": "femsa_sf",
    })

    assert result["status"] == "success"
    assert captured["fetch_kwargs"]["entity"] == "EmpJob"
    assert captured["fetch_kwargs"]["from_date"] == "1900-01-01"
    assert captured["fetch_kwargs"]["to_date"] == "9999-12-31"


def test_preflight_uses_scoped_vault_connection(monkeypatch):
    captured: dict = {}
    ctx = {
        "trusted": True,
        "source": "console",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
    }

    class FakeSapSfClient:
        def __init__(self, conn_id=None, security_context=None):
            captured["conn_id"] = conn_id
            captured["security_context"] = security_context

        def configuration_status(self):
            return {
                "cartridge": "sap_successfactors",
                "configured": True,
                "missing": [],
                "base_url": "https://api68sales.successfactors.com/odata/v2",
            }

    monkeypatch.setattr(preflight, "SapSfClient", FakeSapSfClient)
    monkeypatch.setattr(preflight.settings, "database_url", "postgresql://example")
    monkeypatch.setattr(preflight.settings, "minio_endpoint", "s3.us-east-1.amazonaws.com")
    monkeypatch.setattr(preflight.settings, "minio_bucket", "lakehouse")
    monkeypatch.setattr(preflight.settings, "minio_access_key", "")
    monkeypatch.setattr(preflight.settings, "minio_secret_key", "")

    report = preflight.preflight_for_extract(conn_id="femsa_sf", security_context=ctx)

    assert report is None
    assert captured["conn_id"] == "femsa_sf"
    assert json.loads(captured["security_context"]) == ctx


def test_preflight_allows_aws_s3_iam_role_without_static_minio_keys(monkeypatch):
    monkeypatch.setattr(preflight.settings, "minio_endpoint", "s3.us-east-1.amazonaws.com")
    monkeypatch.setattr(preflight.settings, "minio_bucket", "modecissions-lakehouse")
    monkeypatch.setattr(preflight.settings, "minio_access_key", "")
    monkeypatch.setattr(preflight.settings, "minio_secret_key", "")

    status = preflight.check_minio()

    assert status == {"component": "minio", "configured": True, "missing": []}


def test_minio_client_uses_iam_provider_for_aws_s3_without_static_keys(monkeypatch):
    captured: dict = {}

    def fake_minio(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(minio_client, "Minio", fake_minio)
    monkeypatch.setattr(minio_client.settings, "minio_endpoint", "s3.us-east-1.amazonaws.com")
    monkeypatch.setattr(minio_client.settings, "minio_access_key", "")
    monkeypatch.setattr(minio_client.settings, "minio_secret_key", "")
    monkeypatch.setattr(minio_client.settings, "minio_secure", True)

    minio_client.get_minio_client()

    assert captured["endpoint"] == "s3.us-east-1.amazonaws.com"
    assert isinstance(captured["credentials"], minio_client.Ec2ImdsV2Provider)
    assert captured["secure"] is True
    assert "access_key" not in captured
    assert "secret_key" not in captured


def test_parquet_storage_uri_uses_configured_bucket(monkeypatch):
    uploaded: dict = {}

    def fake_upload_file_to_minio(local_path, object_name):
        uploaded["local_path"] = local_path
        uploaded["object_name"] = object_name

    monkeypatch.setattr(parquet_service.settings, "minio_bucket", "modecissions-lakehouse-783792")
    monkeypatch.setattr(parquet_service, "upload_file_to_minio", fake_upload_file_to_minio)

    uri = parquet_service.write_parquet_and_upload(
        entity="PerPerson",
        rows=[],
        run_id="run-1",
        load_type="full",
        expected_columns=["personId"],
        security_context={"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
    )

    assert uri.startswith("s3://modecissions-lakehouse-783792/")
    assert uploaded["object_name"] in uri
