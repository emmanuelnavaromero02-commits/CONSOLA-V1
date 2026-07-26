from __future__ import annotations

from collections.abc import Callable

from app.schemas import control_room_alert_mutation_responses as alert_contracts
from app.schemas import control_room_state_mutation_responses as state_contracts
from app.schemas import control_room_workflow_mutation_responses as workflow_contracts
from app.schemas.control_room_public_projection import PublicProjectionModel

Model = type[PublicProjectionModel]
Projector = Callable[[object], PublicProjectionModel]
SECRET = "TOPSECRET-P1C"
FORBIDDEN_KEYS = {
    "action",
    "assignee_id",
    "authority_audit",
    "bindings",
    "cartridge_id",
    "connection",
    "created_by_id",
    "dataset",
    "datasets",
    "decision",
    "decision_id",
    "details",
    "execution_id",
    "event_type",
    "id",
    "lesson_id",
    "metadata",
    "module_id",
    "anomaly",
    "option_id",
    "owner",
    "owner_user_id",
    "payload",
    "provenance",
    "receipt",
    "run_id",
    "selected_option_id",
    "source_decision_id",
    "sql",
    "step_id",
    "st",
    "tenant_id",
    "workspace_id",
}
POISON = {
    "tenant_id": "tenant-secret",
    "workspace_id": "workspace-secret",
    "owner_user_id": 77,
    "created_by_id": 77,
    "assignee_id": 88,
    "cartridge_id": "technical-cartridge",
    "module_id": "technical-module",
    "source_decision_id": 900,
    "metadata": {"password": SECRET, "sql": f"SELECT '{SECRET}'"},
    "details": {"dataset": "raw_people", "payload": SECRET},
    "authority_audit": {"bindings": [{"receipt": SECRET}]},
    "datasets": [{"connection": SECRET, "provenance": SECRET}],
}


def nested(base: dict[str, object]) -> dict[str, object]:
    return {**base, **POISON, "unknown": [{**POISON, "value": SECRET}]}


FAMILY_PAYLOADS = {
    "alert": nested(
        {
            "ok": True,
            "alert": nested(
                {
                    "item_id": "business-1",
                    "status": "snoozed",
                    "priority_score": 0,
                    "push_ready": False,
                    "snoozed_until": "2026-07-27T12:00:00Z",
                    "delivery": nested({"status": "snoozed", "enabled": False}),
                    "owner": "owner@example.com",
                }
            ),
            "item": nested({"id": "business-1", "status": "in_review"}),
        }
    ),
    "step": nested(
        {
            "recorded": True,
            "step_id": "investigation",
            "event_type": "internal_event",
            "item": nested({"id": "business-1", "status": "in_review"}),
        }
    ),
    "create_lesson": nested(
        {
            "created": True,
            "lesson": nested({"id": 91, "rule": "Validar el margen antes de aprobar."}),
            "lessons": [
                nested({"id": 91, "rule": "Validar el margen antes de aprobar."})
            ],
            "item": nested({"id": "business-1", "lesson_count": 0}),
        }
    ),
    "outcome": nested(
        {
            "recorded": True,
            "lesson_recorded": False,
            "outcome": nested(
                {
                    "action_taken": "Revisar margen",
                    "option_id": "review",
                    "predicted_value": 0,
                    "actual_value": 0,
                    "prediction_error": 0,
                    "outcome_summary": None,
                }
            ),
            "item": nested({"id": "business-1", "status": "approved"}),
        }
    ),
    "apply_lesson": nested(
        {
            "applied": True,
            "lesson": nested({"id": 91, "rule": "Validar el margen antes de aprobar."}),
            "lesson_application": nested(
                {
                    "lesson_id": 91,
                    "rule": "Validar el margen antes de aprobar.",
                    "note": "Aplicar en la siguiente revisión",
                    "applied_at": "2026-07-26T12:00:00Z",
                }
            ),
            "item": nested({"id": "business-1", "status": "in_review"}),
        }
    ),
    "control": nested(
        {
            "updated": True,
            "control": nested(
                {
                    "id": "refresh",
                    "status": "closed",
                    "st": "Cerrado",
                    "due_at": None,
                    "note": "Validación completada",
                }
            ),
            "item": nested({"id": "business-1", "status": "in_review"}),
        }
    ),
    "decision": nested(
        {
            "decision": nested({"id": 42, "title": "Revisar margen", "status": "open"}),
            "item": nested({"id": "business-1", "status": "decision_created"}),
            "anomaly": nested({"id": "business-1", "status": "decision_created"}),
        }
    ),
    "option": nested(
        {
            "selected": True,
            "option_id": "review",
            "item": nested(
                {
                    "id": "business-1",
                    "status": "in_review",
                    "selected_option_id": "review",
                }
            ),
            "anomaly": nested({"id": "business-1", "status": "in_review"}),
        }
    ),
    "approval": nested(
        {
            "approved": True,
            "decision_id": 42,
            "action": nested(
                {"action_text": "Aprobación registrada", "ts": "2026-07-26T12:00:00Z"}
            ),
            "item": nested({"id": "business-1", "status": "approved"}),
            "anomaly": nested({"id": "business-1", "status": "approved"}),
        }
    ),
    "dismiss": nested(
        {"dismissed": True, "item": nested({"id": "business-1", "status": "dismissed"})}
    ),
    "reopen": nested(
        {"reopened": True, "item": nested({"id": "business-1", "status": "open"})}
    ),
    "threshold": nested(
        {
            "threshold": nested(
                {
                    "anomaly_type": "low_margin",
                    "metric": "margin_pct",
                    "warning_value": 0,
                    "critical_value": None,
                    "currency": "USD",
                    "enabled": False,
                }
            )
        }
    ),
}


