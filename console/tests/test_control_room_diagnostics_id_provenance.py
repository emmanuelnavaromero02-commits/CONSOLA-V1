from __future__ import annotations

import json
from multiprocessing.reduction import ForkingPickler
from urllib.parse import quote

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.routers import control_room_surfaces as surface_routes
from app.services.control_room.diagnostics_public_copy import (
    DIAGNOSTICS_ENDPOINT,
    DIAGNOSTIC_ITEM_TITLE_FIELD,
    _PRODUCT_COPY_REGISTRY,
    _resolve_server_copy,
)
from app.services.control_room.diagnostics_public_factory import (
    RawDiagnosticsDraft,
    build_diagnostics_response,
)
from control_room_diagnostics_private_copy_fixture import (
    private_server_copy_registry,
)
from control_room_surface_fixtures import OPERATOR, snapshot


ID_ONLY_COPY = (
    "Show me the Q4 report",
    "Describe the onboarding process",
    "Use of force policy",
    "Set goals to improve performance",
    "Set expectations to align teams",
    "TABLE Rock",
    "SELECT Comercial",
    "TRUNCATE Labs",
    "Show Solutions",
    "Describe Digital",
    "Set goals to win",
    "Set priorities to high",
)


def _warning_payload(literal: str, **external: object) -> dict[str, object]:
    row: dict[str, object] = {
        "cartridge": "sap_hcm",
        "dataset": "employees",
        "status": "ok",
        "contract_warnings": [literal],
    }
    row.update(external)
    response = build_diagnostics_response(
        RawDiagnosticsDraft(
            generated_at=snapshot().generated_at,
            sources=(row,),
        )
    )
    return response.model_dump(mode="json", exclude_none=True)


@pytest.mark.parametrize("literal", ID_ONLY_COPY)
def test_same_bytes_require_private_id_at_the_exact_position(literal: str) -> None:
    assert literal not in _PRODUCT_COPY_REGISTRY.values()
    assert _warning_payload(literal)["sources"][0]["warnings"] == []

    copy_id, registry = private_server_copy_registry(
        literal,
        endpoint=DIAGNOSTICS_ENDPOINT,
        field=DIAGNOSTIC_ITEM_TITLE_FIELD,
    )
    resolved = _resolve_server_copy(
        copy_id,
        DIAGNOSTICS_ENDPOINT,
        DIAGNOSTIC_ITEM_TITLE_FIELD,
        _registry=registry,
    )

    assert resolved == literal
    assert resolved.encode() == literal.encode()


def test_private_id_is_opaque_exact_and_non_deserializable() -> None:
    literal = "Show me the Q4 report"
    copy_id, registry = private_server_copy_registry(
        literal,
        endpoint=DIAGNOSTICS_ENDPOINT,
        field=DIAGNOSTIC_ITEM_TITLE_FIELD,
    )

    rejected = (
        (object(), DIAGNOSTICS_ENDPOINT, DIAGNOSTIC_ITEM_TITLE_FIELD),
        ("external-copy-id", DIAGNOSTICS_ENDPOINT, DIAGNOSTIC_ITEM_TITLE_FIELD),
        (copy_id, "gold", DIAGNOSTIC_ITEM_TITLE_FIELD),
        (copy_id, DIAGNOSTICS_ENDPOINT, "sources[].warnings[]"),
        (
            json.loads(json.dumps("external-copy-id")),
            DIAGNOSTICS_ENDPOINT,
            DIAGNOSTIC_ITEM_TITLE_FIELD,
        ),
        (
            ForkingPickler.loads(ForkingPickler.dumps(copy_id)),
            DIAGNOSTICS_ENDPOINT,
            DIAGNOSTIC_ITEM_TITLE_FIELD,
        ),
    )

    for candidate, endpoint, field in rejected:
        assert (
            _resolve_server_copy(
                candidate,
                endpoint,
                field,
                _registry=registry,
            )
            is None
        )


def _encoded_private_path(layers: int) -> str:
    value = "/srv/private/payroll.csv"
    for _ in range(layers):
        value = quote(value, safe="")
    return value


@pytest.mark.parametrize(
    "literal",
    (
        "/srv/private/payroll.csv",
        "api_key=sk_live_opaque123",
        "workspace_id=550e8400-e29b-41d4-a716-446655440000",
        "safe\u202etxt",
        _encoded_private_path(10),
        '{"employees":[{"name":"Ana"}]}',
    ),
    ids=("path", "secret", "technical-id", "unicode", "decode-overflow", "structure"),
)
def test_private_id_never_bypasses_non_sql_hazards(literal: str) -> None:
    copy_id, registry = private_server_copy_registry(
        literal,
        endpoint=DIAGNOSTICS_ENDPOINT,
        field=DIAGNOSTIC_ITEM_TITLE_FIELD,
    )

    assert (
        _resolve_server_copy(
            copy_id,
            DIAGNOSTICS_ENDPOINT,
            DIAGNOSTIC_ITEM_TITLE_FIELD,
            _registry=registry,
        )
        is None
    )


@pytest.mark.parametrize(
    "assemble",
    (
        pytest.param(lambda copy, slot: f"{copy} for {slot}", id="f-string"),
        pytest.param(lambda copy, slot: "{} for {}".format(copy, slot), id="format"),
        pytest.param(lambda copy, slot: copy + " for " + slot, id="concatenate"),
        pytest.param(lambda copy, slot: " ".join((copy, "for", slot)), id="join"),
        pytest.param(lambda copy, slot: copy.replace("Q4", slot), id="replace"),
    ),
)
def test_assembled_server_copy_loses_id_provenance(assemble) -> None:
    literal = "Show me the Q4 report"
    copy_id, registry = private_server_copy_registry(
        literal,
        endpoint=DIAGNOSTICS_ENDPOINT,
        field=DIAGNOSTIC_ITEM_TITLE_FIELD,
    )
    resolved = _resolve_server_copy(
        copy_id,
        DIAGNOSTICS_ENDPOINT,
        DIAGNOSTIC_ITEM_TITLE_FIELD,
        _registry=registry,
    )

    assert resolved == literal
    laundered = assemble(resolved, "Madrid")
    assert laundered != literal
    assert _warning_payload(laundered)["sources"][0]["warnings"] == []


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = OPERATOR
        return await call_next(request)

    app.dependency_overrides[require_authenticated] = lambda: OPERATOR
    app.include_router(routes.router)
    return TestClient(app)


@pytest.mark.parametrize("literal", ID_ONLY_COPY)
def test_http_payload_cannot_reach_private_registry(literal: str) -> None:
    serialized_id = json.dumps("private-copy-id")
    current = snapshot(
        sources=(
            {
                "cartridge": "sap_hcm",
                "dataset": "employees",
                "status": "ok",
                "contract_warnings": [literal],
                "copy_id": serialized_id,
                "endpoint": DIAGNOSTICS_ENDPOINT,
                "field": DIAGNOSTIC_ITEM_TITLE_FIELD,
                "trusted": True,
            },
        )
    )
    collect = AsyncMock(return_value=current)

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get(
            "/api/control-room/diagnostics",
            params={
                "copy_id": serialized_id,
                "endpoint": DIAGNOSTICS_ENDPOINT,
                "field": DIAGNOSTIC_ITEM_TITLE_FIELD,
                "trusted": "true",
            },
        )

    assert response.status_code == 200
    assert response.json()["sources"][0]["warnings"] == []
    assert literal.encode() not in response.content
