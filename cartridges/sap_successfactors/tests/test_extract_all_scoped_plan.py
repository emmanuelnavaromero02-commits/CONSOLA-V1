from __future__ import annotations

import asyncio
import json
import os

import pytest

os.environ.setdefault("FIELD_ENCRYPTION_KEY", "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")


def _rows() -> list[dict]:
    return [
        {
            "entity": "PerPerson",
            "odata_entity": "PerPerson",
            "connection_id": "tenant_sf",
            "primary_key": "personIdExternal",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "watermark_field": "lastModifiedDateTime",
        },
        {
            "entity": "FOCompany",
            "odata_entity": "FOCompany",
            "connection_id": "tenant_sf",
            "primary_key": "externalCode",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        },
        {
            "entity": "Position",
            "odata_entity": "Position",
            "connection_id": None,
            "primary_key": "code",
            "tenant_id": None,
            "workspace_id": None,
        },
        {
            "entity": "Candidate",
            "odata_entity": "Candidate",
            "connection_id": "other_conn",
            "primary_key": "candidateId",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        },
        {
            "entity": "EmpJob",
            "odata_entity": "EmpJob",
            "connection_id": "tenant_sf",
            "primary_key": "userId",
            "tenant_id": "tenant-b",
            "workspace_id": "workspace-a",
        },
        {
            "entity": "EmpEmploymentTermination",
            "odata_entity": "EmpEmploymentTermination",
            "connection_id": "tenant_sf",
            "primary_key": "userId",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        },
    ]


def _ctx() -> dict:
    from app.core import request_context

    return request_context._sign_security_context(
        {
            "trusted": True,
            "source": "console",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        }
    )


def _allow_live_metadata(monkeypatch, catalog_service, rows: list[dict]) -> None:
    metadata = {}
    for row in rows:
        entity = str(row.get("odata_entity") or row.get("entity") or "")
        fields = {
            str(value)
            for value in (
                row.get("primary_key"),
                row.get("watermark_field"),
                row.get("date_field"),
            )
            if value
        }
        fields.update(
            str(value) for value in (row.get("select_fields") or []) if value
        )
        metadata[entity] = fields
    monkeypatch.setattr(
        catalog_service,
        "_metadata_entities_for_connection",
        lambda **_kwargs: (metadata, None),
    )


def test_extract_all_plan_reuses_selected_connection_even_with_stale_scope(monkeypatch):
    from app.services import catalog_service

    rows = _rows()
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)
    _allow_live_metadata(monkeypatch, catalog_service, rows)

    entities, skipped = catalog_service.get_extract_all_plan(
        conn_id="tenant_sf",
        security_context=_ctx(),
    )

    assert [row["entity"] for row in entities] == [
        "PerPerson",
        "FOCompany",
        "Position",
        "EmpJob",
        "EmpEmploymentTermination",
    ]
    assert {(row["entity"], row["reason"]) for row in skipped} == {
        ("Candidate", "scope_mismatch"),
    }


def test_extract_all_target_all_includes_former_external_scope_entities(monkeypatch):
    from app.services import catalog_service

    rows = [
        {
            "entity": "Candidate",
            "odata_entity": "Candidate",
            "connection_id": "tenant_sf",
            "primary_key": "candidateId",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        }
    ]
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)
    _allow_live_metadata(monkeypatch, catalog_service, rows)

    entities, skipped = catalog_service.get_extract_all_plan(
        conn_id="tenant_sf",
        security_context=_ctx(),
    )

    assert [row["entity"] for row in entities] == ["Candidate"]
    assert skipped == []


def test_extract_all_plan_treats_cartridge_connection_as_selected_placeholder(monkeypatch):
    from app.services import catalog_service

    rows = [
        {
            "entity": "User",
            "odata_entity": "User",
            "connection_id": "sap_successfactors",
            "primary_key": "userId",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "watermark_field": "lastModifiedDateTime",
        }
    ]
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)
    _allow_live_metadata(monkeypatch, catalog_service, rows)

    entities, skipped = catalog_service.get_extract_all_plan(
        conn_id="tenant_sf",
        security_context=_ctx(),
    )

    assert [row["entity"] for row in entities] == ["User"]
    assert skipped == []
    assert entities[0]["conn_id"] == "tenant_sf"
    assert entities[0]["connection_id"] == "tenant_sf"


