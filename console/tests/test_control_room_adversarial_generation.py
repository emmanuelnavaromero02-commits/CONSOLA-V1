from __future__ import annotations

import json

from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_evidence import has_evidence
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    business_observation_fingerprint,
)


OLD_KEY = "old_control_room_evidence_key_with_more_than_32_chars"
NEW_KEY = "new_control_room_evidence_key_with_more_than_32_chars"


def _scoped_item(*, metric_name: str = "headcount", value: int = 7) -> dict:
    source_row = {
        "item_id": "business-1",
        "kind": "anomaly",
        "metric_name": metric_name,
        "metric_type": "scalar",
        "observed_value": value,
        "observation_date": "2026-07-20",
    }
    return {
        "id": "business-1",
        "kind": "anomaly",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "source_dataset": "gold_metrics",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
        **source_row,
        **runtime_row_evidence_fields(
            source_dataset="gold_metrics",
            source_system="sap_hcm",
            cartridge="sap_hcm",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            source_row=source_row,
            locator_field="item_id",
            observed_at="2026-07-20",
        ),
    }


def test_locator_only_attestation_cannot_prove_fabricated_metric_or_value():
    evidence = runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        source_system="sap_hcm",
        cartridge="sap_hcm",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        source_row={"employee_id": "employee-17"},
        locator_field="employee_id",
        observed_at="2026-07-20",
    )
    item = {
        **_scoped_item(metric_name="turnover_rate", value=999999),
        "id": "employee-17",
        "item_id": "employee-17",
        "employee_id": "employee-17",
        **evidence,
    }

    assert has_evidence(item) is False
    assert classify_business_item(item).eligible is False


def test_metric_name_changes_business_generation_even_with_same_legacy_ref():
    base = {
        "id": "business-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "metric_type": "scalar",
        "observed_value": 7,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_metrics:business-1"],
    }

    assert business_observation_fingerprint(
        {**base, "metric_name": "headcount"}
    ) != business_observation_fingerprint({**base, "metric_name": "turnover_rate"})


def test_signing_key_rotation_does_not_change_business_generation(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID", "old-key")
    monkeypatch.setenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY", OLD_KEY)
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS", raising=False)
    old_item = _scoped_item()

    monkeypatch.setenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID", "new-key")
    monkeypatch.setenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY", NEW_KEY)
    monkeypatch.setenv(
        "CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS",
        json.dumps({"old-key": OLD_KEY}),
    )
    new_item = _scoped_item()

    assert has_evidence(old_item) is True
    assert has_evidence(new_item) is True
    assert business_observation_fingerprint(old_item) == (
        business_observation_fingerprint(new_item)
    )
