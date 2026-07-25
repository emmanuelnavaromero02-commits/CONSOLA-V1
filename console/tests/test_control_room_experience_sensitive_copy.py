from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import httpx
import pytest

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.routers import control_room_surfaces as surface_routes
from app.services.control_room.business_experience import build_business_experience
from control_room_get_edges import installed_read_edges
from control_room_get_harness import ConcurrencyProbe, MutationSentinel, build_app
from control_room_surface_fixtures import VIEWER, business_item, snapshot


JWT = "eyJhbGciOiJIUzI1NiJ9." "eyJzdWIiOiIxMjM0NTY3ODkwIn0." "c2lnbmF0dXJl"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
GCP_KEY = f"AIza{'A' * 35}"
GITHUB_TOKEN = f"ghp_{'a' * 36}"


@pytest.mark.parametrize(
    "sensitive",
    (
        JWT,
        "eyJhbGciOiJIUzI1NiJ9.e30.c2lnbmF0dXJl",
        "IHsiYWxnIjoiSFMyNTYifQ.e30.c2lnbmF0dXJl",
        "Bearer abcdefghijklmnopqrstuvwxyz",
        AWS_KEY,
        GCP_KEY,
        GITHUB_TOKEN,
        "postgres://user:password@db.internal/app",
        "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----",
        "owner@example.com",
        "josé.pérez@empresa.es",
        "+52 55 1234 5678",
        "GODE561231HDFRRN09",
        "GODE561231GR8",
    ),
)
def test_sensitive_fact_title_is_never_published(sensitive: str) -> None:
    item = business_item(title=f"Rotación observada {sensitive}")

    payload = build_business_experience(snapshot(items=(item,))).model_dump_json()

    assert sensitive not in payload
    assert build_business_experience(snapshot(items=(item,))).sections == []


def test_sensitive_copy_after_title_limit_is_detected_before_truncation() -> None:
    contaminated = f"{'A' * 260} {GITHUB_TOKEN}"
    item = business_item(title=contaminated)

    payload = build_business_experience(snapshot(items=(item,))).model_dump_json()

    assert GITHUB_TOKEN not in payload
    assert build_business_experience(snapshot(items=(item,))).sections == []


@pytest.mark.parametrize(
    ("field", "expected_name", "expected_optional"),
    (
        ("entity_label", "count", None),
        ("metric_name", "count", "Observed employee"),
        ("unit", "count", "Observed employee"),
    ),
)
def test_sensitive_optional_fields_are_omitted_or_fall_back(
    field: str,
    expected_name: str,
    expected_optional: str | None,
) -> None:
    item = business_item(**{field: GITHUB_TOKEN})

    payload = build_business_experience(snapshot(items=(item,))).model_dump(
        mode="json", exclude_none=True
    )
    fact = payload["sections"][0]["facts"][0]
    serialized = json.dumps(payload)

    assert GITHUB_TOKEN not in serialized
    assert fact["metric"]["name"] == expected_name
    if field == "entity_label":
        assert "entity_label" not in fact
    else:
        assert fact["entity_label"] == expected_optional
    if field == "unit":
        assert "unit" not in fact["metric"]


@pytest.mark.parametrize(
    "unsafe",
    (
        "Rotación\x00oculta",
        "Rotación\u202eoculta",
        "Rotación\u2066oculta\u2069",
        '{"title":"Rotación","status":"ready"}',
        '["Rotación","ready"]',
    ),
)
def test_unicode_controls_bidi_and_structured_copy_are_rejected(
    unsafe: str,
) -> None:
    item = business_item(title=unsafe)

    assert build_business_experience(snapshot(items=(item,))).sections == []


def _client(user: dict[str, object]) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    app.dependency_overrides[require_authenticated] = lambda: user
    app.include_router(routes.router)
    return TestClient(app)


def test_experience_endpoint_preserves_403_and_scope_404() -> None:
    forbidden = {
        **VIEWER,
        "role": "workspace_user",
        "workspace_role": "workspace_user",
    }
    collect = AsyncMock(return_value=snapshot())
    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        assert _client(forbidden).get("/api/control-room/experience").status_code == 403
    collect.assert_not_awaited()

    foreign = business_item(workspace_id="foreign-workspace")
    collect = AsyncMock(return_value=snapshot(items=(foreign,)))
    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        assert _client(VIEWER).get("/api/control-room/experience").status_code == 404
    collect.assert_awaited_once()


@pytest.mark.asyncio
async def test_experience_endpoint_remains_scoped_pure_and_without_dml() -> None:
    sentinel = MutationSentinel()
    before = deepcopy(sentinel.tables)
    probe = ConcurrencyProbe()
    transport = httpx.ASGITransport(app=build_app(probe))

    with installed_read_edges(sentinel, probe):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/control-room/experience",
                headers={"x-purity-request": "visible-copy"},
            )

    assert response.status_code == 200
    assert sentinel.tables == before
    assert sentinel.mutation_attempts == []
    assert sentinel.scope_calls