CONTRACTS: dict[str, tuple[Model, Projector]] = {
    "alert": (
        alert_contracts.ControlRoomAlertMutationResponse,
        alert_contracts.project_alert_mutation_response,
    ),
    "step": (
        workflow_contracts.ControlRoomStepMutationResponse,
        workflow_contracts.project_step_mutation_response,
    ),
    "create_lesson": (
        workflow_contracts.ControlRoomCreateLessonMutationResponse,
        workflow_contracts.project_create_lesson_mutation_response,
    ),
    "outcome": (
        workflow_contracts.ControlRoomOutcomeMutationResponse,
        workflow_contracts.project_outcome_mutation_response,
    ),
    "apply_lesson": (
        workflow_contracts.ControlRoomApplyLessonMutationResponse,
        workflow_contracts.project_apply_lesson_mutation_response,
    ),
    "control": (
        workflow_contracts.ControlRoomControlMutationResponse,
        workflow_contracts.project_control_mutation_response,
    ),
    "decision": (
        state_contracts.ControlRoomDecisionMutationResponse,
        state_contracts.project_decision_mutation_response,
    ),
    "option": (
        state_contracts.ControlRoomOptionMutationResponse,
        state_contracts.project_option_mutation_response,
    ),
    "approval": (
        state_contracts.ControlRoomApprovalMutationResponse,
        state_contracts.project_approval_mutation_response,
    ),
    "dismiss": (
        state_contracts.ControlRoomDismissMutationResponse,
        state_contracts.project_dismiss_mutation_response,
    ),
    "reopen": (
        state_contracts.ControlRoomReopenMutationResponse,
        state_contracts.project_reopen_mutation_response,
    ),
    "threshold": (
        state_contracts.ControlRoomThresholdMutationResponse,
        state_contracts.project_threshold_mutation_response,
    ),
}
ROUTE_FAMILIES = (
    *(
        ("POST", f"/alerts/{{item_id}}/{action}", "alert")
        for action in ("ack", "snooze", "assign", "false-positive")
    ),
    ("POST", "/items/{item_id}/step", "step"),
    ("POST", "/items/{item_id}/lessons", "create_lesson"),
    ("POST", "/items/{item_id}/outcomes", "outcome"),
    ("POST", "/items/{item_id}/lessons/{lesson_id}/apply", "apply_lesson"),
    ("POST", "/items/{item_id}/control/{control_id}", "control"),
    ("POST", "/anomalies/{anomaly_id}/decision", "decision"),
    ("POST", "/items/{item_id}/decision", "decision"),
    ("POST", "/items/{item_id}/option", "option"),
    ("POST", "/anomalies/{anomaly_id}/approve", "approval"),
    ("POST", "/items/{item_id}/approve", "approval"),
    ("POST", "/items/{item_id}/dismiss", "dismiss"),
    ("POST", "/items/{item_id}/reopen", "reopen"),
    ("POST", "/thresholds", "threshold"),
    ("PATCH", "/thresholds", "threshold"),
)
ROUTE_CONTRACTS = tuple(
    (method, path, family, *CONTRACTS[family])
    for method, path, family in ROUTE_FAMILIES
)