def test_extract_all_default_does_not_silently_skip_known_talent_entities(monkeypatch):
    from app.services import catalog_service

    rows = [
        {
            "entity": entity,
            "odata_entity": entity,
            "connection_id": "tenant_sf",
            "primary_key": "id",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        }
        for entity in ("PerPerson", "Candidate", "GoalPlan", "LearningItem")
    ]
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)
    _allow_live_metadata(monkeypatch, catalog_service, rows)

    entities, skipped = catalog_service.get_extract_all_plan(
        conn_id="tenant_sf",
        security_context=_ctx(),
    )

    assert [row["entity"] for row in entities] == ["PerPerson", "Candidate", "GoalPlan", "LearningItem"]
    assert skipped == []


def test_prepare_entity_config_prunes_invalid_select_fields_from_metadata():
    from app.services import catalog_service

    config = {
        "entity": "JobRequisition",
        "odata_entity": "JobRequisition",
        "mode": "incremental",
        "watermark_field": "lastModifiedDateTime",
        "select_fields": ["jobReqId", "jobTitle", "status", "lastModifiedDateTime"],
    }

    prepared, block = catalog_service.prepare_entity_config_for_metadata(
        config,
        metadata_entities={
            "JobRequisition": {"jobReqId", "status", "lastModifiedDateTime"}
        },
    )

    assert block is None
    assert prepared["select_fields"] == ["jobReqId", "status", "lastModifiedDateTime"]
    assert prepared["expected_select_fields"] == [
        "jobReqId",
        "jobTitle",
        "status",
        "lastModifiedDateTime",
    ]
    assert prepared["metadata_status"] == "select_pruned"
    assert prepared["metadata_pruned_fields"] == ["jobTitle"]


def test_prepare_entity_config_skips_missing_odata_entity():
    from app.services import catalog_service

    prepared, block = catalog_service.prepare_entity_config_for_metadata(
        {
            "entity": "CareerInterest",
            "mode": "incremental",
            "select_fields": ["externalCode", "userId"],
        },
        metadata_entities={},
    )

    assert prepared is None
    assert block == {
        "entity": "CareerInterest",
        "odata_entity": "CareerInterest",
        "status": "skipped_explicit",
        "reason": "entity_not_exposed_in_sap",
        "code": "SUCCESSFACTORS_METADATA_BLOCKED",
        "metadata_status": "metadata_entity_missing",
    }


def test_extract_all_plan_talent_target_uses_live_metadata_targets(monkeypatch):
    from app.services import catalog_service, preflight

    rows = [
        *_rows(),
        {
            "entity": "PerformanceReview",
            "odata_entity": "FormHeader",
            "connection_id": "tenant_sf",
            "primary_key": "formDataId",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        },
        {
            "entity": "GoalPlan",
            "odata_entity": "Goal",
            "connection_id": "tenant_sf",
            "primary_key": "id",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        },
    ]
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)
    monkeypatch.setattr(
        catalog_service,
        "_metadata_entities_for_connection",
        lambda **_kwargs: (
            {
                "FormHeader": {"formDataId", "formSubjectId", "overallRating", "lastModifiedDateTime"},
                "Goal": {"id", "userId", "state", "lastModifiedDateTime"},
                "Position": {"code", "lastModifiedDateTime"},
            },
            None,
        ),
    )
    monkeypatch.setattr(
        preflight,
        "talent_metadata_readiness",
        lambda **_kwargs: {
            "status": "partial",
            "extraction_targets": [
                {
                    "entity": "PerformanceReview",
                    "status": "ready_to_extract",
                },
                {
                    "entity": "CompetencyEntity",
                    "status": "metadata_ready",
                },
            ],
            "blockers": [],
        },
    )

    entities, skipped = catalog_service.get_extract_all_plan(
        conn_id="tenant_sf",
        security_context=_ctx(),
        target="talent",
    )

    assert "PerformanceReview" in [row["entity"] for row in entities]
    assert "Position" in [row["entity"] for row in entities]
    assert any(row["entity"] == "Candidate" and row["status"] == "skipped_explicit" for row in skipped)
    assert any(
        row["entity"] == "CompetencyEntity" and row["status"] == "blocked"
        for row in skipped
    )


