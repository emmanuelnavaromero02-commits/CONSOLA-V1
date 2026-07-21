from app.services.control_room.business_projection import (
    filter_business_decisions,
    project_business_item,
)
from app.services.control_room.business_state_overlay import overlay_business_state
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    business_observation_fingerprint,
    decision_eligibility_provenance,
)


def _item() -> dict:
    return {
        "id": "business-1",
        "kind": "anomaly",
        "status": "open",
        "source_dataset": "gold_metrics",
        "observed_value": 2,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_metrics:business-1"],
    }


def _overlay(item, state):
    return overlay_business_state(
        [item],
        {item["id"]: state},
        item_statuses={"open", "decision_created", "approved"},
        projector=lambda value, **_kwargs: value,
        sort_key=lambda value: str(value.get("id")),
    )[0]


def test_overlay_does_not_revive_unproven_technical_workflow():
    item = _item()
    projected = _overlay(
        item,
        {
            "status": "decision_created",
            "decision_id": 42,
            "selected_option_id": "execute",
            "execution_status": "executed",
            "metadata": {
                "item_kind": "source_state",
                "data_status": "missing",
                "lessons": ["fabricated"],
            },
        },
    )

    assert projected["status"] == "open"
    assert projected["decision_id"] is None
    assert projected["selected_option_id"] is None
    assert projected["execution_status"] == "not_started"
    assert projected["lessons"] is None


def test_overlay_preserves_workflow_with_matching_eligible_provenance():
    item = _item()
    fingerprint = business_observation_fingerprint(item)
    metadata = {
        DECISION_PROVENANCE_KEY: decision_eligibility_provenance(item, decision_id=42),
        CURRENT_ELIGIBILITY_FINGERPRINT_KEY: fingerprint,
    }

    projected = _overlay(
        item,
        {
            "status": "decision_created",
            "decision_id": 42,
            "execution_status": "not_started",
            "metadata": metadata,
        },
    )

    assert projected["status"] == "decision_created"
    assert projected["decision_id"] == 42


def test_overlay_clears_workflow_when_live_observation_changes():
    linked_item = _item()
    metadata = {
        DECISION_PROVENANCE_KEY: decision_eligibility_provenance(
            linked_item, decision_id=42
        ),
        CURRENT_ELIGIBILITY_FINGERPRINT_KEY: business_observation_fingerprint(
            linked_item
        ),
    }
    live_item = {**linked_item, "observed_value": 3}

    projected = _overlay(
        live_item,
        {
            "status": "decision_created",
            "decision_id": 42,
            "execution_status": "not_started",
            "metadata": metadata,
        },
    )

    assert projected["status"] == "open"
    assert projected["decision_id"] is None


def test_overlay_clears_workflow_when_linked_decision_differs_from_provenance():
    item = _item()
    metadata = {
        DECISION_PROVENANCE_KEY: decision_eligibility_provenance(item, decision_id=42),
        CURRENT_ELIGIBILITY_FINGERPRINT_KEY: business_observation_fingerprint(item),
    }

    projected = _overlay(
        item,
        {
            "status": "decision_created",
            "decision_id": 99,
            "execution_status": "not_started",
            "metadata": metadata,
        },
    )

    assert projected["status"] == "open"
    assert projected["decision_id"] is None


def test_decision_projection_requires_eligible_link_provenance():
    item = _item()
    linked = {
        **item,
        "item_id": item["id"],
        "item_kind": item["kind"],
        "decision_id": 42,
        "metadata": {},
    }
    decision = {"id": 42, "kpis": [{"source": "control_room"}]}

    assert filter_business_decisions([decision], [linked]) == []

    fingerprint = business_observation_fingerprint(item)
    linked["metadata"] = {
        DECISION_PROVENANCE_KEY: decision_eligibility_provenance(item, decision_id=42),
        CURRENT_ELIGIBILITY_FINGERPRINT_KEY: fingerprint,
        "observed_value": 2,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_metrics:business-1"],
    }
    assert filter_business_decisions([decision], [linked]) == [decision]


def test_derived_item_provenance_uses_validated_parent_context():
    child = project_business_item(
        {
            **_item(),
            "id": "derived-1",
            "kind": "intelligence_signal",
            "parent_item_id": "parent-1",
        },
        eligible_parent_ids={"parent-1"},
    )

    provenance = decision_eligibility_provenance(child, decision_id=42)

    assert provenance["eligible_at_link"] is True
    assert provenance["item_id"] == "derived-1"
