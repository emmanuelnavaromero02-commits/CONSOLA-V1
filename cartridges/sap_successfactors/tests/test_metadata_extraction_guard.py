from __future__ import annotations

import os
from pathlib import Path


os.environ.setdefault(
    "FIELD_ENCRYPTION_KEY",
    "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=",
)
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")


def _config(**overrides):
    return {
        "entity": "JobRequisition",
        "odata_entity": "JobRequisition",
        "connection_id": "tenant_sf",
        "primary_key": "jobReqId",
        "mode": "incremental",
        "watermark_field": "lastModifiedDateTime",
        "select_fields": [
            "jobReqId",
            "jobTitle",
            "status",
            "lastModifiedDateTime",
        ],
        **overrides,
    }


def test_guard_prunes_select_and_preserves_expected_schema(monkeypatch):
    from app.services import catalog_service, extraction_service

    monkeypatch.setattr(extraction_service, "require_storage_access", lambda: None)

    monkeypatch.setattr(
        catalog_service,
        "_metadata_entities_for_connection",
        lambda **_kwargs: (
            {
                "JobRequisition": {
                    "jobReqId",
                    "status",
                    "lastModifiedDateTime",
                }
            },
            None,
        ),
    )
    captured = {}

    def fake_run(config, **_kwargs):
        captured.update(config)
        return {"entity": config["entity"], "status": "success", "record_count": 1}

    monkeypatch.setattr(extraction_service, "run_entity", fake_run)

    result = extraction_service.run_entity_with_metadata_guard(_config())

    assert result["metadata_verified"] is True
    assert captured["select_fields"] == [
        "jobReqId",
        "status",
        "lastModifiedDateTime",
    ]
    assert captured["expected_select_fields"] == [
        "jobReqId",
        "jobTitle",
        "status",
        "lastModifiedDateTime",
    ]
    assert captured["metadata_status"] == "select_pruned"


def test_guard_does_not_extract_when_metadata_is_unavailable(monkeypatch):
    from app.services import catalog_service, extraction_service

    monkeypatch.setattr(extraction_service, "require_storage_access", lambda: None)

    monkeypatch.setattr(
        catalog_service,
        "_metadata_entities_for_connection",
        lambda **_kwargs: (
            None,
            {
                "status": "blocked",
                "reason": "metadata_unavailable",
                "error": "SECRET-UPSTREAM-DIAGNOSTIC",
            },
        ),
    )
    monkeypatch.setattr(
        extraction_service,
        "run_entity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unguarded extractor must not run")
        ),
    )

    result = extraction_service.run_entity_with_metadata_guard(_config())

    assert result["status"] == "skipped_explicit"
    assert result["code"] == "SUCCESSFACTORS_METADATA_BLOCKED"
    assert result["reason"] == "missing_metadata"
    assert result["metadata_status"] == "metadata_unavailable"
    assert "SECRET" not in repr(result)


def test_guard_does_not_extract_missing_entity_or_primary_key(monkeypatch):
    from app.services import catalog_service, extraction_service

    monkeypatch.setattr(extraction_service, "require_storage_access", lambda: None)

    metadata = {"JobRequisition": {"status", "lastModifiedDateTime"}}
    monkeypatch.setattr(
        catalog_service,
        "_metadata_entities_for_connection",
        lambda **_kwargs: (metadata, None),
    )
    monkeypatch.setattr(
        extraction_service,
        "run_entity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unguarded extractor must not run")
        ),
    )

    missing_primary = extraction_service.run_entity_with_metadata_guard(
        _config(select_fields=["status", "lastModifiedDateTime"])
    )
    missing_entity = extraction_service.run_entity_with_metadata_guard(
        _config(odata_entity="NotExposed")
    )

    assert missing_primary["status"] == "skipped_explicit"
    assert missing_primary["reason"] == "invalid_required_field"
    assert missing_primary["fields_missing"] == ["jobReqId"]
    assert missing_entity["status"] == "skipped_explicit"
    assert missing_entity["reason"] == "entity_not_exposed_in_sap"