def test_extract_all_plan_talent_target_applies_live_odata_alias(monkeypatch):
    from app.services import catalog_service, preflight

    rows = [
        {
            "entity": "PerformanceReview",
            "odata_entity": "FormHeader",
            "connection_id": "tenant_sf",
            "primary_key": "formDataId",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "select_fields": ["formDataId", "formSubjectId", "overallRating", "lastModifiedDateTime"],
        }
    ]
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)
    monkeypatch.setattr(
        catalog_service,
        "_metadata_entities_for_connection",
        lambda **_kwargs: (
            {
                "cust_PerformanceTalent": {
                    "externalCode",
                    "worker",
                    "rating",
                    "lastModifiedDateTime",
                },
            },
            None,
        ),
    )
    monkeypatch.setattr(
        preflight,
        "talent_metadata_readiness",
        lambda **_kwargs: {
            "status": "partial",
            "extraction_targets": [
                {
                    "component": "performance",
                    "entity": "PerformanceReview",
                    "odata_entity": "cust_PerformanceTalent",
                    "status": "ready_to_extract",
                    "fields_present": ["externalCode", "worker", "rating", "lastModifiedDateTime"],
                    "primary_key": "externalCode",
                    "watermark_field": "lastModifiedDateTime",
                    "alias_id": "alias-1",
                    "field_aliases": {"formSubjectId": "worker", "overallRating": "rating"},
                },
            ],
            "blockers": [],
        },
    )

    entities, skipped = catalog_service.get_extract_all_plan(
        conn_id="tenant_sf",
        security_context=_ctx(),
        target="talent",
    )

    performance = next(row for row in entities if row["entity"] == "PerformanceReview")
    assert performance["odata_entity"] == "cust_PerformanceTalent"
    assert performance["primary_key"] == "externalCode"
    assert performance["select_fields"] == ["externalCode", "lastModifiedDateTime", "rating", "worker"]
    assert set(performance["expected_select_fields"]) == {
        "externalCode",
        "lastModifiedDateTime",
        "rating",
        "worker",
        "formSubjectId",
        "overallRating",
    }
    assert performance["metadata_alias_id"] == "alias-1"
    assert performance["metadata_field_aliases"]["overallRating"] == "rating"
    assert not any(row.get("entity") == "PerformanceReview" for row in skipped)


def test_extract_all_plan_talent_target_reports_metadata_blocker(monkeypatch):
    from app.services import catalog_service, preflight

    rows = _rows()
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)
    _allow_live_metadata(monkeypatch, catalog_service, rows)
    monkeypatch.setattr(
        preflight,
        "talent_metadata_readiness",
        lambda **_kwargs: {
            "status": "blocked",
            "extraction_targets": [],
            "blockers": [{"component": "metadata", "reason": "metadata_unavailable"}],
        },
    )

    entities, skipped = catalog_service.get_extract_all_plan(
        conn_id="tenant_sf",
        security_context=_ctx(),
        target="talent",
    )

    assert [row["entity"] for row in entities] == ["Position"]
    assert any(
        row["entity"] == "__talent_cpa__"
        and row["status"] == "blocked"
        and row["reason"] == "missing_metadata"
        for row in skipped
    )


def test_console_extract_all_uses_scoped_plan_and_returns_skipped(monkeypatch):
    from app.api import routes_console

    captured: list[str] = []
    captured_plan_kwargs: dict = {}
    monkeypatch.setattr(routes_console, "preflight_for_extract", lambda **_kwargs: None)
    monkeypatch.setattr(routes_console, "_mark_external_job", lambda *_args, **_kwargs: None)
    def fake_plan(**kwargs):
        captured_plan_kwargs.update(kwargs)
        return (
            [{"entity": "PerPerson", "watermark_field": "lastModifiedDateTime"}],
            [{"entity": "Position", "status": "skipped", "reason": "not_scoped_for_connection"}],
        )

    monkeypatch.setattr(routes_console, "get_extract_all_plan", fake_plan)

    def fake_run_entity(config, **_kwargs):
        captured.append(config["entity"])
        return {"entity": config["entity"], "status": "success", "record_count": 7}

    monkeypatch.setattr(routes_console, "run_entity", fake_run_entity)

    response = routes_console.extract_all(
        mode="incremental",
        target="talent",
        conn_id="tenant_sf",
        idempotency_key="sync_now:sap_successfactors:test",
        body={"security_context": _ctx()},
    )

    assert captured == ["PerPerson"]
    assert captured_plan_kwargs["conn_id"] == "tenant_sf"
    assert captured_plan_kwargs["target"] == "talent"
    assert response["status"] == "completed_with_blocks"
    assert response["target"] == "talent"
    assert response["summary"]["extracted"] == 1
    assert response["skipped"] == [
        {"entity": "Position", "status": "skipped", "reason": "not_scoped_for_connection"}
    ]


