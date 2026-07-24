from __future__ import annotations

from app.services import control_room_service
from app.services.control_room.business_source_scope import scoped_source_row


def test_runtime_source_row_becomes_typed_business_evidence():
    source = next(
        source
        for source in control_room_service._all_sources()  # noqa: SLF001
        if source.dataset == "employees_anomalies"
    )
    row = {
        "pernr": "1001",
        "full_name": "Ana Gomez",
        "anomaly_type": "terminated_but_active",
        "severity": "critical",
        "details": '{"reason":"Baja terminada pero usuario activo"}',
        "detected_at": "2026-05-20T10:00:00Z",
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
    }
    item = control_room_service._normalize_standard_anomaly(  # noqa: SLF001
        source,
        scoped_source_row(
            row,
            tenant_id="tenant-A",
            workspace_id="workspace-A",
        ),
    )

    assert item["evidence_refs"][0]["type"] == "dataset_row"
    assert item["evidence_refs"][0]["source_dataset"] == "employees_anomalies"
    assert item["evidence_refs"][0]["source_record_id"].startswith("record-")
