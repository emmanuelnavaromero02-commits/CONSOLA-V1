from app.services.control_room.business_evidence import has_evidence
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)


def test_runtime_attestation_cannot_be_replayed_for_another_item_in_scope():
    fields = runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        source_system="sap_successfactors",
        cartridge="sap_successfactors",
        tenant_id="tenant-11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        source_row={"employee_id": "employee-17"},
        locator_field="employee_id",
        observed_at="2026-07-20T10:00:00Z",
    )
    item = {
        "id": "employee-99",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "source_system": "sap_successfactors",
        "cartridge": "sap_successfactors",
        "tenant_id": "tenant-11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "entity_id": "employee-99",
        "employee_id": "employee-99",
        **fields,
    }

    assert has_evidence(item) is False


def test_runtime_attestation_cannot_replay_same_locator_for_another_observation():
    fields = runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        source_system="sap_successfactors",
        cartridge="sap_successfactors",
        tenant_id="tenant-11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
        source_row={
            "item_id": "item-a",
            "employee_id": "employee-17",
            "metric_name": "headcount",
            "metric_type": "scalar",
            "observed_value": 17,
            "observation_date": "2026-07-20",
        },
        locator_field="employee_id",
        observed_at="2026-07-20",
    )
    item_a = {
        "id": "item-a",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "source_system": "sap_successfactors",
        "cartridge": "sap_successfactors",
        "tenant_id": "tenant-11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "employee_id": "employee-17",
        "metric_name": "headcount",
        "metric_type": "scalar",
        "observed_value": 17,
        "observation_date": "2026-07-20",
        **fields,
    }
    item_b = {
        "id": "item-b",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "source_system": "sap_successfactors",
        "cartridge": "sap_successfactors",
        "tenant_id": "tenant-11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "employee_id": "employee-17",
        "metric_name": "turnover_rate",
        "metric_type": "scalar",
        "observed_value": 999999,
        "observation_date": "2026-07-20",
        **fields,
    }

    assert has_evidence(item_a) is True
    assert has_evidence(item_b) is False
