from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)
from app.services.control_room.business_projection import (
    filter_business_items,
    project_business_item,
)


USER = {
    "id": 7,
    "role": "super_admin",
    "active_tenant_id": "tenant-A",
    "active_workspace_id": "workspace-A",
}


def _persisted(kind: str, item_id: str) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "cartridge": "platform",
        "source_dataset": "gold_metrics",
        "observation_date": "2026-07-20",
        "metric_type": "scalar",
        "observed_value": 1,
        "evidence_refs": [f"gold_metrics:{item_id}"],
    }


@pytest.mark.asyncio
async def test_dashboard_merge_does_not_revive_ordinary_historical_items():
    current = [
        {
            "id": "current",
            "kind": "anomaly",
            "source_dataset": "gold_metrics",
            "observation_date": "2026-07-20",
            "metric_type": "scalar",
            "observed_value": 1,
            "evidence_refs": ["gold_metrics:current"],
        }
    ]
    persisted = [
        _persisted("anomaly", "historical-anomaly"),
        _persisted("control_item", "historical-control"),
        _persisted("intelligence_signal", "signal-1"),
        _persisted("agent_alert", "alert-1"),
    ]
    with patch.object(
        control_room_service,
        "_persisted_business_items",
        new=AsyncMock(return_value=persisted),
    ):
        result = await control_room_service._dashboard_items_with_persisted(
            USER,
            current,
            set(),
        )

    assert {item["id"] for item in result} == {"current", "signal-1"}


class QueryRecordingPool:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def execute(self, _sql: str, *_args):
        return None

    async def fetch(self, sql: str, *_args):
        self.queries.append(" ".join(sql.split()))
        return []


@pytest.mark.asyncio
async def test_persisted_dashboard_loader_is_kind_scoped_and_bounded():
    pool = QueryRecordingPool()
    with patch.object(
        control_room_service.auth,
        "pool",
        new=AsyncMock(return_value=pool),
    ):
        assert await control_room_service._persisted_business_items(USER) == []

    loader = next(query for query in pool.queries if "FROM control_room_items" in query)
    assert "workspace_id = $1" in loader
    assert "item_kind = ANY($2::text[])" in loader
    assert "tenant_id::text = $3" in loader
    assert "ORDER BY COALESCE(priority_score, 0) DESC" in loader
    assert "item_id DESC LIMIT $4" in loader


def test_dataset_item_preserves_nested_policy_metadata_and_item_kind():
    source = control_room_service.ControlRoomSource(
        dataset="gold_metrics",
        cartridge="sap_hcm",
        domain="Recursos Humanos",
        module_label="Personal",
        entity_kind="Empleado",
        entity_id_field="employee_id",
        entity_label_field="employee_name",
    )
    item = control_room_service._base_item(
        source,
        {
            "item_kind": "anomaly",
            "metadata": {
                "item_kind": "source_state",
                "details": {"source_status": "missing"},
            },
        },
        "metric",
        "1001",
        "Ana",
    )

    assert item["metadata"]["details"]["source_status"] == "missing"
    assert classify_business_item(item).reason is EligibilityReason.SOURCE_STATE
    nested_only = {
        **item,
        "item_kind": "anomaly",
        "metadata": {"details": {"source_status": "missing"}},
    }
    assert (
        classify_business_item(nested_only).reason is EligibilityReason.TECHNICAL_STATE
    )


def test_policy_metadata_survives_persistence_round_trip():
    item = {
        "id": "diagnostic",
        "kind": "anomaly",
        "metadata": {
            "item_kind": "source_state",
            "details": {"source_status": "missing", "observed_value": None},
        },
    }

    metadata = control_room_service._diagnostic_metadata(item)
    restored = control_room_service._persisted_intelligence_payload(
        {
            "item_id": item["id"],
            "item_kind": item["kind"],
            "source_dataset": "gold_metrics",
            "metadata": metadata,
        }
    )

    assert metadata["item_kind"] == "source_state"
    assert metadata["details"]["observed_value"] is None
    assert classify_business_item(restored).reason is EligibilityReason.SOURCE_STATE


def test_validated_parent_context_survives_dashboard_reprojection():
    child = project_business_item(
        {
            "id": "child",
            "kind": "intelligence_signal",
            "parent_item_id": "parent",
            "source_dataset": "gold_metrics",
            "observation_date": "2026-07-20",
            "metric_type": "scalar",
            "observed_value": 1,
            "evidence_refs": ["gold_metrics:child"],
        },
        eligible_parent_ids={"parent"},
    )

    assert filter_business_items([child]) == [dict(child)]


def test_physical_parent_context_survives_a_second_projection():
    parent = {
        "id": "parent",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "observation_date": "2026-07-20",
        "metric_type": "scalar",
        "observed_value": 1,
        "evidence_refs": ["gold_metrics:parent"],
    }
    child = {
        "id": "child",
        "kind": "intelligence_signal",
        "parent_item_id": "parent",
        "source_dataset": "gold_metrics",
        "observation_date": "2026-07-20",
        "metric_type": "scalar",
        "observed_value": 1,
        "evidence_refs": ["gold_metrics:child"],
    }

    first = filter_business_items([parent, child])
    projected_child = next(item for item in first if item["id"] == "child")

    assert filter_business_items([projected_child]) == [dict(projected_child)]


@pytest.mark.asyncio
async def test_lessons_exclude_ineligible_historical_parent():
    lessons = [
        {"id": 1, "item_id": "source-state-1", "rule": "technical"},
        {"id": 2, "item_id": "business-1", "rule": "business"},
    ]
    business_item = {
        "id": "business-1",
        "kind": "anomaly",
        "source_dataset": "employees_anomalies",
        "metric_type": "scalar",
        "observed_value": 1,
        "observation_date": "2026-07-16",
        "evidence_refs": ["employees_anomalies:business-1"],
    }
    with (
        patch.object(
            control_room_service,
            "_load_lesson_rows",
            new=AsyncMock(return_value=lessons),
        ),
        patch.object(
            control_room_service,
            "_persisted_business_items",
            new=AsyncMock(return_value=[business_item]),
        ),
    ):
        result = await control_room_service.list_lessons(USER)

    assert [lesson["id"] for lesson in result["lessons"]] == [2]
    assert result["summary"]["total"] == 1
