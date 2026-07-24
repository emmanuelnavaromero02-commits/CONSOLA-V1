from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_projection import project_business_item
from app.services.control_room.business_state_overlay import overlay_business_state
from app.services.control_room.business_surface_provenance import (
    surface_workflow_provenance_verified,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    business_observation_fingerprint,
    workflow_eligibility_provenance,
)
from app.services.control_room.business_workflow_state import (
    TERMINAL_EXECUTION_STATUSES,
    expected_workflow_stage,
)
from control_room_surface_fixtures import WORKSPACE_ID, business_item, snapshot


ITEM_STATUSES = (
    "open",
    "in_review",
    "decision_created",
    "approved",
    "resolved",
    "dismissed",
)
EXECUTION_STATUSES = (
    "not_started",
    "preview_generated",
    "dry_run_validated",
    "blocked",
    "failed",
    "executed",
    "resolved",
    "terminal",
)


def _expected(status: str, execution_status: str) -> WorkflowStage | None:
    if execution_status in TERMINAL_EXECUTION_STATUSES:
        if status in {"approved", "resolved"}:
            return WorkflowStage.EXECUTED
        return None
    return {
        "in_review": (
            WorkflowStage.OPTION_SELECTED if execution_status == "not_started" else None
        ),
        "decision_created": WorkflowStage.DECISION_CREATED,
        "approved": WorkflowStage.APPROVED,
        "resolved": (
            WorkflowStage.APPROVED if execution_status == "not_started" else None
        ),
    }.get(status)


def _row(status: str, execution_status: str) -> dict[str, object]:
    option_only = status == "in_review"
    return {
        "status": status,
        "execution_status": execution_status,
        "decision_id": None if option_only else 42,
        "selected_option_id": "review" if option_only else None,
    }


@pytest.mark.parametrize("status", ITEM_STATUSES)
@pytest.mark.parametrize("execution_status", EXECUTION_STATUSES)
@pytest.mark.parametrize("stage", tuple(WorkflowStage))
def test_complete_status_stage_execution_matrix(
    status: str,
    execution_status: str,
    stage: WorkflowStage,
) -> None:
    item = business_item(status="open")
    row = _row(status, execution_status)
    expected = _expected(status, execution_status)
    provenance = workflow_eligibility_provenance(
        {
            **item,
            "selected_option_id": (
                "review" if stage is WorkflowStage.OPTION_SELECTED else None
            ),
        },
        stage=stage,
        workspace_id=WORKSPACE_ID,
        decision_id=None if stage is WorkflowStage.OPTION_SELECTED else 42,
        option_id="review" if stage is WorkflowStage.OPTION_SELECTED else None,
    )
    state = {
        **row,
        "metadata": {
            DECISION_PROVENANCE_KEY: provenance,
            CURRENT_ELIGIBILITY_FINGERPRINT_KEY: business_observation_fingerprint(item),
        },
    }
    projected = overlay_business_state(
        [item],
        {str(item["id"]): state},
        item_statuses=set(ITEM_STATUSES),
        projector=project_business_item,
        sort_key=lambda value: str(value.get("id")),
    )[0]

    assert (expected_workflow_stage(row) is stage) is (expected is stage)
    should_verify = row["decision_id"] is not None and expected is stage
    assert surface_workflow_provenance_verified(projected) is should_verify


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            {
                "status": "decision_created",
                "execution_status": "not_started",
                "decision_id": 42,
            },
            WorkflowStage.DECISION_CREATED,
        ),
        (
            {
                "status": "approved",
                "execution_status": "not_started",
                "decision_id": 42,
            },
            WorkflowStage.APPROVED,
        ),
        (
            {
                "status": "approved",
                "execution_status": "executed",
                "decision_id": 42,
            },
            WorkflowStage.EXECUTED,
        ),
        (
            {
                "status": "resolved",
                "execution_status": "not_started",
                "decision_id": 42,
            },
            WorkflowStage.APPROVED,
        ),
        (
            {
                "status": "resolved",
                "execution_status": "executed",
                "decision_id": 42,
            },
            WorkflowStage.EXECUTED,
        ),
        (
            {
                "status": "decision_created",
                "execution_status": "executed",
                "decision_id": 42,
            },
            None,
        ),
        (
            {
                "status": "approved",
                "execution_status": "unknown",
                "decision_id": 42,
            },
            None,
        ),
    ],
)
def test_expected_stage_fails_closed_for_ambiguous_rows(
    row: dict[str, object],
    expected: WorkflowStage | None,
) -> None:
    assert expected_workflow_stage(row) is expected


