from __future__ import annotations

import pytest

from app.services import control_room_service
from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)
from app.services.control_room.business_observation import has_evidence
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)


def _item(**overrides):
    item = {
        "id": "metric-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "source_system": "sap",
        "metric_type": "scalar",
        "observed_value": 3,
        "observation_date": "2026-07-20",
    }
    return {**item, **overrides}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "tenant-7"),
        ("workspace_id", "workspace-7"),
        ("owner_user_id", "owner-7"),
        ("user_id", "user-7"),
        ("account_id", "account-7"),
    ],
)
def test_nested_administrative_id_cannot_be_reused_as_evidence(field, value):
    evidence = {
        "source_dataset": "gold_metrics",
        "id": value,
        field: value,
    }

    assert has_evidence(_item(evidence_pack=evidence)) is False


def test_administrative_id_in_sibling_metadata_cannot_be_reused_as_evidence():
    evidence = {
        "metadata": {"workspace_id": "workspace-7"},
        "references": [{"source_dataset": "gold_metrics", "id": "workspace-7"}],
    }

    assert has_evidence(_item(evidence_pack=evidence)) is False


def test_administrative_id_in_sibling_reference_cannot_be_reused_as_evidence():
    evidence = [
        {"workspace_id": "workspace-7"},
        {"source_dataset": "gold_metrics", "id": "workspace-7"},
    ]

    assert has_evidence(_item(evidence_refs=evidence)) is False


def test_numeric_administrative_id_cannot_be_reused_as_typed_evidence():
    evidence = {
        "type": "dataset_row",
        "source_dataset": "gold_metrics",
        "source_record_id": 7,
        "owner_user_id": 7,
    }

    assert has_evidence(_item(evidence_refs=[evidence])) is False


@pytest.mark.parametrize(
    "reference",
    [
        "note:parece-valido",
        "gold_metrics:workspace-7",
        "gold_metrics/workspace-7",
        "unknown:record-7",
        "gold_metrics:N/A",
    ],
)
def test_untyped_or_placeholder_scalar_reference_is_not_evidence(reference):
    item = _item(workspace_id="workspace-7", evidence_refs=[reference])

    assert has_evidence(item) is False


def test_structured_evidence_source_must_match_item_source():
    evidence = {
        "type": "dataset_row",
        "source_dataset": "gold_other",
        "source_record_id": "record-7",
    }

    assert has_evidence(_item(evidence_refs=[evidence])) is False


@pytest.mark.parametrize(
    "evidence",
    [
        {
            "type": "dataset_row",
            "source_dataset": "sap",
            "source_record_id": "record-7",
        },
        {
            "type": "dataset_row",
            "source_system": "gold_metrics",
            "source_record_id": "record-7",
        },
    ],
)
def test_structured_evidence_source_must_match_the_same_source_role(evidence):
    assert has_evidence(_item(evidence_refs=[evidence])) is False


def test_runtime_reference_is_typed_and_verifiable():
    evidence = runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        source_row={"employee_id": "employee-7"},
        locator_field="employee_id",
        observed_at="2026-07-20T10:00:00Z",
    )

    assert evidence["evidence_refs"][0]["type"] == "dataset_row"
    assert has_evidence(_item(**evidence)) is True


@pytest.mark.parametrize("state", ["failed", "failure", "pending", "not_ready"])
def test_failed_and_pending_structured_states_are_diagnostic(state):
    result = classify_business_item(
        _item(
            data_status=state,
            evidence_refs=[
                {
                    "type": "dataset_row",
                    "source_dataset": "gold_metrics",
                    "source_record_id": "record-7",
                }
            ],
        )
    )

    assert result.reason is EligibilityReason.TECHNICAL_STATE


def test_raw_dataset_intelligence_is_not_projected_as_server_intelligence():
    source = control_room_service.ControlRoomSource(
        dataset="gold_metrics",
        cartridge="sap",
        domain="People",
        module_label="Workforce",
        entity_kind="Employee",
        entity_id_field="employee_id",
        entity_label_field="employee_name",
    )
    item = control_room_service._base_item(
        source,
        {
            "detected_at": "2026-07-20T10:00:00Z",
            "metric_type": "scalar",
            "observed_value": 3,
            "intelligence": {
                "options": [{"id": "attacker-option"}],
                "impact_estimate": 999999,
            },
        },
        "anomaly",
        "employee-7",
        "Employee 7",
    )

    assert item.get("intelligence") in (None, {})
    assert "attacker-option" not in repr(item)


def test_raw_metadata_cannot_hide_intelligence_artifacts():
    source = control_room_service.ControlRoomSource(
        dataset="gold_metrics",
        cartridge="sap",
        domain="People",
        module_label="Workforce",
        entity_kind="Employee",
        entity_id_field="employee_id",
        entity_label_field="employee_name",
    )
    item = control_room_service._base_item(
        source,
        {
            "detected_at": "2026-07-20T10:00:00Z",
            "metric_type": "scalar",
            "observed_value": 3,
            "metadata": {
                "intelligence": {"options": [{"id": "hidden-attacker-option"}]}
            },
        },
        "anomaly",
        "employee-7",
        "Employee 7",
    )

    assert "hidden-attacker-option" not in repr(item)
