from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_council_view import (
    system_suggestion_match,
    system_suggestions,
)
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
    observation_instant,
)
from app.services.control_room.business_item_persistence import _prepared_rows
from app.services.control_room.business_mutation_guard import (
    lock_authoritative_business_item,
)
from app.services.control_room.business_observation_order import (
    OBSERVATION_ORDER_KEY,
    business_observation_order,
)
from app.services.control_room.business_state_rows import state_rows
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
    workflow_has_eligible_provenance,
)
from app.services.control_room.business_workflow_quarantine import (
    quarantine_workflow_metadata,
    workflow_is_quarantined,
)
from app.services.control_room.business_workflow_reconciliation import (
    workflow_metadata_patches,
)


TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
USER = {
    "id": 7,
    "role": "user",
    "tenant_id": TENANT,
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
    "workspace_role": "workspace_admin",
    "allowed_cartridges": ["sap_successfactors"],
}
SIGNAL_ROW = {
    "signal_id": "talent_role_requirements_missing",
    "signal_type": "pipeline",
    "severity": "medium",
    "affected_count": 42,
    "title": "Requisitos de rol pendientes",
    "recommendation": "Validar Position y entidades de skills.",
    "status": "recommendation_only",
    "readiness_status": "partial",
    "source_row_count": 10,
    "blockers": "[]",
    "tenant_id": TENANT,
    "workspace_id": WORKSPACE,
}
FIRST_RUN = "2026-10-01T03:00:00Z"
SECOND_RUN = "2026-10-02T03:00:00Z"
THIRD_RUN = "2026-10-03T03:00:00Z"
NEXT_MONTH_RUN = "2026-11-03T03:00:00Z"
PERIOD_ITEM = {
    "id": "revenue-1",
    "kind": "anomaly",
    "cartridge": "sap_s4hana",
    "source_dataset": "revenue_by_customer",
    "source_system": "sap_s4hana",
    "metric_type": "amount",
    "observed_value": -1250.5,
    "population_count": 12,
    "detected_at": "2026-09",
    "evidence_refs": [
        {
            "type": "dataset_row",
            "source_dataset": "revenue_by_customer",
            "observed_at": "2026-09",
            "business_binding": {
                "version": "business-observation-v1",
                "identity": {"field": "id", "value": "revenue-1"},
                "observation": {"value": "-1250.5", "observed_at": "2026-09"},
                "fingerprint": "f" * 64,
            },
            "server_attestation": "sig",
        }
    ],
}
SNAPSHOT_ITEM = {
    "id": "snapshot-1",
    "kind": "intelligence_signal",
    "cartridge": "sap_successfactors",
    "source_dataset": "sap_successfactors_talent_signals",
    "metric_type": "count",
    "affected_count": 7,
    "population_count": 30,
    "detected_at": "2026-09-30",
    "generated_at": "2026-10-02T03:00:00Z",
    "details": {"materialized_at": "2026-10-02T03:00:00Z"},
    "intelligence": {
        "evidence_pack": {
            "summary": "7 afectados",
            "observed_on": "2026-09-30",
            "items": [{"source_ref": "gold:snapshot-1", "strength": 0.7}],
        }
    },
}


def _source() -> Any:
    return next(
        source
        for source in control_room_service._all_sources()
        if source.dataset == "sap_successfactors_talent_signals"
    )


def _talent_item(run: str, **updates: Any) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    control_room_service._append_normalized_source_rows(
        items,
        _source(),
        [{**SIGNAL_ROW, "generated_at": run, "materialized_at": run, **updates}],
        {},
        USER,
    )
    (item,) = items
    return item


def _persisted(item: dict[str, Any]) -> dict[str, Any]:
    rows = state_rows(
        [item],
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        owner_user_id=USER["id"],
        impact_builder=control_room_service._impact_for_item,
        metadata_builder=control_room_service._metadata_for_item,
        diagnostic_builder=control_room_service._diagnostic_metadata,
    )
    (row,) = _prepared_rows(rows)
    return row


