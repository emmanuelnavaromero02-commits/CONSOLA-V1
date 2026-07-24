from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from app.routers import control_room as routes
from app.schemas.control_room_surfaces import ExperienceFact
from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
)
from control_room_surface_fixtures import (
    TENANT_ID,
    WORKSPACE_ID,
    business_item,
    snapshot,
    source_state,
)


FORBIDDEN_KEYS = {
    "dataset",
    "source_dataset",
    "metadata",
    "details",
    "readiness",
    "readiness_status",
    "sql",
    "hash",
    "payload_hash",
    "engine",
    "tools",
    "evidence_refs",
    "error",
    "omega",
}


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for nested in value.values() for key in _all_keys(nested)
        }
    if isinstance(value, list):
        return {key for nested in value for key in _all_keys(nested)}
    return set()


def test_experience_without_data_has_no_sections():
    payload = build_business_experience(snapshot())
    assert payload.sections == []


def test_business_and_source_state_are_separated():
    payload = build_business_experience(
        snapshot(
            items=(business_item(), source_state()),
            diagnostics=(source_state(),),
        )
    ).model_dump(mode="json", exclude_none=True)

    assert [fact["title"] for fact in payload["sections"][0]["facts"]] == [
        "Observed business condition"
    ]
    assert "source_state" not in json.dumps(payload)


def test_nested_source_state_is_excluded():
    item = business_item(metadata={"item_kind": "source_state"})
    assert build_business_experience(snapshot(items=(item,))).sections == []


def test_technical_only_module_does_not_create_empty_section():
    payload = build_business_experience(
        snapshot(items=(source_state(),), diagnostics=(source_state(),))
    )
    assert payload.sections == []


def test_partial_stale_and_real_zero_are_preserved():
    items = (
        business_item(
            "partial",
            title="Partial observed",
            data_status="partial",
            observed_value=4,
        ),
        business_item(
            "stale",
            title="Stale observed",
            data_status="stale",
            observed_value=3,
        ),
        business_item(
            "zero",
            title="Observed zero",
            data_status="ready",
            observed_value=0,
            population_count=8,
        ),
    )
    payload = build_business_experience(snapshot(items=items))
    facts = {fact.title: fact for fact in payload.sections[0].facts}

    assert set(facts) == {"Partial observed", "Stale observed", "Observed zero"}
    assert facts["Stale observed"].stale is True
    assert facts["Observed zero"].metric
    assert facts["Observed zero"].metric.value == 0


@pytest.mark.parametrize(
    ("metric_type", "affected_count", "expected"),
    [
        ("count", 3, 3),
        ("rate", 3, None),
        ("percentage", 3, None),
        ("average", 3, None),
        ("division", 3, None),
        ("amount", 3, None),
        ("scalar", 3, None),
    ],
)
def test_affected_count_is_only_a_count_metric_value(
    metric_type,
    affected_count,
    expected,
):
    item = business_item(
        metric_type=metric_type,
        observed_value=None,
        affected_count=affected_count,
    )
    payload = build_business_experience(snapshot(items=(item,)))
    if expected is None:
        assert payload.sections == []
        return
    metric = payload.sections[0].facts[0].metric
    assert metric
    assert metric.value == expected


@pytest.mark.parametrize(
    "state",
    [
        "missing",
        "blocked",
        "stub",
        "error",
        "empty",
        "schema_only",
        "no_permission",
        "unavailable",
        "invalid_schema",
        "insufficient_data",
    ],
)
def test_missing_and_error_never_create_sections(state):
    item = business_item(data_status=state)
    assert build_business_experience(snapshot(items=(item,))).sections == []


def test_experience_exposes_only_decisions_with_eligible_provenance():
    item = business_item(status="decision_created", decision_id=42)
    unbacked = build_business_experience(snapshot(items=(item,)))
    assert unbacked.sections[0].facts[0].decision is None

    provenance = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id=WORKSPACE_ID,
        decision_id=42,
    )
    item["metadata"] = {DECISION_PROVENANCE_KEY: provenance}
    backed = build_business_experience(
        snapshot(items=(item,), workflow_overlay_verified=True)
    )
    assert backed.sections[0].facts[0].decision
    assert backed.sections[0].facts[0].decision.reference == 42


def test_experience_drops_every_technical_field_recursively():
    item = business_item(
        sql="SELECT secret FROM private_table",
        metadata={
            "source_dataset": "hidden_gold",
            "evidence_refs": ["hidden:evidence"],
            "omega": {"tools": ["dangerous"]},
        },
        details={"readiness": "missing", "payload_hash": "hash-secret"},
        engine={"name": "bayes"},
        omega={"options": [{"id": "run"}]},
    )
    payload = build_business_experience(snapshot(items=(item,))).model_dump(
        mode="json",
        exclude_none=True,
    )
    serialized = json.dumps(payload)

    assert not (_all_keys(payload) & FORBIDDEN_KEYS)
    for secret in ("hidden_gold", "hidden:evidence", "private_table", "hash-secret"):
        assert secret not in serialized


def test_cross_scope_fails_closed_as_not_found():
    foreign = business_item(workspace_id="foreign-workspace")
    with pytest.raises(HTTPException) as exc:
        build_business_experience(snapshot(items=(foreign,)))
    assert exc.value.status_code == 404


def test_response_models_are_strict_and_openapi_bound():
    with pytest.raises(ValidationError):
        ExperienceFact.model_validate(
            {
                "kind": "anomaly",
                "title": "Fact",
                "severity": "high",
                "observed_at": "2026-07-20T10:00:00Z",
                "unexpected": True,
            }
        )

    app = FastAPI()
    app.include_router(routes.router)
    schema = app.openapi()
    for path, model in (
        (
            "/api/control-room/experience",
            "ControlRoomExperienceResponse",
        ),
        (
            "/api/control-room/diagnostics",
            "ControlRoomDiagnosticsResponse",
        ),
    ):
        response = schema["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response["$ref"].endswith(model)
    strict_models = {
        "ControlRoomExperienceResponse",
        "ControlRoomDiagnosticsResponse",
        "ExperienceFact",
        "DiagnosticItem",
        "SurfaceScope",
    }
    for model in strict_models:
        assert schema["components"]["schemas"][model]["additionalProperties"] is False

    assert TENANT_ID != WORKSPACE_ID
