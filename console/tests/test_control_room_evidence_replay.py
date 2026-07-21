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
