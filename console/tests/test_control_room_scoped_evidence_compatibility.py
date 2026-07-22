from __future__ import annotations

import uuid

import pytest

from app.services.control_room.business_evidence import has_evidence
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_projection import (
    normalize_persisted_business_item,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)


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


def test_persisted_uuid_scope_normalizes_before_evidence_validation():
    tenant_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    item_id = "metric-1"
    observation = {
        "id": item_id,
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "source_system": "sap",
        "cartridge": "sap",
        "tenant_id": str(tenant_id),
        "workspace_id": str(workspace_id),
        "metric_type": "count",
        "observed_value": 1,
        "population_count": 10,
        "observation_date": "2026-07-20",
    }
    metadata = {
        **observation,
        **runtime_row_evidence_fields(
            source_dataset="gold_metrics",
            source_system="sap",
            cartridge="sap",
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id),
            source_row={"item_id": item_id},
            locator_field="item_id",
            observed_at="2026-07-20",
            business_observation=observation,
        ),
    }

    normalized = normalize_persisted_business_item(
        {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "item_id": item_id,
            "item_kind": "anomaly",
            "cartridge_id": "sap",
            "source_dataset": "gold_metrics",
            "metadata": metadata,
        }
    )

    assert normalized["tenant_id"] == str(tenant_id)
    assert normalized["workspace_id"] == str(workspace_id)
    assert classify_business_item(normalized).eligible is True