def test_console_extract_all_passes_entity_idempotency_key(monkeypatch):
    from app.api import routes_console

    captured: list[dict] = []
    monkeypatch.setattr(routes_console, "preflight_for_extract", lambda **_kwargs: None)
    monkeypatch.setattr(routes_console, "_mark_external_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        routes_console,
        "get_extract_all_plan",
        lambda **_kwargs: (
            [{"entity": "PerPerson", "watermark_field": "lastModifiedDateTime"}],
            [],
        ),
    )

    def fake_run_entity(config, **_kwargs):
        captured.append(config)
        return {"entity": config["entity"], "status": "success", "record_count": 7}

    monkeypatch.setattr(routes_console, "run_entity", fake_run_entity)

    routes_console.extract_all(
        mode="incremental",
        conn_id="tenant_sf",
        idempotency_key="sync_now:sap_successfactors:test",
        body={"security_context": _ctx()},
    )

    assert captured[0]["idempotency_key"] == "sync_now:sap_successfactors:test:PerPerson"
    assert captured[0]["parent_idempotency_key"] == "sync_now:sap_successfactors:test"


def test_console_extract_all_triggers_gold_refresh_once_after_entities(monkeypatch):
    from app.api import routes_console

    external_calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(routes_console, "preflight_for_extract", lambda **_kwargs: None)
    monkeypatch.setattr(
        routes_console,
        "get_extract_all_plan",
        lambda **_kwargs: ([{"entity": "PerPerson"}, {"entity": "EmpJob"}], []),
    )

    def fake_mark(func, *args):
        external_calls.append((func.__name__, args))
        if func.__name__ == "_trigger_successfactors_gold_refresh":
            return {"status": "success", "materialized": 19, "total": 19}
        return None

    def fake_run_entity(config, **_kwargs):
        return {"entity": config["entity"], "status": "success", "record_count": 7}

    monkeypatch.setattr(routes_console, "_mark_external_job", fake_mark)
    monkeypatch.setattr(routes_console, "run_entity", fake_run_entity)

    response = routes_console.extract_all(
        mode="incremental",
        target="all",
        conn_id="tenant_sf",
        body={"security_context": _ctx()},
    )

    assert [name for name, _args in external_calls].count("_trigger_silver_refresh") == 2
    assert [name for name, _args in external_calls].count("_trigger_successfactors_gold_refresh") == 1
    assert response["gold_refresh"] == {"status": "success", "materialized": 19, "total": 19}


def test_console_extract_all_classifies_entity_auth_blocks_without_global_502(monkeypatch):
    from app.api import routes_console
    from app.core.sap_client import SAPClientError

    monkeypatch.setattr(routes_console, "preflight_for_extract", lambda **_kwargs: None)
    monkeypatch.setattr(routes_console, "_mark_external_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        routes_console,
        "get_extract_all_plan",
        lambda **_kwargs: (
            [{"entity": "FOCompany"}, {"entity": "EmpJob"}],
            [],
        ),
    )

    def fake_run_entity(config, **_kwargs):
        if config["entity"] == "EmpJob":
            raise SAPClientError(
                "SAML bearer token request failed: 400 Client Error for url: "
                "https://api68sales.successfactors.com/oauth/token"
            )
        return {"entity": config["entity"], "status": "success", "record_count": 7}

    monkeypatch.setattr(routes_console, "run_entity", fake_run_entity)

    response = routes_console.extract_all(
        mode="incremental",
        conn_id="tenant_sf",
        body={"security_context": _ctx()},
    )

    assert not hasattr(response, "status_code")
    assert response["status"] == "completed_with_blocks"
    assert response["summary"]["extracted"] == 1
    assert response["summary"]["auth_blocked"] == 1
    assert [row["status"] for row in response["results"]] == ["extracted", "auth-blocked"]
    assert response["results"][1]["code"] == "AUTH_BLOCKED"


def test_skills_extract_all_routes_use_scoped_plan(monkeypatch):
    from app.api import routes_skills

    captured: list[tuple[str, str | None]] = []
    monkeypatch.setattr(routes_skills, "require_storage_access", lambda: None)
    monkeypatch.setattr(
        routes_skills,
        "get_extract_all_plan",
        lambda **_kwargs: (
            [{"entity": "PerPerson", "watermark_field": "lastModifiedDateTime"}],
            [{"entity": "Candidate", "status": "skipped", "reason": "not_scoped_for_connection"}],
        ),
    )

    def fake_run(config, body, conn_id=None, **_kwargs):
        captured.append((config["entity"], conn_id))
        return {"entity": config["entity"], "status": "success"}

    monkeypatch.setattr(routes_skills, "_run_entity_with_context", fake_run)

    response = routes_skills.run_incremental_all(
        conn_id="tenant_sf",
        body={"security_context": _ctx()},
    )

    assert captured == [("PerPerson", "tenant_sf")]
    assert response["skipped"] == [
        {"entity": "Candidate", "status": "skipped", "reason": "not_scoped_for_connection"}
    ]


