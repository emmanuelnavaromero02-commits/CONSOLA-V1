from __future__ import annotations

import pytest

from app.services.control_room.business_evidence import has_evidence


def _scoped_item(workspace_id: str, evidence_refs: list[object]) -> dict:
    return {
        "id": "metric-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "source_system": "sap",
        "cartridge": "sap",
        "tenant_id": "tenant-a",
        "workspace_id": workspace_id,
        "evidence_refs": evidence_refs,
    }


@pytest.mark.parametrize("workspace_id", ["workspace-a", "workspace-b"])
def test_scoped_item_rejects_unbound_legacy_reference(workspace_id):
    item = _scoped_item(workspace_id, ["gold_metrics:record-17"])

    assert has_evidence(item) is False


def test_scoped_item_rejects_unbound_typed_document():
    item = _scoped_item(
        "workspace-a",
        [
            {
                "type": "document",
                "source_dataset": "gold_metrics",
                "document_id": "document-17",
            }
        ],
    )

    assert has_evidence(item) is False
