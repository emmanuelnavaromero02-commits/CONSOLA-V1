from __future__ import annotations

from copy import deepcopy

from app.services.control_room.business_persisted_row import persisted_business_item
from app.services.control_room.business_state_overlay import overlay_business_state
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    ELIGIBILITY_POLICY_VERSION,
    ELIGIBILITY_POLICY_VERSION_KEY,
    business_observation_fingerprint,
)


def _item(**overrides):
    item = {
        "id": "business-1",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "source_dataset": "gold_metrics",
        "metric_type": "scalar",
        "observed_value": 2,
        "observation_date": "2026-07-20",
        "evidence_refs": [
            {
                "type": "dataset_row",
                "source_dataset": "gold_metrics",
                "source_record_id": "record-1",
            }
        ],
    }
    return {**item, **overrides}


def _artifacts():
    return {
        "intelligence": {"options": [{"id": "persisted-option"}]},
        "decision_intelligence": {"recommended_decision": "persisted-option"},
        "thresholds_applied": [{"id": "old-threshold"}],
        "alert_state": {"status": "active"},
        "control_state": {"status": "blocked"},
    }


def _state(item, *, fingerprint=None, policy_version=None, technical=False):
    metadata = {
        **item,
        **_artifacts(),
        CURRENT_ELIGIBILITY_FINGERPRINT_KEY: fingerprint,
        ELIGIBILITY_POLICY_VERSION_KEY: policy_version,
    }
    if technical:
        metadata.update(item_kind="source_state", data_status="missing")
    return {
        "item_id": item["id"],
        "item_kind": "source_state" if technical else item["kind"],
        "source_dataset": item["source_dataset"],
        "impact_estimate": 999,
        "priority_score": 100,
        "metadata": metadata,
    }


def _overlay(item, state):
    return overlay_business_state(
        [item],
        {item["id"]: state},
        item_statuses={"open", "in_review"},
        projector=lambda value, **_kwargs: value,
        sort_key=lambda value: value["id"],
    )[0]


def _assert_no_persisted_artifacts(item):
    assert item.get("impact_estimate") is None
    assert item.get("priority_score") is None
    assert item.get("thresholds_applied") == []
    assert item.get("alert_state") is None
    assert item.get("control_state") is None
    assert item.get("decision_intelligence") == {}
    assert item.get("intelligence") == {}


def test_technical_state_cannot_overlay_artifacts_on_live_business_item():
    item = _item()
    state = _state(
        item,
        fingerprint=business_observation_fingerprint(item),
        policy_version=ELIGIBILITY_POLICY_VERSION,
        technical=True,
    )

    _assert_no_persisted_artifacts(_overlay(item, state))


def test_different_observation_fingerprint_cannot_overlay_artifacts():
    item = _item()
    old = _item(observed_value=1)
    state = _state(
        item,
        fingerprint=business_observation_fingerprint(old),
        policy_version=ELIGIBILITY_POLICY_VERSION,
    )

    _assert_no_persisted_artifacts(_overlay(item, state))


def test_matching_business_observation_preserves_legitimate_artifacts():
    item = _item()
    state = _state(
        item,
        fingerprint=business_observation_fingerprint(item),
        policy_version=ELIGIBILITY_POLICY_VERSION,
    )

    projected = _overlay(item, state)

    assert projected["impact_estimate"] == 999
    assert projected["priority_score"] == 100
    assert projected["intelligence"]["options"][0]["id"] == "persisted-option"


def test_stale_same_observation_preserves_legitimate_artifacts():
    item = _item(data_status="stale")
    state = _state(
        item,
        fingerprint=business_observation_fingerprint(item),
        policy_version=ELIGIBILITY_POLICY_VERSION,
    )

    assert _overlay(item, state)["impact_estimate"] == 999


def test_overlay_is_repeatable_and_does_not_mutate_persisted_state():
    item = _item()
    state = _state(item, fingerprint="wrong", policy_version="old")
    original = deepcopy(state)

    first = _overlay(item, state)
    second = _overlay(item, state)

    assert first == second
    assert state == original


def _persisted_row(item, metadata, *, workflow=False):
    persisted_metadata = {**item, **metadata}
    persisted_metadata.pop("cartridge", None)
    persisted_metadata.pop("cartridge_id", None)
    row = {
        "item_id": item["id"],
        "item_kind": item["kind"],
        "cartridge_id": item.get("cartridge") or item.get("cartridge_id"),
        "source_dataset": item["source_dataset"],
        "status": "open",
        "impact_estimate": 999,
        "priority_score": 100,
        "metadata": persisted_metadata,
    }
    if workflow:
        row.update(
            status="in_review",
            decision_id=42,
            selected_option_id="persisted-option",
            execution_status="executed",
        )
    return row


def _project_persisted(row):
    return persisted_business_item(
        row,
        expected_item_id="business-1",
        item_statuses={"open", "in_review"},
        severity_weights={"medium": 2},
    )


def test_persisted_item_without_policy_proof_drops_business_artifacts():
    projected = _project_persisted(_persisted_row(_item(), _artifacts(), workflow=True))

    _assert_no_persisted_artifacts(projected)
    assert projected["status"] == "open"
    assert projected["decision_id"] is None
    assert projected["selected_option_id"] is None
    assert projected["execution_status"] == "not_started"
    assert "persisted-option" not in repr(projected.get("metadata"))


def test_persisted_item_with_exact_policy_proof_keeps_business_artifacts():
    item = _item()
    metadata = {
        **_artifacts(),
        CURRENT_ELIGIBILITY_FINGERPRINT_KEY: business_observation_fingerprint(item),
        ELIGIBILITY_POLICY_VERSION_KEY: ELIGIBILITY_POLICY_VERSION,
    }

    projected = _project_persisted(_persisted_row(item, metadata))

    assert projected["impact_estimate"] == 999
    assert projected["intelligence"]["options"][0]["id"] == "persisted-option"
