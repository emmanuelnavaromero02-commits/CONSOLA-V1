from __future__ import annotations

import json
import os

os.environ.setdefault("FIELD_ENCRYPTION_KEY", "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=")

from app.api import routes_console
from app.core import minio_client
from app.services import preflight
from app.services import extraction_service


def test_console_extract_route_preserves_conn_id_and_scope(monkeypatch):
    captured: dict = {}
    ctx = {
        "trusted": True,
        "source": "console",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
    }

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
