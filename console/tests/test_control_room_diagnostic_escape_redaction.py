from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.routers import control_room_surfaces as surface_routes
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from app.services.control_room.operational_diagnostics import (
    build_operational_diagnostics,
)
from control_room_surface_fixtures import (
    OPERATOR,
    snapshot,
    source_state,
    source_status,
)


SECRET = "TOPSECRET"


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = OPERATOR
        return await call_next(request)

    app.dependency_overrides[require_authenticated] = lambda: OPERATOR
    app.include_router(routes.router)
    return TestClient(app)


@pytest.mark.parametrize(
    "value",
    (
        r'{"pa\u0073sword":"TOPSECRET"}',
        r'{"api_\u006bey":"TOPSECRET"}',
        r'{"to\x6ben":"TOPSECRET"}',
        r'{"client\u0053ecret":"TOPSECRET"}',
        r'{"pass\U00000077ord":"TOPSECRET"}',
        '{"pa＼u0073sword":"TOPSECRET"}',
        '{"api＿＼u006bey":"TOPSECRET"}',
        '{"to＼x6ben":"TOPSECRET"}',
        '{"pa\\\u200bu0073sword":"TOPSECRET"}',
        r'{"pa\ｕ0073sword":"TOPSECRET"}',
        r'{"pa\υ0073sword":"TOPSECRET"}',
        '{"pa﹨u0073sword":"TOPSECRET"}',
    ),
)
def test_explicit_escape_forms_are_detection_only_and_redact_secrets(
    value: str,
) -> None:
    redacted = redact_diagnostic_value(value)

    assert redacted == "[REDACTED]"
    assert SECRET not in str(redacted)


def test_escaped_mapping_keys_redact_nested_values() -> None:
    payload = redact_diagnostic_value(
        {
            r"pa\u0073sword": SECRET,
            "nested": {r"api\u004bey": SECRET},
            r"client\u0053ecret": [SECRET],
        }
    )

    assert payload == {
        r"pa\u0073sword": "[REDACTED]",
        "nested": {r"api\u004bey": "[REDACTED]"},
        r"client\u0053ecret": "[REDACTED]",
    }
    assert SECRET not in str(payload)


@pytest.mark.parametrize(
    "key",
    (
        "pa＼u0073sword",
        "api＿＼u006bey",
        "pa\\\u200bu0073sword",
        r"pa\ｕ0073sword",
        r"pa\υ0073sword",
        r"pa\uFF3Cu0073sword",
        r"p\uFF3C141ssword",
        r"api\uFE68137key",
    ),
)
def test_unicode_normalized_escape_keys_redact_without_changing_key(key: str) -> None:
    payload = redact_diagnostic_value({key: SECRET})

    assert payload == {key: "[REDACTED]"}
    assert SECRET not in str(payload)


@pytest.mark.parametrize(
    "value",
    (
        r'{"pa\u005cu0073sword":"TOPSECRET"}',
        r'{"pa\\u0073sword":"TOPSECRET"}',
        r'{"p\u005c141ssword":"TOPSECRET"}',
        r'{"p\x5c141ssword":"TOPSECRET"}',
        r'{"p\U0000005c141ssword":"TOPSECRET"}',
        r'{"p\x61ssw\157rd":"TOPSECRET"}',
        r'{"p\141ssw\u006frd":"TOPSECRET"}',
        r'{"pa\uFF3Cu0073sword":"TOPSECRET"}',
        r'{"api\uFF3Cx5fkey":"TOPSECRET"}',
        r'{"to\uFE68x6ben":"TOPSECRET"}',
        r'{"p\uFF3C141ssword":"TOPSECRET"}',
        r'{"p\uFE68141ssword":"TOPSECRET"}',
        r'{"api\uFF3C137key":"TOPSECRET"}',
        r'{"to\uFF3C153en":"TOPSECRET"}',
        r'{"p\uFF3Cqassword":"TOPSECRET"}',
        r"invalid \u12GG TOPSECRET",
        (r"\u0061" * 129) + "TOPSECRET",
        ("a" * 16_384) + r"\u0061TOPSECRET",
    ),
)
def test_nested_invalid_or_excessive_escapes_fail_closed(value: str) -> None:
    redacted = redact_diagnostic_value(value)

    assert redacted == "[REDACTED]"
    assert SECRET not in str(redacted)


@pytest.mark.parametrize(
    "value",
    (
        r'{"metric":"to\u0074al","value":7}',
        r"Retry \x35 times",
        r"Unicode sample: Caf\u00e9",
        "Metric ＼u0074otal",
    ),
)
def test_legitimate_escaped_text_is_not_transformed(value: str) -> None:
    assert redact_diagnostic_value(value) == value


def _poisoned_snapshot():
    return snapshot(
        diagnostics=(source_state(title='{"pa＼u0073sword":"TOPSECRET"}'),),
        sources=(
            source_status(
                readiness_reason=r'{"api\uFF3Cx5fkey":"TOPSECRET"}',
                readiness_blockers=[
                    r'{"to\υ006ben":"TOPSECRET"}',
                    r"Retry \x35 times",
                ],
                contract_warnings=[
                    '{"client﹨u0053ecret":"TOPSECRET"}',
                    r'{"pass\U00000077ord":"TOPSECRET"}',
                    r'{"p\u005c141ssword":"TOPSECRET"}',
                    r'{"p\uFF3C141ssword":"TOPSECRET"}',
                ],
            ),
        ),
    )


def test_title_reason_blockers_and_lists_are_redacted_directly() -> None:
    payload = build_operational_diagnostics(_poisoned_snapshot()).model_dump(
        mode="json",
        exclude_none=True,
    )

    assert SECRET not in str(payload)
    assert payload["diagnostic_items"][0]["title"] == "[REDACTED]"
    assert payload["sources"][0]["reason"] == "[REDACTED]"
    assert payload["sources"][0]["blockers"] == [
        "[REDACTED]",
        r"Retry \x35 times",
    ]
    assert payload["sources"][0]["warnings"] == [
        "[REDACTED]",
        "[REDACTED]",
        "[REDACTED]",
        "[REDACTED]",
    ]


def test_diagnostics_http_never_serializes_escaped_secret() -> None:
    collect = AsyncMock(return_value=_poisoned_snapshot())
    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    assert SECRET not in response.text
    assert response.json()["sources"][0]["blockers"][1] == r"Retry \x35 times"
    collect.assert_awaited_once_with(OPERATOR)
