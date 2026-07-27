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
ESCAPE_SHAPED_SEGMENTS_V2 = ("u1234", "x41", "U00000041")
BENIGN_PREFIX_CONTROLS_V2 = ("abcD", "u12GG")
SENSITIVE_ESCAPE_SEGMENTS_V3 = (
    "u0042earer TOPSECRET",
    "u005cu0042earer TOPSECRET",
)
SPLIT_SENSITIVE_PATHS_V4 = (
    ("u0042", "u0065arer TOPSECRET"),
    ("u0042", "u0065", "u0061", "u0072", "u0065", "u0072 TOPSECRET"),
)


def _structured_paths(segment: str) -> tuple[str, ...]:
    return (
        rf'{{"path":"C:\\{segment}\\OMEGA"}}',
        rf'{{"path":"C:\\\\{segment}\\\\OMEGA"}}',
        rf'{{"path":"\\\\server\\{segment}\\OMEGA"}}',
        rf'{{"path":"\\\\server\\\\{segment}\\\\OMEGA"}}',
        rf'{{"path":"\\\\\\\\server\\\\{segment}\\\\OMEGA"}}',
    )


def _structured_split_paths(segments: tuple[str, ...]) -> tuple[str, ...]:
    standard = "\\" * 2
    doubled = "\\" * 4
    unc_standard = "\\" * 4
    unc_doubled = "\\" * 8
    standard_tail = standard.join(segments)
    doubled_tail = doubled.join(segments)
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


def test_v2_escape_shaped_path_segment_matrix_is_byte_identical() -> None:
    checked = 0
    for segment in ESCAPE_SHAPED_SEGMENTS_V2:
        for value in _structured_paths(segment):
            assert redact_diagnostic_value(value) == value
            checked += 1

    assert checked == 15


def test_v2_benign_prefix_path_controls_are_byte_identical() -> None:
    checked = 0
    for segment in BENIGN_PREFIX_CONTROLS_V2:
        for value in _structured_paths(segment):
            assert redact_diagnostic_value(value) == value
            checked += 1

    assert checked == 10


def test_v3_sensitive_path_escape_segment_matrix_never_leaks() -> None:
    checked = 0
    for segment in SENSITIVE_ESCAPE_SEGMENTS_V3:
        for value in _structured_paths(segment):
            redacted = redact_diagnostic_value(value)
            assert redacted == "[REDACTED]", value
            assert SECRET not in str(redacted)
            checked += 1

    assert checked == 10


def test_v4_split_sensitive_whole_path_matrix_never_leaks() -> None:
    checked = 0
    for segments in SPLIT_SENSITIVE_PATHS_V4:
        for value in _structured_split_paths(segments):
            redacted = redact_diagnostic_value(value)
            assert redacted == "[REDACTED]", value
            assert SECRET not in str(redacted)
            checked += 1

    assert checked == 10


@pytest.mark.parametrize(
    "value",
    (
        r'{"path":"D:\\abcD"}',
        r'{"path":"D:\\\\abcD"}',
        r'{"path":"C:\\u12GG\\OMEGA"}',
        r'{"path":"C:\\\\u12GG\\\\OMEGA"}',
    ),
)
def test_exact_non_escape_path_controls_are_byte_identical(value: str) -> None:
    assert redact_diagnostic_value(value) == value


@pytest.mark.parametrize(
    "reason",
    (
        r'{"path":"C:\\\\u1234\\\\OMEGA"}',
        r'{"path":"C:\\x41\\OMEGA"}',
        r'{"path":"\\\\server\\U00000041\\OMEGA"}',
    ),
)
def test_escape_shaped_path_readiness_reason_http_is_byte_identical(
    reason: str,
) -> None:
    collect = AsyncMock(
        return_value=snapshot(
            sources=(source_status(readiness_reason=reason),),
        )
    )

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    assert response.json()["sources"][0]["reason"] == reason
    collect.assert_awaited_once_with(OPERATOR)


