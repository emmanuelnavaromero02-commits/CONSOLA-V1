from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
    verified_runtime_row_reference,
)
from app.services.intelligence.decision_orchestrator import normalize_signal
from app.services.intelligence.evidence_refs import normalize_evidence_refs


def _runtime_reference() -> dict:
    fields = runtime_row_evidence_fields(
        source_dataset="gold_people",
        source_system="sap_hcm",
        cartridge="sap_hcm",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        source_row={"item_id": "business-1"},
        locator_field="item_id",
        observed_at="2026-07-21",
    )
    return fields["evidence_refs"][0]


def _forged_reference() -> dict:
    return {
        "type": "dataset_row",
        "source_dataset": "gold_people",
        "source_record_id": "record-fake",
        "source_locator": {
            "relation": "gold_people",
            "field": "item_id",
            "value": "fake",
        },
        "observed_at": "2026-07-21",
    }


def test_normalized_runtime_reference_remains_server_verified():
    normalized = normalize_evidence_refs(
        [_runtime_reference()],
        allow_server_dataset_rows=True,
    )

    assert verified_runtime_row_reference(normalized[0]) is True


def test_normalizer_denies_attested_dataset_row_without_server_trust():
    with pytest.raises(HTTPException) as exc:
        normalize_evidence_refs([_runtime_reference()])

    assert exc.value.status_code == 422


def test_normalizer_rejects_unattested_dataset_row():
    with pytest.raises(HTTPException) as exc:
        normalize_evidence_refs([_forged_reference()])

    assert exc.value.status_code == 422


def test_orchestrator_rejects_unattested_payload_dataset_row():
    with pytest.raises(HTTPException) as exc:
        normalize_signal(
            {
                "source_type": "control_room_item",
                "source_id": "business-1",
                "evidence_refs": [_forged_reference()],
            },
            {"metadata": {}},
        )

    assert exc.value.status_code == 422


def test_orchestrator_rejects_attested_payload_dataset_row_replay():
    with pytest.raises(HTTPException) as exc:
        normalize_signal(
            {
                "source_type": "control_room_item",
                "source_id": "business-1",
                "evidence_refs": [_runtime_reference()],
            },
            {"metadata": {}},
        )

    assert exc.value.status_code == 422


def test_orchestrator_preserves_verified_source_reference():
    normalized = normalize_signal(
        {"source_type": "control_room_item", "source_id": "business-1"},
        {"metadata": {"evidence_refs": [_runtime_reference()]}},
    )

    assert verified_runtime_row_reference(normalized["evidence_refs"][0]) is True
