from __future__ import annotations

import json
from typing import get_args, get_origin

import pytest
from app.routers.control_room import router
from app.schemas import control_room_alert_mutation_responses as alert_contracts
from app.schemas import control_room_state_mutation_responses as state_contracts
from app.schemas import control_room_workflow_mutation_responses as workflow_contracts
from control_room_mutation_contract_cases import (
    FAMILY_PAYLOADS,
    FORBIDDEN_KEYS,
    ROUTE_CONTRACTS,
    SECRET,
    Model,
    Projector,
)


def keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in keys(item)}
    return set()


@pytest.mark.parametrize("_method,_path,family,model,projector", ROUTE_CONTRACTS)
def test_mutation_contracts_remove_deep_poison_and_use_declared_model(
    _method: str, _path: str, family: str, model: Model, projector: Projector
) -> None:
    result = projector(FAMILY_PAYLOADS[family])
    payload = result.model_dump()

    assert type(result) is model
    assert not keys(payload) & FORBIDDEN_KEYS
    assert SECRET not in json.dumps(payload)


def test_mutation_contracts_preserve_required_business_facts() -> None:
    alert = alert_contracts.project_alert_mutation_response(FAMILY_PAYLOADS["alert"])
    cleared_alert = alert_contracts.project_alert_mutation_response(
        {"ok": True, "alert": None, "item": {"id": "business-1", "status": "dismissed"}}
    )
    lesson = workflow_contracts.project_create_lesson_mutation_response(
        FAMILY_PAYLOADS["create_lesson"]
    )
    outcome = workflow_contracts.project_outcome_mutation_response(
        FAMILY_PAYLOADS["outcome"]
    )
    control = workflow_contracts.project_control_mutation_response(
        FAMILY_PAYLOADS["control"]
    )
    decision = state_contracts.project_decision_mutation_response(
        FAMILY_PAYLOADS["decision"]
    )
    approval = state_contracts.project_approval_mutation_response(
        FAMILY_PAYLOADS["approval"]
    )
    threshold = state_contracts.project_threshold_mutation_response(
        FAMILY_PAYLOADS["threshold"]
    )

    assert alert.alert and alert.alert.status == "snoozed"
    assert alert.alert.priority_score == 0 and alert.alert.push_ready is False
    assert alert.alert.delivery.status == "snoozed"
    assert cleared_alert.alert is None and cleared_alert.item.status == "dismissed"
    assert lesson.lesson.rule == "Validar el margen antes de aprobar."
    assert lesson.item.lesson_count == 0
    assert outcome.outcome.predicted_value == outcome.outcome.actual_value == 0
    assert outcome.outcome.prediction_error == 0
    assert outcome.outcome.outcome_summary is None
    assert control.control.status == "closed" and control.control.due_at is None
    assert decision.item.status == "decision_created"
    assert approval.item.status == "approved"
    assert threshold.threshold.warning_value == 0
    assert threshold.threshold.critical_value is None
    assert threshold.threshold.enabled is False


def test_all_eighteen_public_mutations_have_an_explicit_contract() -> None:
    assert len(ROUTE_CONTRACTS) == 18
    assert len({(method, path) for method, path, *_rest in ROUTE_CONTRACTS}) == 18

    routes = {
        (method, route.path.removeprefix("/api/control-room")): route
        for route in router.routes
        for method in (getattr(route, "methods", set()) or set())
    }
    for method, path, _family, model, _projector in ROUTE_CONTRACTS:
        assert routes[(method, path)].response_model is model


def test_every_public_mutation_with_a_success_shape_is_typed_or_exactly_exempt() -> (
    None
):
    typed_existing = {
        ("POST", "/sap-successfactors/market-validation/run"),
        ("POST", "/actions/preview"),
        ("POST", "/items/{item_id}/analysis"),
    }
    retired = {
        ("POST", "/sap-successfactors/talent/actions/preview"),
        ("POST", "/items/{item_id}/action-preview"),
        ("POST", "/items/{item_id}/action-dry-run"),
        ("POST", "/items/{item_id}/execute"),
    }
    internal_or_fail_closed = {
        ("POST", "/internal/read"),
        ("POST", "/items/{item_id}/auto-run"),
    }
    mutation_contracts = {(method, path) for method, path, *_rest in ROUTE_CONTRACTS}
    actual = {
        (method, route.path.removeprefix("/api/control-room"))
        for route in router.routes
        for method in (getattr(route, "methods", set()) or set())
        if method in {"POST", "PATCH", "PUT", "DELETE"}
    }

    assert (
        actual
        == mutation_contracts | typed_existing | retired | internal_or_fail_closed
    )
    for route in router.routes:
        methods = getattr(route, "methods", set()) or set()
        key = next(
            (
                (method, route.path.removeprefix("/api/control-room"))
                for method in methods
                if method in {"POST", "PATCH", "PUT", "DELETE"}
            ),
            None,
        )
        if key in mutation_contracts | typed_existing:
            assert route.response_model is not None
        elif key in retired:
            assert route.status_code == 410
            assert route.response_model is None
        elif key == ("POST", "/items/{item_id}/auto-run"):
            assert route.response_model is None


def test_new_models_are_strict_and_have_no_free_mapping_fields() -> None:
    models = {model for *_prefix, model, _projector in ROUTE_CONTRACTS}
    for model in models:
        assert model.model_config["extra"] == "forbid"
        for field in model.model_fields.values():
            nodes = (field.annotation, *get_args(field.annotation))
            assert all(get_origin(node) is not dict for node in nodes)
