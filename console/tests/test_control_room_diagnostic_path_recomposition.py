from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.routers import control_room_surfaces as surface_routes
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from control_room_surface_fixtures import OPERATOR, snapshot, source_status


SECRET = "TOPSECRET"
BEARER_SPLITS_V5 = (
    ("B", "u0065arer TOPSECRET"),
    ("Be", "u0061rer TOPSECRET"),
    ("Bea", "u0072er TOPSECRET"),
    ("Bear", "u0065r TOPSECRET"),
)
ASSIGNMENT_SPLITS_V5 = (
    ("password", "u003aTOPSECRET"),
    ("pass", "u0077ord", "u003dTOPSECRET"),
)
BENIGN_SPLITS_V5 = (
    ("Met", "u0072ic"),
    ("Be", "x4G"),
    ("pass", "u12GG"),
)


def _structured_paths(parts: tuple[str, ...]) -> tuple[str, ...]:
    standard = "\\" * 2
    doubled = "\\" * 4
    unc_standard = "\\" * 4
    unc_doubled = "\\" * 8
    standard_tail = standard.join(parts)
    doubled_tail = doubled.join(parts)
    raw_paths = (
        f"C:{standard}{standard_tail}",
        f"C:{doubled}{doubled_tail}",
        f"{unc_standard}server{standard}{standard_tail}",
        f"{unc_standard}server{doubled}{doubled_tail}",
        f"{unc_doubled}server{doubled}{doubled_tail}",
    )
    return tuple(f'{{"path":"{path}"}}' for path in raw_paths)


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = OPERATOR
        return await call_next(request)

    app.dependency_overrides[require_authenticated] = lambda: OPERATOR
    app.include_router(routes.router)
    return TestClient(app)


def test_v5_bearer_recomposition_matrix_never_leaks() -> None:
    checked = 0
    for parts in BEARER_SPLITS_V5:
        for value in _structured_paths(parts):
            assert redact_diagnostic_value(value) == "[REDACTED]", value
            checked += 1

    assert checked == 20


def test_v5_sensitive_assignment_recomposition_matrix_never_leaks() -> None:
    checked = 0
    for parts in ASSIGNMENT_SPLITS_V5:
        for value in _structured_paths(parts):
            redacted = redact_diagnostic_value(value)
            assert redacted == "[REDACTED]", value
            assert SECRET not in str(redacted)
            checked += 1

    assert checked == 10


def test_v5_unsupported_prefix_controls_are_byte_identical() -> None:
    checked = 0
    for parts in BENIGN_SPLITS_V5:
        for value in _structured_paths(parts):
            assert redact_diagnostic_value(value) == value
            checked += 1

    assert checked == 15


@pytest.mark.parametrize(
    "reason",
    (
        r'{"path":"C:\\B\\u0065arer TOPSECRET"}',
        r'{"path":"C:\\\\Be\\\\u0061rer TOPSECRET"}',
        r'{"path":"\\\\server\\Bea\\u0072er TOPSECRET"}',
        r'{"path":"\\\\server\\\\Bear\\\\u0065r TOPSECRET"}',
        r'{"path":"C:\\password\\u003aTOPSECRET"}',
        r'{"path":"\\\\server\\pass\\u0077ord\\u003dTOPSECRET"}',
    ),
)
def test_v5_recomposed_readiness_reason_http_never_leaks(reason: str) -> None:
    collect = AsyncMock(
        return_value=snapshot(
            sources=(source_status(readiness_reason=reason),),
        )
    )

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    assert SECRET not in response.text
    assert response.json()["sources"][0]["reason"] == "[REDACTED]"
    collect.assert_awaited_once_with(OPERATOR)