@pytest.mark.parametrize(
    "reason",
    (
        r'{"path":"C:\\\\u0042earer TOPSECRET"}',
        r'{"path":"C:\\\\u005cu0042earer TOPSECRET"}',
        r'{"path":"\\\\server\\u0042earer TOPSECRET\\OMEGA"}',
        r'{"path":"\\\\server\\\\u005cu0042earer TOPSECRET\\\\OMEGA"}',
        r'{"path":"C:\\\\u0042\\\\u0065arer TOPSECRET"}',
        r'{"path":"C:\\u0042\\u0065\\u0061\\u0072\\u0065\\u0072 TOPSECRET"}',
        r'{"path":"\\\\server\\u0042\\u0065arer TOPSECRET"}',
    ),
)
def test_sensitive_path_escape_readiness_reason_http_never_leaks(
    reason: str,
) -> None:
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


@pytest.mark.parametrize(
    "value",
    (
        r'{"path":"C:\\u1234\\OMEGA","password":"TOPSECRET"}',
        r'{"path":"C:\\x41\\password=TOPSECRET"}',
        r'{"path":"C:\\U00000041\\OMEGA"}; pa\qssword=TOPSECRET',
        r'{"path":"C:\\u1234\\OMEGA",' r'"note":"\u005cu005cu0070assword=TOPSECRET"}',
    ),
)
def test_path_scope_never_hides_adjacent_or_nested_secrets(value: str) -> None:
    redacted = redact_diagnostic_value(value)

    assert redacted == "[REDACTED]"
    assert SECRET not in str(redacted)


def test_path_scope_does_not_relax_sibling_escape_nesting_limit() -> None:
    value = r'{"path":"C:\\u1234\\OMEGA",' r'"note":"\\\\u0070assword TOPSECRET"}'

    assert redact_diagnostic_value(value) == "[REDACTED]"


def test_path_segment_derivation_beyond_two_rounds_fails_closed() -> None:
    value = r'{"path":"C:\\u005cu005cu0042earer TOPSECRET"}'

    assert redact_diagnostic_value(value) == "[REDACTED]"


def test_whole_path_escape_limit_is_exact() -> None:
    def value_with(segment_count: int) -> str:
        segments = "".join(
            rf"\\u{codepoint:04x}"
            for codepoint in range(0x4E00, 0x4E00 + segment_count)
        )
        return '{"path":"C:' + segments + '"}'

    at_limit = value_with(128)
    over_limit = value_with(129)

    assert redact_diagnostic_value(at_limit) == at_limit
    assert redact_diagnostic_value(over_limit) == "[REDACTED]"


def test_whole_path_detection_form_limit_is_bounded() -> None:
    codepoints = (
        0x00A8,
        0x00BC,
        0x00BD,
        0x00BE,
        0x01C4,
        0x01C5,
        0x01C6,
        0x0340,
        0x03D0,
        0x03D2,
        0x03F0,
    )

    def value_with(field_count: int) -> str:
        return " ".join(
            rf'{{"path":"C:\\u{codepoint:04x}"}}'
            for codepoint in codepoints[:field_count]
        )

    below_limit = value_with(10)
    over_limit = value_with(11)

    assert redact_diagnostic_value(below_limit) == below_limit
    assert redact_diagnostic_value(over_limit) == "[REDACTED]"


def test_whole_path_nested_escape_limit_is_exact() -> None:
    def value_with(segment_count: int) -> str:
        return '{"path":"C:' + r"\\u005cu0041" * segment_count + '"}'

    at_limit = value_with(64)
    over_limit = value_with(65)

    assert redact_diagnostic_value(at_limit) == at_limit
    assert redact_diagnostic_value(over_limit) == "[REDACTED]"


@pytest.mark.parametrize("segment", ESCAPE_SHAPED_SEGMENTS_V2)
def test_mixed_escape_width_path_shape_still_fails_closed(segment: str) -> None:
    value = rf'{{"path":"C:\\{segment}\\\\OMEGA"}}'

    assert redact_diagnostic_value(value) == "[REDACTED]"
