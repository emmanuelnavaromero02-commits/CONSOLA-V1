from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException

from app.routers import control_room as routes
from app.routers import control_room_surfaces as surfaces
from app.services import control_room_service
from app.services.control_room.business_action_catalog import (
    ENABLED_ACTION_TEMPLATE_IDS_SQL,
    load_enabled_action_template_ids,
)
from control_room_surface_fixtures import (
    OPERATOR,
    VIEWER,
    action_item,
    business_item,
    snapshot,
)


class ReadOnlyCatalogPool:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.queries: list[str] = []

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))

    async def fetch(self, sql: str, *args):
        self.queries.append(sql)
        return [
            {
                "template_id": "request_owner_review",
                "cartridge_id": "platform",
                "label": "Solicitar revision de owner",
                "requires_approval": True,
            },
            {
                "template_id": "fabricated_template",
                "cartridge_id": "platform",
                "label": "Fabricated",
                "requires_approval": True,
            },
        ]


@pytest.mark.asyncio
async def test_catalog_is_one_scoped_read_and_filters_unknown_templates():
    pool = ReadOnlyCatalogPool()
    with patch.object(
        control_room_service.auth, "pool", new=AsyncMock(return_value=pool)
    ):
        result = await load_enabled_action_template_ids(OPERATOR)

    assert result == frozenset({"request_owner_review"})
    assert len(pool.executed) == 1
    assert len(pool.queries) == 1
    assert pool.queries[0] == ENABLED_ACTION_TEMPLATE_IDS_SQL
    assert "WHERE enabled IS TRUE" in pool.queries[0]
    assert "template_id = ANY($1::text[])" in pool.queries[0]
    assert "LIMIT $2" in pool.queries[0]
    assert not any(
        keyword in pool.queries[0].upper()
        for keyword in ("INSERT ", "UPDATE ", "DELETE ", "MERGE ")
    )


@pytest.mark.asyncio
async def test_v2_route_loads_catalog_once_for_many_items():
    items = tuple(action_item(f"business-{index}") for index in range(20))
    collect = AsyncMock(return_value=snapshot(items=items))
    catalog = AsyncMock(return_value=frozenset({"request_owner_review"}))
    with (
        patch.object(surfaces, "collect_surface_snapshot", collect),
        patch.object(surfaces, "load_enabled_action_template_ids", catalog),
    ):
        response = await surfaces.control_room_experience_v2(OPERATOR)

    assert sum(len(section.facts) for section in response.sections) == 20
    catalog.assert_awaited_once_with(OPERATOR)
    assert all(
        len(fact.actions) == 1
        for section in response.sections
        for fact in section.facts
    )


@pytest.mark.asyncio
async def test_v2_route_does_not_query_action_catalog_without_write_permission():
    collect = AsyncMock(return_value=snapshot(items=(action_item(),)))
    catalog = AsyncMock(side_effect=AssertionError("action catalog queried"))
    with (
        patch.object(surfaces, "collect_surface_snapshot", collect),
        patch.object(surfaces, "load_enabled_action_template_ids", catalog),
    ):
        response = await surfaces.control_room_experience_v2(VIEWER)

    catalog.assert_not_awaited()
    assert response.sections[0].facts[0].actions == []


def test_v2_openapi_is_versioned_strict_and_read_only():
    app = FastAPI()
    app.include_router(routes.router)
    schema = app.openapi()
    operation = schema["paths"]["/api/control-room/experience/v2"]["get"]

    assert "post" not in schema["paths"]["/api/control-room/experience/v2"]
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response["$ref"].endswith("ControlRoomExperienceV2Response")
    for model in (
        "ControlRoomExperienceV2Response",
        "ExperienceFactV2",
        "ExperienceAction",
        "ExperienceActionPrerequisite",
    ):
        assert schema["components"]["schemas"][model]["additionalProperties"] is False


def test_preview_route_retains_write_permission_and_exact_server_path():
    route = next(
        route
        for route in routes.router.routes
        if route.path == "/api/control-room/items/{item_id}/action-preview"
    )
    permissions = {
        dependency.dependency.required_permission
        for dependency in route.dependencies
        if hasattr(dependency.dependency, "required_permission")
    }

    assert route.methods == {"POST"}
    assert permissions == {"control_room.write"}


@pytest.mark.asyncio
async def test_preview_rejects_missing_or_substituted_template_as_not_found():
    item = action_item()
    binding_id = item["metadata"]["explicit_action_bindings"][0]["binding_id"]
    for template_id in ("missing_template", "prepare_billing_review"):
        with patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=item),
        ):
            with pytest.raises(HTTPException) as exc:
                await control_room_service.action_preview(
                    str(item["id"]),
                    OPERATOR,
                    template_id=template_id,
                    binding_id=binding_id,
                )
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_preview_scope_rejection_happens_before_template_resolution():
    lookup = AsyncMock(side_effect=HTTPException(404, "control room item not found"))
    resolver = AsyncMock(side_effect=AssertionError("template resolution leaked"))
    with (
        patch.object(control_room_service, "_item_for_mutation", new=lookup),
        patch.object(
            control_room_service, "require_explicit_action_template", new=resolver
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.action_preview(
                "replayed-item",
                {**OPERATOR, "active_workspace_id": "foreign-workspace"},
                template_id="request_owner_review",
                binding_id="a" * 64,
            )

    assert exc.value.status_code == 404
    resolver.assert_not_awaited()
