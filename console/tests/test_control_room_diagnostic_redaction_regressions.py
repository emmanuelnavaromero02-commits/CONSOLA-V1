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
MALFORMED_PATTERNS = (
    r"\q",
    r"\uGG",
    r"\u1G",
    r"\u12GG",
    r"\xGG",
    r"\x4G",
    r"\uD800GG",
    r"\uDFFFGG",
    r"\U00110000GG",
    r"\UFFFFFFFFGG",
    r"\\q",
    r"\u005cq",
)
FIELD_SPLITS = tuple(
    (word[:split], word[split:])
    for word in ("password", "api_key", "token")
    for split in range(1, len(word))
)
STRUCTURED_PATHS_V1 = (
    r'{"path":"C:\\Users\\OMEGA"}',
    r'{"path":"C:\\\\Users\\\\OMEGA"}',
    r'{"path":"C:\\Utility\\OMEGA"}',
    r'{"path":"C:\\\\Utility\\\\OMEGA"}',
    r'{"path":"C:\\xray\\OMEGA"}',
    r'{"path":"C:\\\\xray\\\\OMEGA"}',
    r'{"path":"D:\\Users\\OMEGA"}',
    r'{"path":"D:\\\\Users\\\\OMEGA"}',
    r'{"path":"\\\\server\\share\\OMEGA"}',
    r'{"path":"\\\\server\\\\share\\\\OMEGA"}',
    r'{"path":"\\\\\\\\server\\\\share\\\\OMEGA"}',
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


@pytest.mark.parametrize(
    "key",
    (
        r"pa\qssword",
        r"api\qkey",
        r"pa\u12ssword",
        r"pa\uD800ssword",
        r"pa\U00110000ssword",
        r"\\password",
    ),
)
def test_malformed_sensitive_mapping_keys_fail_closed(key: str) -> None:
    payload = redact_diagnostic_value({"outer": {key: SECRET}})

    assert payload == {"outer": {key: "[REDACTED]"}}
    assert SECRET not in str(payload)


def test_bounded_malformed_mapping_key_matrix_never_leaks() -> None:
    checked = 0
    for left, right in FIELD_SPLITS:
        for malformed in MALFORMED_PATTERNS:
            key = f"{left}{malformed}{right}"
            payload = redact_diagnostic_value(
                {"direct": {key: SECRET}, "nested": [{key: SECRET}]}
            )

            assert SECRET not in str(payload), key
            assert payload["direct"][key] == "[REDACTED]"
            assert payload["nested"][0][key] == "[REDACTED]"
            checked += 1

    assert checked == 204


def test_bounded_malformed_text_assignment_matrix_never_leaks() -> None:
    checked = 0
    for left, right in FIELD_SPLITS:
        for malformed in MALFORMED_PATTERNS:
            key = f"{left}{malformed}{right}"
            values = (
                f"{key}={SECRET}",
                f"[x,{key}:{SECRET}]",
                f'"{key}":"{SECRET}"',
                f"{key} is {SECRET}",
            )
            for value in values:
                redacted = redact_diagnostic_value(value)
                assert redacted == "[REDACTED]", value
                assert SECRET not in str(redacted)
                checked += 1

    assert checked == 816


@pytest.mark.parametrize(
    "key",
    (
        r"p\uGGa\uGGs\uGGsword",
        r"t\qo\qk\qen",
    ),
)
def test_malformed_field_nesting_beyond_two_rounds_fails_closed(key: str) -> None:
    payload = redact_diagnostic_value({key: SECRET})

    assert payload == {key: "[REDACTED]"}
    assert SECRET not in str(payload)


@pytest.mark.parametrize(
    "value",
    (
        r'{pa\qssword:"TOPSECRET"}',
        r'{/*comment*/"pa\qssword":"TOPSECRET"}',
        r'{"metric":1}; "pa\qssword":"TOPSECRET"',
    ),
)
def test_malformed_structured_assignment_fragments_fail_closed(value: str) -> None:
    redacted = redact_diagnostic_value(value)

    assert redacted == "[REDACTED]"
    assert SECRET not in str(redacted)


@pytest.mark.parametrize(
    "path",
    (
        r"C:\Program Files\OMEGA",
        r"C:\Users\emmanuel\OMEGA",
        r"D:\Datos\OMEGA",
        r"\\server\share\OMEGA",
        r'{"path":"C:\\Program Files\\OMEGA"}',
        r'{"metric":1}; C:\Program Files\OMEGA',
        r"{C:\Program Files\OMEGA}",
        r'"C:\Program Files\OMEGA": visible',
        r'{"metric":1}; "C:\Program Files\OMEGA": visible',
        r'{"metric":1}; "\\server\share\OMEGA": visible',
        r"path=C:\Program Files\OMEGA",
        r"C:\Program Files\OMEGA / docs",
    ),
)
def test_normal_windows_paths_remain_byte_identical(path: str) -> None:
    assert redact_diagnostic_value(path) == path


def test_v1_structured_windows_path_matrix_remains_byte_identical() -> None:
    checked = 0
    for path in STRUCTURED_PATHS_V1:
        assert redact_diagnostic_value(path) == path
        checked += 1

    assert checked == 11


@pytest.mark.parametrize(
    "value",
    (
        r'{"path":"C:\\Users\\OMEGA"}; pa\qssword=TOPSECRET',
        r'{"path":"C:\\Users\\OMEGA","pa\qssword":"TOPSECRET"}',
        r'{"path":"C:\\Users\\pa\qssword=TOPSECRET"}',
        r'{"path":"C:\\Users\\OMEGA",' r'"note":"\u005cu0070assword=TOPSECRET"}',
        r'{"path":"C:\\u005cu0042earer TOPSECRET"}',
        r'{"path":"C:\\\\u005cu0042earer TOPSECRET"}',
        r'{"path":"C:\\Ｂｅａｒｅｒ TOPSECRET"}',
    ),
)
def test_structured_path_scope_does_not_hide_secret_assignments(value: str) -> None:
    redacted = redact_diagnostic_value(value)

    assert redacted == "[REDACTED]"
    assert SECRET not in str(redacted)


def test_structured_path_exception_preserves_global_escape_bound() -> None:
    value = '{"path":"C:' + r"\\segment" * 129 + '"}'

    assert redact_diagnostic_value(value) == "[REDACTED]"


@pytest.mark.parametrize(
    "path",
    (
        r"C:\Program Files\OMEGA",
        r"C:\Users\emmanuel\OMEGA",
        r"D:\Datos\OMEGA",
        r"\\server\share\OMEGA",
    ),
)
def test_normal_windows_path_mapping_keys_remain_byte_identical(path: str) -> None:
    assert redact_diagnostic_value({path: path}) == {path: path}


@pytest.mark.parametrize(
    "reason",
    (
        r'{"metric":1}; "pa\qssword":"TOPSECRET"',
        r"pa\qssword=TOPSECRET",
        r"[x,pa\qssword:TOPSECRET]",
        r"pa\qssword is TOPSECRET",
    ),
)
def test_readiness_reason_http_never_leaks_malformed_assignment_secret(
    reason: str,
) -> None:
    poisoned = snapshot(
        sources=(
            source_status(
                readiness_reason=reason,
            ),
        ),
    )
    collect = AsyncMock(return_value=poisoned)

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    assert SECRET not in response.text
    assert response.json()["sources"][0].get("reason") is None
    collect.assert_awaited_once_with(OPERATOR)


@pytest.mark.parametrize(
    "reason",
    (
        r'{"path":"C:\\Utility\\OMEGA"}',
        r'{"path":"C:\\\\Users\\\\OMEGA"}',
        r'{"path":"\\\\server\\share\\OMEGA"}',
    ),
)
def test_structured_windows_path_readiness_reason_http_is_omitted(
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
    assert response.json()["sources"][0].get("reason") is None
    collect.assert_awaited_once_with(OPERATOR)
