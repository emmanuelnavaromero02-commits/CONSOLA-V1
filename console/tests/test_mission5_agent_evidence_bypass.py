from __future__ import annotations

import pytest

from app.services import control_room_service, tool_policy
from app.services.control_room.business_persisted_row import persisted_business_item
from app.services.control_room.business_agent_evidence import (
    is_agent_authored,
    without_agent_attestations,
)
from app.services.control_room.business_projection import (
    filter_business_items,
    normalize_persisted_business_item,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)

TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
AGENT = "33333333-3333-4333-8333-333333333333"


def _signed_metadata(item_id: str) -> dict:
    return {
        "data_status": "ready",
        "source_system": "replicon",
        "metric_type": "scalar",
        "observed_value": 1,
        "observation_date": "2026-07-20",
        **runtime_row_evidence_fields(
            source_dataset="gold_workforce",
            source_system="replicon",
            cartridge="replicon",
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            source_row={"item_id": item_id},
            locator_field="item_id",
            observed_at="2026-07-20",
            business_observation={
                "id": item_id,
                "kind": "agent_alert",
                "metric_type": "scalar",
                "observed_value": 1,
                "observation_date": "2026-07-20",
            },
        ),
    }


def _row(metadata: dict, item_id: str = "alert-1") -> dict:
    return {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "item_id": item_id,
        "cartridge_id": "replicon",
        "domain": "Operacion",
        "source_dataset": "gold_workforce",
        "item_kind": "agent_alert",
        "title": "Alerta de capacidad",
        "severity": "high",
        "status": "open",
        "decision_id": None,
        "entity_kind": "employee",
        "entity_id": "7",
        "entity_label": "Equipo norte",
        "anomaly_type": "capacity_risk",
        "metadata": metadata,
        "first_seen_at": None,
        "last_seen_at": None,
        "resolved_at": None,
        "dismissed_at": None,
        "impact_estimate": None,
        "impact_currency": "USD",
        "confidence": 0.8,
        "priority_score": 10,
        "selected_option_id": None,
        "execution_status": "not_started",
    }


def test_signed_reference_really_is_attested_before_the_closure():
    metadata = _signed_metadata("alert-1")
    assert metadata["evidence_refs"][0]["server_attestation"]
    item = control_room_service._persisted_intelligence_payload(_row(metadata))
    assert filter_business_items([item])


def test_agent_authored_row_with_copied_signed_reference_is_not_evidence():
    metadata = {
        **_signed_metadata("alert-1"),
        "source": "agent",
        "agent_id": AGENT,
        "control_state": {"source": "agent", "advisory": True},
    }
    item = control_room_service._persisted_intelligence_payload(_row(metadata))

    assert filter_business_items([item]) == []
    assert "server_attestation" not in repr(item)


def test_normalized_projection_strips_the_same_references():
    metadata = {**_signed_metadata("alert-1"), "source": "agent", "agent_id": AGENT}
    item = normalize_persisted_business_item(_row(metadata))

    assert "server_attestation" not in repr(item)
    assert filter_business_items([item]) == []


def test_each_agent_marker_alone_triggers_the_strip():
    signed = _signed_metadata("alert-1")
    for marker in (
        {"source": "agent"},
        {"agent_id": AGENT},
        {"control_state": {"source": "agent"}},
    ):
        metadata = {**signed, **marker}
        assert is_agent_authored(metadata)
        assert "server_attestation" not in repr(without_agent_attestations(metadata))


def test_strip_is_recursive_and_keeps_unsigned_references():
    metadata = {
        "source": "agent",
        "evidence_refs": [
            {"kind": "wisdom_bit_monitor", "wisdom_bit_id": "WB-TALENTO"},
            {"type": "dataset_row", "server_attestation": "x" * 64},
        ],
        "details": {
            "evidence": {"nested": [{"server_attestation": "y" * 64, "a": 1}]},
            "note": "kept",
        },
        "analysis_evidence": {"engine": "wisdom_bit", "metrics": {"signal_count": 2}},
    }
    cleaned = without_agent_attestations(metadata)

    assert cleaned["evidence_refs"] == [
        {"kind": "wisdom_bit_monitor", "wisdom_bit_id": "WB-TALENTO"}
    ]
    assert cleaned["details"] == {"evidence": {"nested": []}, "note": "kept"}
    assert cleaned["analysis_evidence"]["metrics"]["signal_count"] == 2


def test_console_rows_are_returned_unchanged():
    metadata = _signed_metadata("alert-1")
    assert not is_agent_authored(metadata)
    assert without_agent_attestations(metadata) == metadata


AGENT_ITEM = "agent_alert:" + "d" * 32


def test_mcp_infra_item_id_marks_the_row_even_with_markers_overwritten():
    metadata = {
        **_signed_metadata(AGENT_ITEM),
        "source": "console",
        "agent_id": "",
        "control_state": {},
    }
    assert is_agent_authored(metadata, item_id=AGENT_ITEM)

    item = control_room_service._persisted_intelligence_payload(
        _row(metadata, item_id=AGENT_ITEM)
    )
    normalized = normalize_persisted_business_item(_row(metadata, item_id=AGENT_ITEM))

    assert filter_business_items([item]) == []
    assert filter_business_items([normalized]) == []
    assert "server_attestation" not in repr(item)
    assert "server_attestation" not in repr(normalized)


def test_command_path_strips_agent_attestations_too():
    row = _row(_signed_metadata(AGENT_ITEM), item_id=AGENT_ITEM)

    item = persisted_business_item(
        row,
        expected_item_id=AGENT_ITEM,
        item_statuses={"open"},
        severity_weights={"critical": 4, "high": 3, "medium": 2, "low": 1},
    )

    assert item is not None
    assert "server_attestation" not in repr(item)


def test_copilot_tool_policy_refuses_server_only_alert_arguments():
    for key in ("_server_metadata_patch", "_server_event_kind", "_server_event_metadata"):
        with pytest.raises(tool_policy.ToolPolicyError):
            tool_policy._reject_backend_context({"alert_type": "x", key: {}})