def _row_item(row: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(row["metadata"])
    return {
        **metadata,
        **row,
        "id": row["item_id"],
        "kind": row["item_kind"],
        "metadata": metadata,
    }


def _approved_row(item: dict[str, Any], decision_id: int) -> dict[str, Any]:
    row = _persisted(item)
    provenance = workflow_eligibility_provenance(
        {**item, "workspace_id": WORKSPACE},
        stage=WorkflowStage.EXECUTED,
        workspace_id=WORKSPACE,
        decision_id=decision_id,
    )
    return {
        **row,
        "status": "approved",
        "decision_id": decision_id,
        "execution_status": "executed",
        "selected_option_id": None,
        "metadata": {**row["metadata"], DECISION_PROVENANCE_KEY: provenance},
    }


class _Connection:
    def __init__(self, existing: dict[str, Any]) -> None:
        self.existing = existing

    async def fetch(self, *_args: Any) -> list[dict[str, Any]]:
        return [self.existing]


def test_identical_rematerialization_keeps_the_talent_identity():
    first = _talent_item(FIRST_RUN)
    second = _talent_item(SECOND_RUN)

    assert classify_business_item(second).eligible
    assert business_observation_fingerprint(first) == business_observation_fingerprint(
        second
    )
    assert business_observation_fingerprint(
        _row_item(_persisted(first))
    ) == business_observation_fingerprint(_row_item(_persisted(second)))
    assert business_observation_fingerprint(first) == business_observation_fingerprint(
        _row_item(_persisted(first))
    )


def test_rematerialization_still_advances_freshness_and_order():
    first_row = _persisted(_talent_item(FIRST_RUN))
    second_row = _persisted(_talent_item(SECOND_RUN))

    assert second_row["metadata"]["detected_at"] == SECOND_RUN
    assert second_row["metadata"]["details"]["generated_at"] == SECOND_RUN
    assert second_row["metadata"]["details"]["materialized_at"] == SECOND_RUN
    assert (
        first_row["metadata"][OBSERVATION_ORDER_KEY]
        < second_row["metadata"][OBSERVATION_ORDER_KEY]
    )
    assert _talent_item(SECOND_RUN)["freshness_at"] == SECOND_RUN


def test_order_supersedes_orders_stamped_before_the_identity_change():
    row = _persisted(_talent_item(FIRST_RUN))
    instant = observation_instant(FIRST_RUN)

    assert row["metadata"][OBSERVATION_ORDER_KEY] > f"0001|{instant}|{'f' * 64}"


@pytest.mark.parametrize(
    "change",
    [
        {"affected_count": 43},
        {"source_row_count": 11},
        {"signal_id": "talent_mobility_observed"},
    ],
)
def test_changed_business_values_change_the_talent_identity(change):
    first = _talent_item(FIRST_RUN)
    changed = _talent_item(SECOND_RUN, **change)

    assert business_observation_fingerprint(first) != business_observation_fingerprint(
        changed
    )


def test_business_period_that_differs_from_materialization_stays_in_identity():
    rerun = {
        **SNAPSHOT_ITEM,
        "generated_at": "2026-10-03T03:00:00Z",
        "details": {"materialized_at": "2026-10-03T03:00:00Z"},
    }

    assert business_observation_fingerprint(rerun) == (
        business_observation_fingerprint(SNAPSHOT_ITEM)
    )
    assert business_observation_fingerprint(
        {**rerun, "detected_at": "2026-10-01"}
    ) != business_observation_fingerprint(SNAPSHOT_ITEM)
    assert business_observation_fingerprint(
        {**SNAPSHOT_ITEM, "detected_at": "2026-10-02T03:00:00Z"}
    ) != business_observation_fingerprint(SNAPSHOT_ITEM)


def test_observation_instants_without_materialization_keep_their_identity():
    assert business_observation_fingerprint(PERIOD_ITEM) == (
        "e1df37a744b545d9d5def6cdf20c163c326dfbc124df0dbc107244e7139e958c"
    )
    assert business_observation_fingerprint(SNAPSHOT_ITEM) == (
        "dee8843aab948432efe26cb9ebc482fad8e4399398bef58f486ed05b0c09a010"
    )
    assert business_observation_fingerprint(
        {**PERIOD_ITEM, "detected_at": "2026-10"}
    ) != business_observation_fingerprint(PERIOD_ITEM)


def test_only_restated_materialization_instants_are_masked():
    run = "2026-10-02T03:00:00Z"
    snapshot = {
        **PERIOD_ITEM,
        "detected_at": run,
        "generated_at": run,
        "evidence_refs": [
            {
                **PERIOD_ITEM["evidence_refs"][0],
                "observed_at": run,
                "business_binding": {
                    **PERIOD_ITEM["evidence_refs"][0]["business_binding"],
                    "observation": {"value": "-1250.5", "observed_at": run},
                },
            }
        ],
    }
    rerun = "2026-10-03T03:00:00Z"
    rerun_snapshot = json.loads(json.dumps(snapshot).replace(run, rerun))
    rerun_snapshot["evidence_refs"][0]["business_binding"]["fingerprint"] = "a" * 64

    assert business_observation_fingerprint(snapshot) == (
        business_observation_fingerprint(rerun_snapshot)
    )
    changed_value = json.loads(json.dumps(rerun_snapshot))
    changed_value["evidence_refs"][0]["business_binding"]["observation"]["value"] = "-9"
    assert business_observation_fingerprint(changed_value) != (
        business_observation_fingerprint(snapshot)
    )
    assert business_observation_fingerprint(
        {**snapshot, "observed_value": -9}
    ) != business_observation_fingerprint(snapshot)
    period_binding = json.loads(json.dumps(PERIOD_ITEM))
    period_binding["evidence_refs"][0]["business_binding"]["fingerprint"] = "a" * 64
    assert business_observation_fingerprint(period_binding) != (
        business_observation_fingerprint(PERIOD_ITEM)
    )


def test_numbers_are_never_read_as_materialization_instants():
    item = {**SNAPSHOT_ITEM, "generated_at": "2026-10-02", "affected_count": 20261002}

    assert business_observation_fingerprint(item) != business_observation_fingerprint(
        {**item, "affected_count": 20261003}
    )


def test_observation_instant_normalizes_supported_shapes():
    assert observation_instant("2026-10-02T03:00:00Z") == "2026-10-02T03:00:00.000000Z"
    assert observation_instant("2026-10-01T22:00:00-05:00") == (
        "2026-10-02T03:00:00.000000Z"
    )
    assert observation_instant(datetime(2026, 10, 2, 3)) == (
        "2026-10-02T03:00:00.000000Z"
    )
    assert observation_instant(datetime(2026, 10, 2, 3, tzinfo=UTC)) == (
        "2026-10-02T03:00:00.000000Z"
    )
    assert observation_instant(date(2026, 10, 2)) == "2026-10-02T00:00:00.000000Z"
    assert observation_instant("2026-10-02") == "2026-10-02T00:00:00.000000Z"
    assert observation_instant("not a date") == ""
    assert observation_instant(None) == ""


@pytest.mark.asyncio
async def test_approved_talent_signal_keeps_its_decision_after_identical_rematerialization():
    existing = _approved_row(_talent_item(FIRST_RUN), 31)
    incoming = _persisted(_talent_item(SECOND_RUN))

    patches = await workflow_metadata_patches(_Connection(existing), [incoming])

    patch = patches[(WORKSPACE, existing["item_id"])]
    assert WORKFLOW_QUARANTINE_KEY not in patch
    assert (
        patch[DECISION_PROVENANCE_KEY]
        == (existing["metadata"][DECISION_PROVENANCE_KEY])
    )
    assert workflow_has_eligible_provenance(
        existing["metadata"],
        {**_row_item(incoming), "selected_option_id": None},
        decision_id=31,
    )
    assert business_observation_order(_row_item(incoming)) > (
        business_observation_order(_row_item(existing))
    )


@pytest.mark.asyncio
async def test_approved_talent_signal_is_quarantined_when_the_business_value_changes():
    existing = _approved_row(_talent_item(FIRST_RUN), 31)
    incoming = _persisted(_talent_item(SECOND_RUN, affected_count=43))

    patches = await workflow_metadata_patches(_Connection(existing), [incoming])

    patch = patches[(WORKSPACE, existing["item_id"])]
    assert DECISION_PROVENANCE_KEY not in patch
    (generation,) = patch[WORKFLOW_QUARANTINE_KEY]["generations"]
    assert generation["reason"] == "observation_changed"


def _open_row(row: dict[str, Any], **metadata: Any) -> dict[str, Any]:
    return {
        **row,
        "status": "open",
        "decision_id": None,
        "execution_status": "not_started",
        "metadata": {**row["metadata"], **metadata},
    }


def _raw(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "metadata": json.dumps(row["metadata"], default=str)}


class _GuardConnection:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    async def fetchrow(self, *_args: Any) -> dict[str, Any]:
        return json.loads(json.dumps(self.row, default=str))


@pytest.mark.asyncio
async def test_value_returning_to_a_quarantined_observation_stays_actionable():
    approved = _approved_row(_talent_item(FIRST_RUN), 31)
    changed = _persisted(_talent_item(SECOND_RUN, affected_count=43))
    key = (WORKSPACE, approved["item_id"])
    patch = (await workflow_metadata_patches(_Connection(approved), [changed]))[key]
    (generation,) = patch[WORKFLOW_QUARANTINE_KEY]["generations"]
    assert generation["fingerprint"] == business_observation_fingerprint(
        _talent_item(FIRST_RUN)
    )
    assert generation["observation_order"] == business_observation_order(
        _row_item(approved)
    )
    changed_row = _open_row(changed, **patch)
    live = _talent_item(THIRD_RUN)
    reverted = _persisted(live)
    carried = (await workflow_metadata_patches(_Connection(changed_row), [reverted]))[
        key
    ]
    reverted_row = _open_row(reverted, **carried)

    assert carried[WORKFLOW_QUARANTINE_KEY] == patch[WORKFLOW_QUARANTINE_KEY]
    assert business_observation_fingerprint(live) == generation["fingerprint"]
    assert not workflow_is_quarantined(_row_item(reverted_row))
    assert not workflow_is_quarantined(_raw(reverted_row))
    assert not workflow_is_quarantined({**live, "metadata": reverted_row["metadata"]})
    assert system_suggestion_match(
        live, _raw(reverted_row), tenant_id=TENANT, workspace_id=WORKSPACE
    )
    locked = await lock_authoritative_business_item(
        _GuardConnection(reverted_row), user=USER, item=live
    )
    assert locked["item_id"] == approved["item_id"]


@pytest.mark.asyncio
async def test_quarantine_keeps_blocking_the_materialization_it_was_raised_on():
    live = _talent_item(FIRST_RUN)
    row = _persisted(live)
    metadata = quarantine_workflow_metadata(
        row["metadata"],
        item=_row_item(row),
        decision_id=31,
        stage="executed",
        reason="workflow_not_business_eligible",
    )
    quarantined = _open_row(row, **metadata)

    assert workflow_is_quarantined(_row_item(quarantined))
    assert workflow_is_quarantined(quarantined)
    assert workflow_is_quarantined(_raw(quarantined))
    assert workflow_is_quarantined({**live, "metadata": metadata})
    assert (
        system_suggestion_match(
            live, _raw(quarantined), tenant_id=TENANT, workspace_id=WORKSPACE
        )
        is None
    )
    with pytest.raises(HTTPException) as blocked:
        await lock_authoritative_business_item(
            _GuardConnection(quarantined), user=USER, item=live
        )
    assert blocked.value.status_code == 409

    rerun = _persisted(_talent_item(SECOND_RUN))
    later = _open_row(
        rerun, **{WORKFLOW_QUARANTINE_KEY: metadata[WORKFLOW_QUARANTINE_KEY]}
    )
    assert not workflow_is_quarantined(_raw(later))

    (generation,) = metadata[WORKFLOW_QUARANTINE_KEY]["generations"]
    legacy = {
        key: value for key, value in generation.items() if key != "observation_order"
    }
    unordered = _open_row(
        rerun,
        **{WORKFLOW_QUARANTINE_KEY: {"policy_version": "v2", "generations": [legacy]}},
    )
    assert workflow_is_quarantined(_raw(unordered))


def test_pending_system_proposal_keeps_its_id_across_identical_rematerialization():
    def suggestion(item: dict[str, Any]) -> Any:
        row = {"status": "open", "decision_id": None, **_persisted(item)}
        (match,) = system_suggestions(
            [item],
            {item["id"]: row},
            user=USER,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
        )
        return match

    first = suggestion(_talent_item(FIRST_RUN))
    rerun = suggestion(_talent_item(SECOND_RUN))
    changed = suggestion(_talent_item(SECOND_RUN, affected_count=43))

    assert first.evidence_digest != rerun.evidence_digest
    assert first.handle == rerun.handle
    assert changed.handle != first.handle


def test_talent_identity_follows_severity_and_observation_month():
    first = _talent_item(FIRST_RUN)
    pack = first["intelligence"]["evidence_pack"]

    assert pack["observation_period"] == "2026-10"
    assert pack["severity"] == "medium"
    assert business_observation_fingerprint(
        _talent_item(SECOND_RUN, severity="critical")
    ) != business_observation_fingerprint(first)
    next_month = _talent_item(NEXT_MONTH_RUN)
    assert business_observation_fingerprint(next_month) != (
        business_observation_fingerprint(first)
    )
    assert business_observation_fingerprint(
        _row_item(_persisted(next_month))
    ) == business_observation_fingerprint(next_month)


@pytest.mark.asyncio
async def test_approval_does_not_carry_into_the_next_observation_month():
    existing = _approved_row(_talent_item(FIRST_RUN), 31)
    incoming = _persisted(_talent_item(NEXT_MONTH_RUN))

    patch = (await workflow_metadata_patches(_Connection(existing), [incoming]))[
        (WORKSPACE, existing["item_id"])
    ]

    assert DECISION_PROVENANCE_KEY not in patch
    (generation,) = patch[WORKFLOW_QUARANTINE_KEY]["generations"]
    assert generation["reason"] == "observation_changed"


def test_business_periods_are_never_masked_as_materialization_instants():
    daily = {
        "id": "daily-1",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "source_dataset": "gold_people",
        "affected_count": 3,
        "observation_date": "2026-10-02",
        "generated_at": "2026-10-02",
    }
    next_day = {**daily, "observation_date": "2026-10-03", "generated_at": "2026-10-03"}
    assert business_observation_fingerprint(daily) != (
        business_observation_fingerprint(next_day)
    )
    for field in ("as_of", "observation_date", "period_key"):
        nested = {
            **daily,
            "observation_date": None,
            "evidence_pack": {field: "2026-10-02", "summary": "3 afectados"},
        }
        nested_next = {
            **nested,
            "generated_at": "2026-10-03",
            "evidence_pack": {field: "2026-10-03", "summary": "3 afectados"},
        }
        assert business_observation_fingerprint(nested) != (
            business_observation_fingerprint(nested_next)
        )
    stamped = {**daily, "observation_date": None, "detected_at": "2026-10-02"}
    assert business_observation_fingerprint(
        stamped
    ) == business_observation_fingerprint(
        {**stamped, "detected_at": "2026-10-03", "generated_at": "2026-10-03"}
    )
