from app.services.control_room.business_persisted_row import persisted_business_item
from app.services.control_room.business_workflow_provenance import (
    WORKFLOW_QUARANTINE_KEY,
)


def test_quarantined_workflow_is_not_projected_as_active_business_state():
    row = {
        "item_id": "signal-1",
        "item_kind": "intelligence_signal",
        "cartridge_id": "sap_successfactors",
        "source_dataset": "gold_people",
        "title": "Measured signal",
        "status": "decision_created",
        "decision_id": 42,
        "selected_option_id": "execute",
        "execution_status": "dry_run_validated",
        "metadata": {
            "item_kind": "intelligence_signal",
            "source_dataset": "gold_people",
            "metric_type": "count",
            "observed_value": 1,
            "population_count": 10,
            "observation_date": "2026-07-20",
            "evidence_refs": ["gold_people:signal-1"],
            WORKFLOW_QUARANTINE_KEY: {"reason": "fingerprint_mismatch"},
        },
    }

    item = persisted_business_item(
        row,
        expected_item_id="signal-1",
        item_statuses={"open", "decision_created"},
        severity_weights={"medium": 2},
    )

    assert item["status"] == "open"
    assert item["decision_id"] is None
    assert item["selected_option_id"] is None
    assert item["execution_status"] == "not_started"