def test_async_extract_all_job_is_serial_for_scoped_connection_and_preserves_skips(monkeypatch):
    from app.core import job_runner, minio_client
    from app.services import catalog_service, extraction_service

    updates: list[dict] = []
    logs: list[dict] = []
    captured_plan_kwargs: dict = {}

    monkeypatch.delenv("SAP_SUCCESSFACTORS_EXTRACT_ALL_CONCURRENCY", raising=False)
    monkeypatch.setattr(minio_client, "require_storage_access", lambda: None)
    def fake_plan(**kwargs):
        captured_plan_kwargs.update(kwargs)
        return (
            [{"entity": "PerPerson"}],
            [{"entity": "Position", "status": "skipped", "reason": "not_scoped_for_connection"}],
        )

    monkeypatch.setattr(catalog_service, "get_extract_all_plan", fake_plan)
    monkeypatch.setattr(
        extraction_service,
        "run_entity_with_metadata_guard",
        lambda config: {"entity": config["entity"], "status": "success", "record_count": 5},
    )

    async def fake_update(job_id, status, message="", result=None, error=""):
        updates.append({
            "job_id": job_id,
            "status": status,
            "message": message,
            "result": result,
            "error": error,
        })

    async def fake_log(job_id, entity, level, message, detail=None):
        logs.append({
            "job_id": job_id,
            "entity": entity,
            "level": level,
            "message": message,
            "detail": detail,
        })

    async def fake_refresh(_entity, _security_context=None):
        return None

    monkeypatch.setattr(job_runner, "_update", fake_update)
    monkeypatch.setattr(job_runner, "_log", fake_log)
    monkeypatch.setattr(job_runner, "_trigger_silver_refresh", fake_refresh)

    asyncio.run(job_runner._run_extract_all("job-1", "incremental", _ctx(), "tenant_sf", "talent"))

    final = updates[-1]
    assert captured_plan_kwargs["target"] == "talent"
    assert final["status"] == "done"
    assert final["result"]["target"] == "talent"
    assert final["result"]["selected"] == 1
    assert final["result"]["concurrency"] == 1
    assert final["result"]["summary"]["extracted"] == 1
    assert final["result"]["skipped"] == [
        {"entity": "Position", "status": "skipped_explicit", "reason": "not_scoped_for_connection"}
    ]
    assert json.dumps(logs, ensure_ascii=True).find("not_scoped_for_connection") != -1


def test_async_batch_storage_failure_happens_before_metadata_plan(monkeypatch):
    from app.core import job_runner, minio_client
    from app.services import catalog_service

    plan_called = False

    def unexpected_plan(**_kwargs):
        nonlocal plan_called
        plan_called = True
        raise AssertionError("metadata plan must not run without storage")

    monkeypatch.setattr(catalog_service, "get_extract_all_plan", unexpected_plan)
    monkeypatch.setattr(
        minio_client,
        "require_storage_access",
        lambda: (_ for _ in ()).throw(
            minio_client.StoragePreflightError("storage_signature_invalid")
        ),
    )
    updates: list[dict] = []
    logs: list[dict] = []

    async def fake_update(job_id, status, message=None, result=None, error=None):
        updates.append(
            {
                "job_id": job_id,
                "status": status,
                "message": message,
                "result": result,
                "error": error,
            }
        )

    async def fake_log(job_id, entity, level, message, detail=None):
        logs.append(
            {
                "job_id": job_id,
                "entity": entity,
                "level": level,
                "message": message,
                "detail": detail,
            }
        )

    monkeypatch.setattr(job_runner, "_update", fake_update)
    monkeypatch.setattr(job_runner, "_log", fake_log)

    asyncio.run(
        job_runner._run_extract_all(
            "job-1", "incremental", _ctx(), "tenant_sf", "talent"
        )
    )

    assert plan_called is False
    assert updates[-1]["status"] == "failed"
    assert updates[-1]["error"] == "storage_signature_invalid"
    assert updates[-1]["result"]["entities"] == [
        {
            "entity": "__extract_all__",
            "status": "blocked",
            "code": "storage_signature_invalid",
            "failure_code": "storage_signature_invalid",
        }
    ]
    assert "storage_signature_invalid" in repr(logs)
