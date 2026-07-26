from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_explicit_action_binding import (
    attach_explicit_action_binding,
)
from console.tests.control_room_execution_helpers import runtime_evidenced_item
from console.tests.test_control_room_talent_preview_guard import USER, _talent_item


def _platform_item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "cartridge": "platform",
        "status": "open",
        "execution_status": "not_started",
        "source_dataset": "gold_people",
        "source_system": "test",
        "entity_id": "employee-1",
        "metric_type": "count",
        "observed_value": 1,
        "population_count": 10,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_people:item-1"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ({"status": "resolved"}, "not actionable"),
        ({"data_status": "stale"}, "data is stale"),
        ({"entity_id": ""}, "invalid_observation"),
    ],
)
async def test_talent_preview_rejects_invalid_current_state_before_catalog(
    changes: dict[str, str], detail: str
) -> None:
    item = {**_talent_item(), **changes}
    catalog = AsyncMock()
    with (
        patch.object(
            control_room_service, "_item_for_mutation", AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service, "query_dataset_rows", AsyncMock(return_value=[])
        ),
        patch.object(
            control_room_service,
            "require_enabled_action_template_for_user",
            catalog,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.sap_successfactors_talent_action_preview(
                USER,
                {
                    "action_id": item["id"],
                    "template_id": "prepare_successfactors_review",
                    "binding_id": "a" * 64,
                },
            )

    assert exc.value.status_code == 409
    assert detail in str(exc.value.detail)
    catalog.assert_not_awaited()


@pytest.mark.asyncio
async def test_talent_preview_rechecks_catalog_after_explicit_binding() -> None:
    item = attach_explicit_action_binding(
        _talent_item(), template_id="prepare_successfactors_review"
    )
    binding = item["metadata"]["explicit_action_bindings"][0]
    catalog = AsyncMock(side_effect=HTTPException(404, "action template not found"))
    with (
        patch.object(
            control_room_service, "_item_for_mutation", AsyncMock(return_value=item)
        ),
        patch.object(
            control_room_service, "query_dataset_rows", AsyncMock(return_value=[])
        ),
        patch.object(
            control_room_service,
            "require_enabled_action_template_for_user",
            catalog,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.sap_successfactors_talent_action_preview(
                USER,
                {
                    "action_id": item["id"],
                    "template_id": binding["template_id"],
                    "binding_id": binding["binding_id"],
                },
            )

    assert exc.value.status_code == 404
    catalog.assert_awaited_once_with(USER, binding["template_id"])


@pytest.mark.asyncio
async def test_auto_run_disabled_template_stops_before_first_mutation() -> None:
    item = attach_explicit_action_binding(
        runtime_evidenced_item(_platform_item()), template_id="create_followup_task"
    )
    guard = AsyncMock(side_effect=HTTPException(404, "action template not found"))
    mutation_names = (
        "record_item_step",
        "select_item_option",
        "create_decision_for_item",
        "action_preview",
        "action_dry_run",
    )
    mutations = {name: AsyncMock() for name in mutation_names}
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                control_room_service,
                "_item_for_mutation",
                AsyncMock(return_value=item),
            )
        )
        stack.enter_context(
            patch.object(
                control_room_service,
                "require_enabled_action_template_for_user",
                guard,
            )
        )
        for name, value in mutations.items():
            stack.enter_context(patch.object(control_room_service, name, value))
        with pytest.raises(HTTPException) as exc:
            await control_room_service.run_auto_item(item["id"], USER)

    assert exc.value.status_code == 404
    guard.assert_awaited_once_with(USER, "create_followup_task")
    for mutation in mutations.values():
        mutation.assert_not_awaited()
