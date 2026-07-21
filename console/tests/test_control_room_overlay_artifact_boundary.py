from __future__ import annotations

import asyncio
from copy import deepcopy

from app.services.control_room.business_state_overlay import (
    load_overlay_state,
    overlay_business_state,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    ELIGIBILITY_POLICY_VERSION,
    business_observation_fingerprint,
)


ARTIFACT_KEYS = (
    "alert_state",
    "control_state",
    "decision_intelligence",
    "impact_estimate",
    "impact_currency",
    "intelligence",
    "priority_score",
    "threshold_state",
    "thresholds_applied",
)


def _item(**overrides):
    return {
        "id": "business-1",
        "kind": "anomaly",
        "status": "open",
        "source_dataset": "gold_metrics",
        "metric_type": "scalar",
        "observed_value": 2,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_metrics:record-1"],
        **overrides,
    }


def _state_for(item, **metadata_overrides):
    metadata = {
        "item_kind": item["kind"],
        "source_dataset": item["source_dataset"],
        "metric_type": item["metric_type"],
        "observed_value": item["observed_value"],
        "observation_date": item["observation_date"],
        "evidence_refs": item["evidence_refs"],
        CURRENT_ELIGIBILITY_FINGERPRINT_KEY: business_observation_fingerprint(item),
        "eligibility_policy_version": ELIGIBILITY_POLICY_VERSION,
        "intelligence": {"options": [{"id": "old-option"}]},
        "decision_intelligence": {"score": 99},
        "thresholds_applied": [{"id": "old-threshold"}],
        "threshold_state": "triggered",
        "alert_state": {"status": "open"},
        "control_state": {"control-1": {"status": "failed"}},
        **metadata_overrides,
    }
    return {
        "status": "open",
        "impact_estimate": 900,
        "impact_currency": "USD",
        "confidence": 0.99,
        "priority_score": 100,
        "metadata": metadata,
    }


def _overlay(item, state):
    return overlay_business_state(
        [item],
        {item["id"]: state},
        item_statuses={"open", "decision_created", "approved"},
        projector=lambda value, **_kwargs: value,
        sort_key=lambda value: str(value.get("id")),
    )[0]


def _assert_no_persisted_artifacts(projected):
    assert projected["impact_estimate"] is None
    assert projected["impact_currency"] is None
    assert projected["priority_score"] is None
    assert projected["confidence"] is None
    assert projected["thresholds_applied"] == []
    assert projected["threshold_state"] == "default"
    assert projected["alert_state"] is None
    assert projected["control_state"] is None
    assert projected["decision_intelligence"] == {}
    assert projected["intelligence"] == {}


def test_technical_persisted_state_does_not_revive_business_artifacts():
    item = _item()
    state = _state_for(
        item,
        item_kind="source_state",
        data_status="missing",
    )

    projected = _overlay(item, state)

    _assert_no_persisted_artifacts(projected)


def test_different_observation_fingerprint_does_not_overlay_artifacts():
    prior = _item(observed_value=1)
    current = _item(observed_value=2)

    projected = _overlay(current, _state_for(prior))

    _assert_no_persisted_artifacts(projected)


def test_matching_observation_and_policy_preserve_legitimate_artifacts():
    item = _item()

    projected = _overlay(item, _state_for(item))

    assert projected["impact_estimate"] == 900
    assert projected["impact_currency"] == "USD"
    assert projected["priority_score"] == 100
    assert projected["threshold_state"] == "triggered"
    assert projected["alert_state"] == {"status": "open"}
    assert projected["control_state"] == {"control-1": {"status": "failed"}}
    assert projected["decision_intelligence"] == {"score": 99}
    assert projected["intelligence"]["options"] == [{"id": "old-option"}]


def test_explicit_old_policy_version_does_not_overlay_artifacts():
    item = _item()

    projected = _overlay(
        item,
        _state_for(item, eligibility_policy_version="control-room-business-v1"),
    )

    _assert_no_persisted_artifacts(projected)


def test_stale_runtime_with_same_observation_preserves_artifacts():
    item = _item(data_status="stale")

    projected = _overlay(item, _state_for(item, data_status="stale"))

    assert projected["impact_estimate"] == 900
    assert projected["intelligence"]["options"] == [{"id": "old-option"}]


def test_repeated_projection_is_read_only_and_deterministic():
    item = _item()
    state = _state_for(item, item_kind="source_state", data_status="missing")
    item_before = deepcopy(item)
    state_before = deepcopy(state)

    first = _overlay(item, state)
    second = _overlay(item, state)

    assert first == second
    assert item == item_before
    assert state == state_before


def test_overlay_loader_keeps_persisted_kind_and_source_for_policy_checks():
    item = _item()

    class Connection:
        sql = ""

        async def fetch(self, sql, *_args):
            self.sql = sql
            state = _state_for(item)
            metadata = dict(state["metadata"])
            metadata.pop("item_kind")
            return [
                {
                    "item_id": item["id"],
                    "item_kind": "source_state",
                    "source_dataset": item["source_dataset"],
                    **state,
                    "metadata": metadata,
                }
            ]

    conn = Connection()

    loaded = asyncio.run(
        load_overlay_state(
            conn,
            workspace_id="workspace-1",
            item_ids=["business-1"],
            tenant_id="tenant-1",
            owner_id=7,
        )
    )

    projection = conn.sql.partition("FROM control_room_items")[0]
    assert "item_kind" in projection
    assert "source_dataset" in projection
    _assert_no_persisted_artifacts(_overlay(item, loaded[item["id"]]))
