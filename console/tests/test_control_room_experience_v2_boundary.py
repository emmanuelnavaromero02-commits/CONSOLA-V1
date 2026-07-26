from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.dependencies import require_authenticated
from app.routers import control_room
from app.routers import control_room_surfaces as surfaces
from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_experience_v2 import (
    build_business_experience_v2,
)
from control_room_surface_fixtures import (
    OPERATOR,
    VIEWER,
    action_item,
    business_item,
    snapshot,
)


@pytest.mark.parametrize(
    "item_id",
    (
        "business:1",
        "business.1",
        "business%1",
        "business/1",
        "negocio-área-1",
        "x" * 241,
    ),
)
def test_v2_preserves_every_v1_fact_when_item_id_cannot_bind(item_id: str):
    current = snapshot(items=(business_item(item_id),))
    v1 = build_business_experience(current).model_dump(mode="json", exclude_none=True)
    v2 = build_business_experience_v2(
        current,
        user=OPERATOR,
        enabled_template_ids={"request_owner_review"},
    ).model_dump(mode="json", exclude_none=True)

    assert len(v1["sections"]) == len(v2["sections"]) == 1
    v1_fact = v1["sections"][0]["facts"][0]
    v2_fact = v2["sections"][0]["facts"][0]
    assert {key: value for key, value in v2_fact.items() if key != "actions"} == v1_fact
    assert v2_fact["actions"] == []
    assert "item_id" not in v2_fact


def test_unsigned_or_tampered_bindings_never_publish_actions():
    unsigned = business_item()
    unsigned["metadata"] = {
        **dict(unsigned.get("metadata") or {}),
        "explicit_action_bindings": [
            {
                "template_id": "request_owner_review",
                "item_id": "business-1",
            }
        ],
    }
    signed = action_item()
    tampered = deepcopy(signed)
    tampered["metadata"]["explicit_action_bindings"][0]["template_id"] = (
        "create_followup_task"
    )

    for item in (unsigned, tampered):
        response = build_business_experience_v2(
            snapshot(items=(item,)),
            user=OPERATOR,
            enabled_template_ids={"request_owner_review", "create_followup_task"},
        )
        assert response.sections[0].facts[0].actions == []


def _client(user: dict | None) -> TestClient:
    app = FastAPI()
    if user is not None:

        @app.middleware("http")
        async def _inject_user(request: Request, call_next):
            request.state.user = user
            return await call_next(request)

        async def _current_user():
            return user

        app.dependency_overrides[require_authenticated] = _current_user
    app.include_router(control_room.router)
    return TestClient(app, raise_server_exceptions=True)


def _forbidden_action_metadata(value: object) -> set[str]:
    forbidden = {
        "item_id",
        "template_id",
        "binding",
        "binding_id",
        "tenant_id",
        "workspace_id",
        "source_dataset",
        "dataset",
        "system",
        "fingerprint",
        "policy_version",
        "producer",
        "provenance",
        "metadata",
    }
    if isinstance(value, dict):
        found = forbidden.intersection(value)
        for nested in value.values():
            found.update(_forbidden_action_metadata(nested))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for nested in value:
            found.update(_forbidden_action_metadata(nested))
        return found
    return set()


def test_v2_asgi_enforces_401_403_and_redacts_authorized_viewer():
    path = "/api/control-room/experience/v2"
    unauthorized = _client(None).get(path)
    assert unauthorized.status_code == 401

    blocked = {
        **VIEWER,
        "role": "workspace_user",
        "workspace_role": "workspace_user",
    }
    collect = AsyncMock(return_value=snapshot(items=(action_item(),)))
    with patch.object(surfaces, "collect_surface_snapshot", collect):
        forbidden = _client(blocked).get(path)
    assert forbidden.status_code == 403
    collect.assert_not_awaited()

    collect = AsyncMock(return_value=snapshot(items=(action_item(),)))
    catalog = AsyncMock(side_effect=AssertionError("viewer queried action catalog"))
    with (
        patch.object(surfaces, "collect_surface_snapshot", collect),
        patch.object(surfaces, "load_enabled_action_template_ids", catalog),
    ):
        response = _client(VIEWER).get(path)
    assert response.status_code == 200
    payload = response.json()
    fact = payload["sections"][0]["facts"][0]
    assert fact["actions"] == []
    assert _forbidden_action_metadata(fact) == set()
    catalog.assert_not_awaited()


def test_writer_payload_exposes_only_opaque_business_action_contract():
    payload = build_business_experience_v2(
        snapshot(items=(action_item(),)),
        user=OPERATOR,
        enabled_template_ids={"request_owner_review"},
    ).model_dump(mode="json", exclude_none=True)

    assert "scope" not in payload
    assert _forbidden_action_metadata(payload) == set()
    fact = payload["sections"][0]["facts"][0]
    assert "item_id" not in fact
    action = fact["actions"][0]
    assert set(action) == {
        "action_handle",
        "label",
        "operation",
        "enabled",
        "requires_approval",
        "prerequisites",
        "method",
        "endpoint",
    }
    assert action["endpoint"] == "/api/control-room/actions/preview"
    assert len(action["action_handle"]) == 64
    assert _forbidden_action_metadata(action) == set()
