from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("FIELD_ENCRYPTION_KEY", "ZVi4nlltq1NSkJjp17QoaHhaRB2RDQRsNTW7I4yf8GE=")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-12345")


def _rows() -> list[dict]:
    return [
        {
            "entity": "PerPerson",
            "connection_id": "femsa_sf",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "watermark_field": "lastModifiedDateTime",
        },
        {
            "entity": "FOCompany",
            "connection_id": "femsa_sf",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        },
        {
            "entity": "Position",
            "connection_id": None,
            "tenant_id": None,
            "workspace_id": None,
        },
        {
            "entity": "Candidate",
            "connection_id": "other_conn",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        },
        {
            "entity": "EmpJob",
            "connection_id": "femsa_sf",
            "tenant_id": "tenant-b",
            "workspace_id": "workspace-a",
        },
        {
            "entity": "EmpEmploymentTermination",
            "connection_id": "femsa_sf",
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


def test_extract_all_plan_keeps_only_scoped_connection_and_reports_skips(monkeypatch):
    from app.services import catalog_service

    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: _rows())

    entities, skipped = catalog_service.get_extract_all_plan(
        conn_id="femsa_sf",
        security_context=_ctx(),
    )

    assert [row["entity"] for row in entities] == ["PerPerson", "FOCompany", "EmpEmploymentTermination"]
    assert {(row["entity"], row["reason"]) for row in skipped} == {
        ("Position", "not_scoped_for_connection"),
        ("Candidate", "not_scoped_for_connection"),
        ("EmpJob", "scope_mismatch"),
    }


def test_extract_all_external_scope_block_can_be_overridden(monkeypatch):
    from app.services import catalog_service

    rows = [
        {
            "entity": "Candidate",
            "connection_id": "femsa_sf",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        }
    ]
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)
    monkeypatch.setenv("SAP_SUCCESSFACTORS_EXTRACT_ALL_EXCLUDE_ENTITIES", "")

    entities, skipped = catalog_service.get_extract_all_plan(
        conn_id="femsa_sf",
        security_context=_ctx(),
    )

    assert [row["entity"] for row in entities] == ["Candidate"]
    assert ("Candidate", "external_scope_blocked") not in {
        (row["entity"], row["reason"]) for row in skipped
    }


def test_extract_all_default_skips_known_unavailable_successfactors_entities(monkeypatch):
    from app.services import catalog_service

    rows = [
        {
            "entity": entity,
            "connection_id": "femsa_sf",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        }
        for entity in ("PerPerson", "Candidate", "GoalPlan", "LearningItem")
    ]
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)

    entities, skipped = catalog_service.get_extract_all_plan(
        conn_id="femsa_sf",
        security_context=_ctx(),
    )

    assert [row["entity"] for row in entities] == ["PerPerson"]
    assert {(row["entity"], row["reason"]) for row in skipped} == {
        ("Candidate", "external_scope_blocked"),
        ("GoalPlan", "external_scope_blocked"),
        ("LearningItem", "external_scope_blocked"),
    }


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
        "status": "skipped",
        "reason": "metadata_entity_missing",
        "code": "SUCCESSFACTORS_METADATA_BLOCKED",
    }


def test_extract_all_plan_talent_target_uses_live_metadata_targets(monkeypatch):
    from app.services import catalog_service, preflight

    rows = [
        *_rows(),
        {
            "entity": "PerformanceReview",
            "connection_id": "femsa_sf",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        },
        {
            "entity": "GoalPlan",
            "connection_id": "femsa_sf",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
        },
    ]
    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: rows)
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
        conn_id="femsa_sf",
        security_context=_ctx(),
        target="talent",
    )

    assert [row["entity"] for row in entities] == ["PerformanceReview"]
    assert skipped == [
        {
            "entity": "CompetencyEntity",
            "status": "skipped",
            "reason": "not_configured_for_extraction",
        }
    ]


def test_extract_all_plan_talent_target_reports_metadata_blocker(monkeypatch):
    from app.services import catalog_service, preflight

    monkeypatch.setattr(catalog_service, "get_all_entities", lambda: _rows())
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
        conn_id="femsa_sf",
        security_context=_ctx(),
        target="talent",
    )

    assert entities == []
    assert skipped == [
        {
            "entity": "__talent_cpa__",
            "status": "skipped",
            "reason": "talent_metadata_not_ready",
            "blockers": [{"component": "metadata", "reason": "metadata_unavailable"}],
        }
    ]


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
        conn_id="femsa_sf",
        idempotency_key="sync_now:sap_successfactors:test",
        body={"security_context": _ctx()},
    )

    assert captured == ["PerPerson"]
    assert captured_plan_kwargs["conn_id"] == "femsa_sf"
    assert captured_plan_kwargs["target"] == "talent"
    assert response["status"] == "success"
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
        conn_id="femsa_sf",
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
        conn_id="femsa_sf",
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
        conn_id="femsa_sf",
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
        conn_id="femsa_sf",
        body={"security_context": _ctx()},
    )

    assert captured == [("PerPerson", "femsa_sf")]
    assert response["skipped"] == [
        {"entity": "Candidate", "status": "skipped", "reason": "not_scoped_for_connection"}
    ]


def test_async_extract_all_job_is_serial_for_scoped_connection_and_preserves_skips(monkeypatch):
    from app.core import job_runner
    from app.services import catalog_service, extraction_service

    updates: list[dict] = []
    logs: list[dict] = []
    captured_plan_kwargs: dict = {}

    monkeypatch.delenv("SAP_SUCCESSFACTORS_EXTRACT_ALL_CONCURRENCY", raising=False)
    def fake_plan(**kwargs):
        captured_plan_kwargs.update(kwargs)
        return (
            [{"entity": "PerPerson"}],
            [{"entity": "Position", "status": "skipped", "reason": "not_scoped_for_connection"}],
        )

    monkeypatch.setattr(catalog_service, "get_extract_all_plan", fake_plan)
    monkeypatch.setattr(
        extraction_service,
        "run_entity",
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

    asyncio.run(job_runner._run_extract_all("job-1", "incremental", _ctx(), "femsa_sf", "talent"))

    final = updates[-1]
    assert captured_plan_kwargs["target"] == "talent"
    assert final["status"] == "done"
    assert final["result"]["target"] == "talent"
    assert final["result"]["selected"] == 1
    assert final["result"]["concurrency"] == 1
    assert final["result"]["summary"]["extracted"] == 1
    assert final["result"]["skipped"] == [
        {"entity": "Position", "status": "skipped", "reason": "not_scoped_for_connection"}
    ]
    assert json.dumps(logs, ensure_ascii=True).find("not_scoped_for_connection") != -1
