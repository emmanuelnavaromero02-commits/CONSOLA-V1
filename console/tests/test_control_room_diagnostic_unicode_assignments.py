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
ASSIGNMENT_SEPARATOR_CODEPOINTS_V1 = (
    0x02D0,
    0x02F8,
    0x0589,
    0x05C3,
    0x205A,
    0x2236,
    0xA789,
    0xFE13,
    0xFE55,
    0xFF1A,
    0x10781,
    0x207C,
    0x208C,
    0x2260,
    0xFE66,
    0xFF1D,
)
IS_SPACE_CODEPOINTS_V1 = (
    0x00A0,
    0x00A8,
    0x00AF,
    0x00B4,
    0x00B8,
    0x02D8,
    0x02D9,
    0x02DA,
    0x02DB,
    0x02DC,
    0x02DD,
    0x037A,
    0x0384,
    0x0385,
    0x1FBD,
    0x1FBF,
    0x1FC0,
    0x1FC1,
    0x1FCD,
    0x1FCE,
    0x1FCF,
    0x1FDD,
    0x1FDE,
    0x1FDF,
    0x1FED,
    0x1FEE,
    0x1FFD,
    0x1FFE,
    0x2000,
    0x2001,
    0x2002,
    0x2003,
    0x2004,
    0x2005,
    0x2006,
    0x2007,
    0x2008,
    0x2009,
    0x200A,
    0x2017,
    0x202F,
    0x203E,
    0x205F,
    0x3000,
    0x309B,
    0x309C,
    0xFC5E,
    0xFC5F,
    0xFC60,
    0xFC61,
    0xFC62,
    0xFC63,
    0xFE49,
    0xFE4A,
    0xFE4B,
    0xFE4C,
    0xFE70,
    0xFE72,
    0xFE74,
    0xFE76,
    0xFE78,
    0xFE7A,
    0xFE7C,
    0xFE7E,
    0xFFE3,
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


def test_v1_unicode_assignment_separator_matrix_never_leaks() -> None:
    checked = 0
    for codepoint in ASSIGNMENT_SEPARATOR_CODEPOINTS_V1:
        value = f"pa\\qssword{chr(codepoint)}{SECRET}"
        assert redact_diagnostic_value(value) == "[REDACTED]", hex(codepoint)
        checked += 1

    assert checked == 16


def test_v1_unicode_is_space_matrix_never_leaks() -> None:
    checked = 0
    for codepoint in IS_SPACE_CODEPOINTS_V1:
        space = chr(codepoint)
        value = f"pa\\qssword{space}is{space}{SECRET}"
        assert redact_diagnostic_value(value) == "[REDACTED]", hex(codepoint)
        checked += 1

    assert checked == 65


@pytest.mark.parametrize(
    "reason",
    (
        f"pa\\qssword{chr(0xFF1A)}{SECRET}",
        f"pa\\qssword{chr(0x00A0)}is{chr(0x00A0)}{SECRET}",
    ),
)
def test_unicode_assignment_readiness_reason_http_never_leaks(reason: str) -> None:
    collect = AsyncMock(
        return_value=snapshot(
            sources=(source_status(readiness_reason=reason),),
        )
    )

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    assert SECRET not in response.text
    assert response.json()["sources"][0].get("reason") is None


@pytest.mark.parametrize(
    "value",
    (
        rf"pa\qssword={SECRET}",
        rf"pa\qssword:{SECRET}",
        rf"pa\qssword is {SECRET}",
    ),
)
def test_ascii_assignment_controls_still_fail_closed(value: str) -> None:
    assert redact_diagnostic_value(value) == "[REDACTED]"


def test_unicode_assignment_characters_without_sensitive_fields_are_unchanged() -> None:
    values = (
        f"latency{chr(0xFF1A)}stable",
        f"latency{chr(0x00A0)}is{chr(0x00A0)}stable",
    )

    assert tuple(redact_diagnostic_value(value) for value in values) == values
