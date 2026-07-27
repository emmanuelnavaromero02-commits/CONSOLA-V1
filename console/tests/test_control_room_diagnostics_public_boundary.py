from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.routers import control_room_surfaces as surface_routes
from app.services.control_room.operational_diagnostics import _text
from app.services.public_text_sensitivity import (
    contains_public_technical_copy,
    contains_public_technical_data,
)
from control_room_public_technical_canaries import TECHNICAL_PUBLIC_CANARIES
from control_room_surface_fixtures import (
    OPERATOR,
    installation,
    snapshot,
    source_state,
    source_status,
)


SAFE_OPERATIONAL_WARNINGS = (
    "Schema version changed",
    "Dataset materializado correctamente",
    "Más información: https://support.example.com/help",
    "Portal: https://support.example.com/help?next=/public/home",
    "Dataset copy completed",
    "Create policy for annual leave",
    "Gold-Leaf Logistics",
    "Silver-People Consulting",
    "Gold.Private Banking",
    "Gold.Coast Operations",
    "Silver.People Consulting",
    "Analyze workforce trends",
    "Refresh materialized view of talent",
    "Values (people first)",
    "Copy payroll to dashboard.",
    "Estado listo para revisión.",
    "Create table for annual planning.",
    "SuccessFactorsTraining",
    "SQL Team",
    "Basic Training",
    "Comment on table design",
    "Insert into culture",
    "Create index of capabilities",
    "Alter role responsibilities",
    "Select department from menu.",
    "Delete from shortlist.",
    "Use payroll insights",
    "Update status set to ready.",
    "Portal: https://example.com/#/dashboard/overview",
    "Portal: https://www.example.com/home/help",
    "Portal: https://www.example.com/users/profile",
    "Portal: https://support.example.com/?next=/home",
    "Portal: https://support.example.com/#/users/profile",
    "Portal: https://example.com/help?redirect=/public/home",
    "Download: https://example.com/help?download=https://cdn.example.com/report.pdf",
)


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = OPERATOR
        return await call_next(request)

    app.dependency_overrides[require_authenticated] = lambda: OPERATOR
    app.include_router(routes.router)
    return TestClient(app)


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _strings(nested)]
    if isinstance(value, list):
        return [item for nested in value for item in _strings(nested)]
    return []


def _assert_absent(response, *values: str) -> None:
    published = "\n".join(_strings(response.json()))
    serialized = response.content.decode("utf-8")
    for value in values:
        assert value not in published
        assert json.dumps(value, ensure_ascii=False)[1:-1] not in serialized


@pytest.mark.parametrize("technical", TECHNICAL_PUBLIC_CANARIES)
def test_authenticated_diagnostics_rejects_technical_scalar(technical: str) -> None:
    collect = AsyncMock(
        return_value=snapshot(
            sources=(source_status(readiness_reason=technical),),
        )
    )

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    _assert_absent(response, technical)
    collect.assert_awaited_once_with(OPERATOR)


@pytest.mark.parametrize(
    ("field", "container", "canary"),
    (
        (
            "title",
            {"nested": {"dataset": "title-mapping-canary"}},
            "title-mapping-canary",
        ),
        ("title", ["title-sequence-canary"], "title-sequence-canary"),
        (
            "reason",
            {"nested": {"receipt": "reason-mapping-canary"}},
            "reason-mapping-canary",
        ),
        ("reason", ["reason-sequence-canary"], "reason-sequence-canary"),
    ),
)
def test_authenticated_diagnostics_never_stringifies_text_containers(
    field: str,
    container: object,
    canary: str,
) -> None:
    current = (
        snapshot(diagnostics=(source_state(title=container),))
        if field == "title"
        else snapshot(sources=(source_status(readiness_reason=container),))
    )
    collect = AsyncMock(return_value=current)

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    _assert_absent(response, canary)


@pytest.mark.parametrize(
    "key",
    (
        r"C:\srv\private\path-key-marker.sql",
        r"\\server\share\path-key-marker.sql",
        "/srv/private/path-key-marker.sql",
        r"C:\pa\u0073sword\path-key-marker.sql",
    ),
)
def test_authenticated_diagnostics_rejects_nested_path_shaped_keys(key: str) -> None:
    collect = AsyncMock(
        return_value=snapshot(
            diagnostics=(source_state(title={"outer": {key: "safe"}}),),
        )
    )

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    _assert_absent(response, key)


@pytest.mark.parametrize(
    "key",
    ("filepath", "file_path", "directory", "pathValue", "directoryValue"),
)
def test_authenticated_diagnostics_rejects_nested_path_semantic_keys(
    key: str,
) -> None:
    canary = "nested-path-key-canary"
    collect = AsyncMock(
        return_value=snapshot(
            diagnostics=(source_state(title={"outer": {key: canary}}),),
        )
    )

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    _assert_absent(response, canary)


def test_all_diagnostic_public_text_fields_use_the_same_policy() -> None:
    values = (
        r"C:\srv\private\source-domain.sql",
        "SELECT * FROM private_source",
        "dataset=blocked_source",
        "bindings=blocked-warning",
        r"\\server\share\diagnostic-title.sql",
        "tenant_id=diagnostic-domain",
        "/srv/private/installation-label.sql",
        "provenance=installation-category",
    )
    collect = AsyncMock(
        return_value=snapshot(
            sources=(
                source_status(
                    domain=values[0],
                    readiness_reason=values[1],
                    readiness_blockers=[values[2]],
                    contract_warnings=[values[3]],
                ),
            ),
            diagnostics=(source_state(title=values[4], domain=values[5]),),
            installations=(installation(label=values[6], category=values[7]),),
        )
    )

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    _assert_absent(response, *values)


class _NeverStringify:
    def __str__(self) -> str:
        raise AssertionError("arbitrary diagnostic objects must not be stringified")


@pytest.mark.parametrize("value", [b"private-bytes", _NeverStringify()])
def test_diagnostic_text_extractor_accepts_only_scalar_strings(value: object) -> None:
    assert _text({"title": value}, "title") == ""


def test_authenticated_diagnostics_preserves_safe_operational_copy() -> None:
    collect = AsyncMock(
        return_value=snapshot(
            sources=(
                source_status(
                    domain="People",
                    readiness_reason="Dataset no materializado",
                    readiness_blockers=["Retry after 5 seconds"],
                    contract_warnings=list(SAFE_OPERATIONAL_WARNINGS),
                ),
            ),
            diagnostics=(source_state(title="Revisión operativa", domain="People"),),
            installations=(
                installation(label="SAP SuccessFactors", category="Human Resources"),
            ),
        )
    )

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    published = "\n".join(_strings(response.json()))
    for value in (
        "People",
        "Dataset no materializado",
        "Retry after 5 seconds",
        *SAFE_OPERATIONAL_WARNINGS,
        "Revisión operativa",
        "SAP SuccessFactors",
        "Human Resources",
    ):
        assert value in published


@pytest.mark.parametrize(
    "value",
    (*TECHNICAL_PUBLIC_CANARIES,),
)
def test_shared_public_text_policy_classifies_technical_copy(value: str) -> None:
    assert contains_public_technical_copy(value)


def test_shared_public_text_policy_inspects_nested_keys_and_values() -> None:
    assert contains_public_technical_data(
        {
            "safe": [
                {"nested": r"C:\srv\private\catalog.sql"},
                {r"C:\pa\u0073sword\catalog.sql": "otherwise safe"},
            ]
        }
    )
    assert not contains_public_technical_data(
        {"safe": ["Revisión operativa", {"area": "Dirección de México"}]}
    )