def _state(
    item: dict[str, object],
    *,
    status: str,
    stage: WorkflowStage,
    execution_status: str = "not_started",
    decision_id: int = 42,
) -> dict[str, object]:
    provenance = workflow_eligibility_provenance(
        item,
        stage=stage,
        workspace_id=WORKSPACE_ID,
        decision_id=decision_id,
    )
    return {
        "status": status,
        "decision_id": decision_id,
        "execution_status": execution_status,
        "metadata": {
            DECISION_PROVENANCE_KEY: provenance,
            CURRENT_ELIGIBILITY_FINGERPRINT_KEY: business_observation_fingerprint(item),
        },
    }


def _decision(
    item: dict[str, object],
    state: dict[str, object],
):
    before = deepcopy(state)
    projected = overlay_business_state(
        [item],
        {str(item["id"]): state},
        item_statuses=set(ITEM_STATUSES),
        projector=project_business_item,
        sort_key=lambda value: str(value.get("id")),
    )
    fact = (
        build_business_experience(snapshot(items=tuple(projected))).sections[0].facts[0]
    )
    assert state == before
    return fact.decision


@pytest.mark.parametrize(
    ("status", "stage", "execution_status", "visible"),
    [
        ("decision_created", WorkflowStage.DECISION_CREATED, "not_started", True),
        ("decision_created", WorkflowStage.APPROVED, "not_started", False),
        ("decision_created", WorkflowStage.EXECUTED, "not_started", False),
        ("approved", WorkflowStage.DECISION_CREATED, "not_started", False),
        ("approved", WorkflowStage.APPROVED, "not_started", True),
        ("resolved", WorkflowStage.DECISION_CREATED, "not_started", False),
        ("resolved", WorkflowStage.APPROVED, "not_started", True),
        ("approved", WorkflowStage.APPROVED, "executed", False),
        ("approved", WorkflowStage.EXECUTED, "executed", True),
        ("resolved", WorkflowStage.EXECUTED, "terminal", True),
    ],
)
def test_surface_decision_requires_exact_canonical_stage_without_dml(
    status: str,
    stage: WorkflowStage,
    execution_status: str,
    visible: bool,
) -> None:
    item = business_item(status="open")

    decision = _decision(
        item,
        _state(
            item,
            status=status,
            stage=stage,
            execution_status=execution_status,
        ),
    )

    assert (decision is not None) is visible


def test_mixed_items_never_share_stage_verification() -> None:
    valid = business_item("valid", title="Valid decision", status="open")
    mismatched = business_item("mismatch", title="Mismatched decision", status="open")
    states = {
        "valid": _state(
            valid,
            status="approved",
            stage=WorkflowStage.APPROVED,
        ),
        "mismatch": _state(
            mismatched,
            status="approved",
            stage=WorkflowStage.DECISION_CREATED,
        ),
    }
    projected = overlay_business_state(
        [valid, mismatched],
        states,
        item_statuses=set(ITEM_STATUSES),
        projector=project_business_item,
        sort_key=lambda value: str(value.get("id")),
    )
    facts = {
        fact.title: fact
        for section in build_business_experience(
            snapshot(items=tuple(projected))
        ).sections
        for fact in section.facts
    }

    assert facts["Valid decision"].decision is not None
    assert facts["Mismatched decision"].decision is None