def test_guard_reports_missing_connection_before_metadata(monkeypatch):
    from app.services import catalog_service, extraction_service

    monkeypatch.setattr(extraction_service, "require_storage_access", lambda: None)
    monkeypatch.setattr(
        catalog_service,
        "prepare_entity_config_for_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("metadata must not run without a connection")
        ),
    )

    result = extraction_service.run_entity_with_metadata_guard(
        _config(connection_id="", conn_id="")
    )

    assert result["status"] == "skipped_explicit"
    assert result["reason"] == "missing_connection"
    assert result["metadata_status"] == "missing_connection"


def test_extract_all_plan_keeps_metadata_failure_as_explicit_outcome(monkeypatch):
    from app.services import catalog_service

    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: [_config()])
    monkeypatch.setattr(
        catalog_service,
        "_metadata_entities_for_connection",
        lambda **_kwargs: (
            None,
            {
                "entity": "__metadata__",
                "status": "blocked",
                "reason": "metadata_unavailable",
                "error": "SECRET-UPSTREAM-DIAGNOSTIC",
            },
        ),
    )

    entities, outcomes = catalog_service.get_extract_all_plan(conn_id="tenant_sf")

    assert entities == []
    assert len(outcomes) == 1
    assert outcomes[0]["entity"] == "JobRequisition"
    assert outcomes[0]["status"] == "skipped_explicit"
    assert outcomes[0]["metadata_status"] == "metadata_unavailable"
    assert "SECRET" not in repr(outcomes)


def test_extract_all_plan_marks_missing_entity_and_required_fields_as_explicit_skips(
    monkeypatch,
):
    from app.services import catalog_service

    configs = [
        _config(entity="MissingEntity", odata_entity="MissingEntity"),
        _config(entity="MissingPrimary", odata_entity="MissingPrimary"),
    ]
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: configs)
    monkeypatch.setattr(
        catalog_service,
        "_metadata_entities_for_connection",
        lambda **_kwargs: (
            {
                "MissingPrimary": {"status", "lastModifiedDateTime"},
            },
            None,
        ),
    )

    entities, outcomes = catalog_service.get_extract_all_plan(conn_id="tenant_sf")

    assert entities == []
    assert {item["entity"]: item["status"] for item in outcomes} == {
        "MissingEntity": "skipped_explicit",
        "MissingPrimary": "skipped_explicit",
    }
    assert {item["entity"]: item["reason"] for item in outcomes} == {
        "MissingEntity": "entity_not_exposed_in_sap",
        "MissingPrimary": "invalid_required_field",
    }
    assert all(item["code"] == "SUCCESSFACTORS_METADATA_BLOCKED" for item in outcomes)


def test_talent_catalog_preflight_never_serializes_raw_exception(monkeypatch):
    from app.services import preflight
    from app.services import catalog_service

    monkeypatch.setattr(
        preflight,
        "talent_metadata_readiness",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError(
                "HTTP 400 https://tenant.example/odata/v2/$metadata?token=SENTINEL "
                "body=SENTINEL-BODY"
            )
        ),
    )

    _, outcomes, _, _ = catalog_service._talent_extract_target_entities(
        conn_id="tenant_sf",
        security_context={"tenant_id": "t1", "workspace_id": "w1"},
    )

    assert outcomes == [
        {
            "entity": "__talent_metadata__",
            "status": "blocked",
            "reason": "missing_metadata",
            "failure_code": "metadata_query_invalid",
        }
    ]
    assert "SENTINEL" not in repr(outcomes)


def test_every_successfactors_execution_surface_uses_common_guard():
    root = Path(__file__).resolve().parents[1]
    surfaces = (
        root / "app" / "mcp_server.py",
        root / "app" / "api" / "routes_console.py",
        root / "app" / "api" / "routes_skills.py",
        root / "app" / "core" / "job_runner.py",
        root / "dags" / "sap_successfactors_extract.py",
        root / "dags" / "sap_successfactors_extract_all.py",
    )

    for path in surfaces:
        source = path.read_text(encoding="utf-8")
        assert "run_entity_with_metadata_guard" in source, path
