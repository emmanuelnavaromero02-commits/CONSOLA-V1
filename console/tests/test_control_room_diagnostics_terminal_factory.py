from __future__ import annotations

import inspect
import json
from multiprocessing.reduction import ForkingPickler
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.routers import control_room_surfaces as surface_routes
from app.services.control_room.diagnostics_public_copy import (
    DIAGNOSTICS_ENDPOINT,
    DIAGNOSTIC_ITEM_TITLE_FIELD,
    _resolve_server_copy,
)
from app.services.control_room.diagnostics_public_factory import (
    RawDiagnosticsDraft,
    build_diagnostics_response,
)
from control_room_diagnostics_private_copy_fixture import (
    CLASS_1_COPY,
    CLASS_2_COPY,
    HARD_SERVER_COPY,
    P2_BUSINESS_COPY,
    SQL_AMBIGUOUS_COPY,
    TECHNICAL_COPY,
    private_server_copy_registry,
)
from control_room_surface_fixtures import OPERATOR, snapshot


@pytest.mark.parametrize("literal", CLASS_1_COPY)
def test_private_server_copy_is_exact_and_position_bound(literal: str) -> None:
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
        == literal
    )
    assert (
        _resolve_server_copy(
            copy_id,
            "gold",
            DIAGNOSTIC_ITEM_TITLE_FIELD,
            _registry=registry,
        )
        is None
    )
    assert (
        _resolve_server_copy(
            copy_id,
            DIAGNOSTICS_ENDPOINT,
            "sources[].reason",
            _registry=registry,
        )
        is None
    )
    assert (
        _resolve_server_copy(
            "external-copy-id",
            DIAGNOSTICS_ENDPOINT,
            DIAGNOSTIC_ITEM_TITLE_FIELD,
            _registry=registry,
        )
        is None
    )
    assert (
        _resolve_server_copy(
            object(),
            DIAGNOSTICS_ENDPOINT,
            DIAGNOSTIC_ITEM_TITLE_FIELD,
            _registry=registry,
        )
        is None
    )
    assert (
        _resolve_server_copy(
            json.loads(json.dumps("deserialized-copy-id")),
            DIAGNOSTICS_ENDPOINT,
            DIAGNOSTIC_ITEM_TITLE_FIELD,
            _registry=registry,
        )
        is None
    )
    assert (
        _resolve_server_copy(
            ForkingPickler.loads(ForkingPickler.dumps(copy_id)),
            DIAGNOSTICS_ENDPOINT,
            DIAGNOSTIC_ITEM_TITLE_FIELD,
            _registry=registry,
        )
        is None
    )


def test_server_copy_resolver_has_no_text_trust_or_slot_api() -> None:
    parameters = set(inspect.signature(_resolve_server_copy).parameters)
    assert not parameters & {"text", "trusted", "slots", "values", "template"}


@pytest.mark.parametrize("literal", HARD_SERVER_COPY)
def test_private_server_copy_cannot_bypass_hard_hazards(literal: str) -> None:
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


def _draft() -> RawDiagnosticsDraft:
    return RawDiagnosticsDraft(
        generated_at=snapshot().generated_at,
        sources=(
            {
                "cartridge": "sap_hcm",
                "dataset": "employees",
                "status": "unavailable",
                "data_readiness": "unavailable",
                "readiness_reason": "TABLE private_schema.people",
                "error": "postgres://user:pass@db.local/private",
            },
        ),
        diagnostic_items=(
            {
                "kind": "source_state",
                "title": "FROM private_schema.people SELECT *",
                "error": "secret=opaque",
            },
        ),
        installations=(
            {
                "cartridge_id": "sap_hcm",
                "installation_status": "failed",
                "label": "/srv/private/install.sql",
                "error_message": "password=opaque",
            },
        ),
    )


def test_terminal_factory_owns_rejection_and_controlled_server_copy() -> None:
    payload = build_diagnostics_response(_draft()).model_dump(
        mode="json",
        exclude_none=True,
    )

    assert "reason" not in payload["sources"][0]
    assert payload["sources"][0]["error"] == "Source query failed"
    assert payload["diagnostic_items"][0]["title"] == "Technical diagnostic"
    assert payload["diagnostic_items"][0]["error"] == "Diagnostic error reported"
    assert "label" not in payload["installations"][0]
    assert payload["installations"][0]["error"] == "Installation error reported"


@pytest.mark.parametrize("literal", (*P2_BUSINESS_COPY, *CLASS_2_COPY))
def test_terminal_factory_preserves_business_copy_byte_identically(
    literal: str,
) -> None:
    draft = RawDiagnosticsDraft(
        generated_at=snapshot().generated_at,
        sources=(
            {
                "cartridge": "sap_hcm",
                "dataset": "employees",
                "status": "ok",
                "contract_warnings": [literal],
            },
        ),
    )

    payload = build_diagnostics_response(draft).model_dump(mode="json")

    assert payload["sources"][0]["warnings"] == [literal]
    assert payload["sources"][0]["warnings"][0].encode() == literal.encode()


@pytest.mark.parametrize("literal", (*TECHNICAL_COPY, *SQL_AMBIGUOUS_COPY))
def test_terminal_factory_blocks_unprovenanced_technical_copy(literal: str) -> None:
    draft = RawDiagnosticsDraft(
        generated_at=snapshot().generated_at,
        sources=(
            {
                "cartridge": "sap_hcm",
                "dataset": "employees",
                "status": "ok",
                "contract_warnings": [literal],
            },
        ),
    )

    payload = build_diagnostics_response(draft).model_dump(mode="json")

    assert payload["sources"][0]["warnings"] == []


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = OPERATOR
        return await call_next(request)

    app.dependency_overrides[require_authenticated] = lambda: OPERATOR
    app.include_router(routes.router)
    return TestClient(app)


def test_authenticated_http_uses_the_terminal_diagnostics_factory() -> None:
    current = snapshot(
        diagnostics=_draft().diagnostic_items,
        sources=_draft().sources,
        installations=_draft().installations,
    )
    collect = AsyncMock(return_value=current)

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    assert "private_schema" not in response.text
    assert "/srv/private" not in response.text
    assert response.json()["diagnostic_items"][0]["title"] == "Technical diagnostic"
    assert response.json()["sources"][0]["error"] == "Source query failed"
    collect.assert_awaited_once_with(OPERATOR)


@pytest.mark.parametrize("literal", (*P2_BUSINESS_COPY, *CLASS_2_COPY))
def test_authenticated_http_preserves_business_copy_byte_identically(
    literal: str,
) -> None:
    current = snapshot(
        sources=(
            {
                "cartridge": "sap_hcm",
                "dataset": "employees",
                "status": "ok",
                "contract_warnings": [literal],
            },
        )
    )
    collect = AsyncMock(return_value=current)

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    assert response.json()["sources"][0]["warnings"] == [literal]
    assert literal.encode() in response.content


@pytest.mark.parametrize("literal", (*TECHNICAL_COPY, *SQL_AMBIGUOUS_COPY))
def test_authenticated_http_blocks_unprovenanced_technical_copy(literal: str) -> None:
    current = snapshot(
        sources=(
            {
                "cartridge": "sap_hcm",
                "dataset": "employees",
                "status": "ok",
                "contract_warnings": [literal],
            },
        )
    )
    collect = AsyncMock(return_value=current)

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    assert response.json()["sources"][0]["warnings"] == []
    assert literal.encode() not in response.content
