from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room import business_authoritative_execution
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from console.tests.control_room_execution_helpers import (
    USER,
    authoritative_item_row,
    explicit_action,
)
from control_room_surface_fixtures import business_item


TEMPLATE_ID = "request_owner_review"


def _snapshot() -> tuple[dict, str]:
    return explicit_action(
        business_item(tenant_id="tenant-A", workspace_id="workspace-A"),
        template_id=TEMPLATE_ID,
    )


def _mutation_guards() -> dict[str, AsyncMock]:
    return {
        name: AsyncMock(side_effect=AssertionError(f"{name} reached"))
        for name in (
            "_ensure_item_row",
            "_record_action_execution",
            "_record_action_run",
            "_set_execution_status",
            "_record_item_event",
        )
    }


@pytest.mark.asyncio
async def test_preview_rejects_authoritative_input_digest_divergence() -> None:
    snapshot, binding_id = _snapshot()
    locked = authoritative_item_row(snapshot)
    original_payload = control_room_service._execution_payload  # noqa: SLF001
    payload_calls = 0

    def divergent_payload(item, mode, template):
        nonlocal payload_calls
        payload_calls += 1
        payload = deepcopy(original_payload(item, mode, template))
        if payload_calls == 2:
            payload["guardrails"]["feature_flag"] = "changed-after-snapshot"
        return payload

    async def scoped(_pool, _user, work):
        return await work(object(), "tenant-A", "workspace-A")

    mutations = _mutation_guards()
    template_gate = AsyncMock(side_effect=AssertionError("template gate reached"))
    audit = AsyncMock(side_effect=AssertionError("audit reached"))
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(return_value=snapshot),
        ),
        patch.object(
            control_room_service.auth, "pool", AsyncMock(return_value=object())
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
        patch.object(
            business_authoritative_execution,
            "lock_authoritative_business_item",
            AsyncMock(return_value=locked),
        ),
        patch.object(control_room_service, "_execution_payload", new=divergent_payload),
        patch.object(
            control_room_service,
            "_require_authoritative_template_enabled",
            template_gate,
        ),
        patch.object(control_room_service.audit_service, "record_event", audit),
        patch.object(
            control_room_service,
            "_ensure_item_row",
            mutations["_ensure_item_row"],
        ),
        patch.object(
            control_room_service,
            "_record_action_execution",
            mutations["_record_action_execution"],
        ),
        patch.object(
            control_room_service,
            "_record_action_run",
            mutations["_record_action_run"],
        ),
        patch.object(
            control_room_service,
            "_set_execution_status",
            mutations["_set_execution_status"],
        ),
        patch.object(
            control_room_service,
            "_record_item_event",
            mutations["_record_item_event"],
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.action_preview(
                str(snapshot["id"]),
                USER,
                template_id=TEMPLATE_ID,
                binding_id=binding_id,
            )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_business_state_changed"
    assert payload_calls == 2
    template_gate.assert_not_awaited()
    audit.assert_not_awaited()
    for mutation in mutations.values():
        mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_rejects_authoritative_template_digest_divergence() -> None:
    snapshot, binding_id = _snapshot()
    locked = authoritative_item_row(snapshot)
    original_label = ACTION_TEMPLATES[TEMPLATE_ID]["label"]

    async def scoped(_pool, _user, work):
        ACTION_TEMPLATES[TEMPLATE_ID]["label"] = "Changed after handle resolution"
        try:
            return await work(object(), "tenant-A", "workspace-A")
        finally:
            ACTION_TEMPLATES[TEMPLATE_ID]["label"] = original_label

    mutations = _mutation_guards()
    template_gate = AsyncMock(side_effect=AssertionError("template gate reached"))
    audit = AsyncMock(side_effect=AssertionError("audit reached"))
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(return_value=snapshot),
        ),
        patch.object(
            control_room_service.auth, "pool", AsyncMock(return_value=object())
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
        patch.object(
            business_authoritative_execution,
            "lock_authoritative_business_item",
            AsyncMock(return_value=locked),
        ),
        patch.object(
            control_room_service,
            "_require_authoritative_template_enabled",
            template_gate,
        ),
        patch.object(control_room_service.audit_service, "record_event", audit),
        patch.object(
            control_room_service,
            "_ensure_item_row",
            mutations["_ensure_item_row"],
        ),
        patch.object(
            control_room_service,
            "_record_action_execution",
            mutations["_record_action_execution"],
        ),
        patch.object(
            control_room_service,
            "_record_action_run",
            mutations["_record_action_run"],
        ),
        patch.object(
            control_room_service,
            "_set_execution_status",
            mutations["_set_execution_status"],
        ),
        patch.object(
            control_room_service,
            "_record_item_event",
            mutations["_record_item_event"],
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.action_preview(
                str(snapshot["id"]),
                USER,
                template_id=TEMPLATE_ID,
                binding_id=binding_id,
            )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_business_state_changed"
    template_gate.assert_not_awaited()
    audit.assert_not_awaited()
    for mutation in mutations.values():
        mutation.assert_not_awaited()
