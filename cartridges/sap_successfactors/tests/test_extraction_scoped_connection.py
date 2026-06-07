from __future__ import annotations

import json
import os

os.environ.setdefault("FIELD_ENCRYPTION_KEY", "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=")

from app.api import routes_console
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
    monkeypatch.setattr(routes_console, "preflight_for_extract", lambda: None)
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
