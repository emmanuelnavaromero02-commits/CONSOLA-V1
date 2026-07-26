from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.routers import control_room_surfaces as surface_routes
from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.operational_diagnostics import (
    build_operational_diagnostics,
    redact_diagnostic_value,
)
from app.services.control_room.surface_snapshot import collect_surface_snapshot
from app.services import control_room_service
from control_room_surface_fixtures import (
    OPERATOR,
    TENANT_ID,
    VIEWER,
    WORKSPACE_ID,
    business_item,
    installation,
    snapshot,
    source_state,
    source_status,
)


PRIVATE_SURFACE_KEYS = {
    "scope",
    "tenant_id",
    "workspace_id",
    "dataset",
    "source_dataset",
    "cartridge",
    "cartridge_id",
    "connector_id",
    "module",
    "module_id",
    "current_step",
    "id",
    "item_id",
}


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for nested in value.values() for key in _all_keys(nested)
        }
    if isinstance(value, list):
        return {key for nested in value for key in _all_keys(nested)}
    return set()


def _client(user: dict) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    app.dependency_overrides[require_authenticated] = lambda: user
    app.include_router(routes.router)
    return TestClient(app)


def test_source_state_is_diagnostics_only():
    state = source_state()
    current = snapshot(
        items=(business_item(), state),
        diagnostics=(state,),
        sources=(source_status(),),
        installations=(installation(),),
    )
    experience = build_business_experience(current).model_dump(mode="json")
    diagnostics = build_operational_diagnostics(current).model_dump(mode="json")

    assert "source_state" not in json.dumps(experience)
    assert [item["kind"] for item in diagnostics["diagnostic_items"]] == [
        "source_state"
    ]
    assert "Observed business condition" not in json.dumps(diagnostics)
    assert not (_all_keys(experience) & PRIVATE_SURFACE_KEYS)
    assert not (_all_keys(diagnostics) & PRIVATE_SURFACE_KEYS)


def test_diagnostics_recursively_redacts_secrets_and_sensitive_pii():
    secret = "top-secret-value"
    current = snapshot(
        diagnostics=(
            source_state(
                details={
                    "error": (
                        f"Bearer {secret} token={secret} "
                        "admin@example.com +34 555 010 123 "
                        "postgres://user:pass@db.local/app "
                        '{"oauth_access_token":"json-secret-value"} '
                        "api key: alternate-secret "
                        "password is hidden-value "
                        "RFC ABCD010203EF4 "
                        "address is 123 Private Street"
                    ),
                    "nested": {
                        "api_key": secret,
                        "oauth_access_token": secret,
                        "client_private_key": secret,
                        "contact_email": "owner@example.com",
                        "phone": "+34-555-0101",
                        "refresh_token": secret,
                    },
                }
            ),
        ),
        sources=(
            source_status(
                error=f"authorization={secret} owner@example.com",
                metadata={"password": secret},
            ),
        ),
        installations=(
            installation(
                error_message=f"cookie={secret}",
                credentials={"secret": secret},
            ),
        ),
    )
    payload = build_operational_diagnostics(current).model_dump(
        mode="json",
        exclude_none=True,
    )
    serialized = json.dumps(payload)
    nested = redact_diagnostic_value(
        {"metadata": {"token": secret, "email": "owner@example.com"}}
    )

    assert secret not in serialized
    assert "json-secret-value" not in serialized
    assert "alternate-secret" not in serialized
    assert "hidden-value" not in serialized
    assert "ABCD010203EF4" not in serialized
    assert "123 Private Street" not in serialized
    assert "admin@example.com" not in serialized
    assert "owner@example.com" not in serialized
    assert "+34 555 010 123" not in serialized
    assert "user:pass@" not in serialized
    assert payload["sources"][0]["error"] == "Source query failed"
    assert payload["diagnostic_items"][0]["error"] == "Diagnostic error reported"
    assert payload["installations"][0]["error"] == "Installation error reported"
    assert nested == {"metadata": {"token": "[REDACTED]", "email": "[REDACTED]"}}
    assert not (_all_keys(payload) & PRIVATE_SURFACE_KEYS)


def test_viewer_reads_experience_but_not_diagnostics_and_operator_reads_both():
    current = snapshot(
        items=(business_item(),),
        diagnostics=(source_state(),),
        sources=(source_status(),),
        installations=(installation(),),
    )
    collect = AsyncMock(return_value=current)
    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        viewer = _client(VIEWER)
        experience = viewer.get("/api/control-room/experience")
        assert experience.status_code == 200
        assert not (_all_keys(experience.json()) & PRIVATE_SURFACE_KEYS)
        assert viewer.get("/api/control-room/diagnostics").status_code == 403
        operator = _client(OPERATOR)
        diagnostics = operator.get("/api/control-room/diagnostics")
        assert diagnostics.status_code == 200
        assert not (_all_keys(diagnostics.json()) & PRIVATE_SURFACE_KEYS)
        for technical_value in (
            "gold_business_observations",
            "people_overview",
            "test_registry",
            TENANT_ID,
            WORKSPACE_ID,
        ):
            assert technical_value not in experience.text
            assert technical_value not in diagnostics.text

    assert collect.await_count == 2


def test_diagnostics_cross_scope_fails_closed_as_not_found():
    foreign = {"workspace_id": "foreign-workspace"}
    for field in ("diagnostics", "sources", "installations"):
        with pytest.raises(HTTPException) as exc:
            build_operational_diagnostics(snapshot(**{field: (foreign,)}))
        assert exc.value.status_code == 404


def test_each_successful_route_collects_exactly_once():
    empty = snapshot()
    for user, path in (
        (VIEWER, "/api/control-room/experience"),
        (OPERATOR, "/api/control-room/diagnostics"),
    ):
        collect = AsyncMock(return_value=empty)
        with patch.object(surface_routes, "collect_surface_snapshot", collect):
            response = _client(user).get(path)
        assert response.status_code == 200
        collect.assert_awaited_once_with(user)


@pytest.mark.asyncio
async def test_snapshot_uses_one_canonical_read_only_collection():
    collector = AsyncMock(
        return_value={
            "items": [],
            "diagnostics": [],
            "sources": [],
            "installations": [],
        }
    )
    with patch.object(control_room_service, "_collect_items", collector):
        await collect_surface_snapshot(VIEWER)

    collector.assert_awaited_once()
    assert collector.await_args.kwargs["persist"] is False
    assert collector.await_args.kwargs["include_source_state_items"] is True
