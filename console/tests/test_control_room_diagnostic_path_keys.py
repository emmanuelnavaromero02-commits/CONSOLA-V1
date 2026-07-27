from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.routers import control_room_surfaces as surface_routes
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from app.services.control_room.diagnostic_redaction_keys import (
    _PII_KEYS,
    _SECRET_KEYS,
)
from control_room_surface_fixtures import OPERATOR, snapshot, source_status


SECRET = "path-key-secret-never-publish"
SENSITIVE_PATH_KEYS = (
    r"C:\password",
    r"D:\api_key",
    r"\\server\share\token",
    "E:/client_secret",
    "//server/share/credential",
    r"C:\folder/api-key",
    r"C:\pa\u0073sword",
    r"C:\u0070assword",
    r"C:\\x70assword",
    r"D:\api\u005fkey",
    r"\\server\share\u0074oken",
    r"C\u003a\u005ctoken",
    r"\u005c\u005cserver\u005cshare\u005ccredential",
    r"C:\pa\u005cu0073sword",
    "C：＼pаssword",
    "＼＼server＼share＼to​ken",
    "C:⧵ѕecret",
    "／／server∕share⁄api_key",
    r"C:\pa\qssword",
    r"C:\password\.",
    r"\\server\share\authorization_header-backup",
    r"C:\u0070\u0061\u0073\u0073\u0077\u006f\u0072\u0064",
    "C:\\credentıal",
    r"C:\email.csv",
    r"C:\p\qa\qs\qs\qword",
    r"\\server\share\p\qa\qs\qs\qword",
    r"C:\password.tar.gz.old",
)
BENIGN_PATHS = (
    r"C:\Program Files\OMEGA",
    r"C:\Users\emmanuel\OMEGA",
    r"\\server\share\OMEGA",
)
PATH_PREFIXES = (
    "C:\\",
    "D:\\\\",
    "E:/",
    "F://",
    "C:\\folder\\",
    "C:/folder/",
    "C:\\folder/",
    "\\\\server\\share\\",
    "\\\\server/share/",
    "//server/share/",
    "＼＼server＼share＼",
)
METADATA_SUFFIXES = (
    ".json",
    ".txt",
    ".env",
    "-backup",
    "_value",
    ":$DATA",
)
STRONG_FIELDS = tuple(sorted(_SECRET_KEYS | _PII_KEYS))


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = OPERATOR
        return await call_next(request)

    app.dependency_overrides[require_authenticated] = lambda: OPERATOR
    app.include_router(routes.router)
    return TestClient(app)


@pytest.mark.parametrize("key", SENSITIVE_PATH_KEYS)
def test_path_shaped_mapping_key_terminal_redacts_value(key: str) -> None:
    redacted = redact_diagnostic_value({key: SECRET})

    assert redacted == {key: "[REDACTED]"}
    assert SECRET not in json.dumps(redacted)


@pytest.mark.parametrize("key", SENSITIVE_PATH_KEYS)
def test_nested_path_shaped_mapping_key_never_leaks(key: str) -> None:
    redacted = redact_diagnostic_value(
        {"outer": [{"details": {key: {"value": SECRET}}}]}
    )

    assert redacted == {
        "outer": [{"details": {key: "[REDACTED]"}}],
    }
    assert SECRET not in json.dumps(redacted)


@pytest.mark.parametrize("path", BENIGN_PATHS)
def test_benign_path_values_remain_byte_identical(path: str) -> None:
    assert redact_diagnostic_value(path) == path
    assert redact_diagnostic_value({"path": path}) == {"path": path}
    assert redact_diagnostic_value({path: path}) == {path: path}


@pytest.mark.parametrize(
    "path",
    (r"C:\password", r"D:\api_key", r"\\server\share\token"),
)
def test_path_semantics_depend_on_mapping_key_context(path: str) -> None:
    assert redact_diagnostic_value(path) == path
    assert redact_diagnostic_value({"path": path}) == {"path": path}
    assert redact_diagnostic_value({path: SECRET}) == {path: "[REDACTED]"}


def test_all_strong_fields_with_metadata_suffixes_redact_in_eleven_paths() -> None:
    checked = 0
    for prefix in PATH_PREFIXES:
        for field in STRONG_FIELDS:
            for suffix in METADATA_SUFFIXES:
                key = f"{prefix}{field}{suffix}"
                assert redact_diagnostic_value({key: SECRET}) == {key: "[REDACTED]"}
                checked += 1

    assert checked == 2_838


def test_malformed_escape_recomposition_redacts_every_strong_field_split() -> None:
    checked = 0
    for prefix in PATH_PREFIXES:
        for field in STRONG_FIELDS:
            for split in range(1, len(field)):
                key = f"{prefix}{field[:split]}\\q{field[split:]}"
                nested = {"outer": [{"details": {key: SECRET}}]}
                assert SECRET not in json.dumps(redact_diagnostic_value(nested))
                checked += 1

    assert checked == 4_697


def test_multi_component_malformed_recomposition_is_bounded_and_complete() -> None:
    checked = 0
    for prefix in PATH_PREFIXES[:4]:
        for field in STRONG_FIELDS:
            key = (
                prefix
                + field[0]
                + "".join(f"\\q{character}" for character in field[1:])
            )
            nested = {"outer": [{"details": {key: SECRET}}]}
            assert SECRET not in json.dumps(redact_diagnostic_value(nested))
            checked += 1

    assert checked == 172


def test_dot_segments_use_the_last_effective_semantic_component() -> None:
    checked = 0
    for prefix in PATH_PREFIXES:
        for field in STRONG_FIELDS:
            for suffix in ("\\.", "/.", "\\..", "/./"):
                key = f"{prefix}{field}{suffix}"
                assert redact_diagnostic_value({key: SECRET})[key] == "[REDACTED]"
                checked += 1

    assert checked == 1_892


def test_fully_encoded_terminals_redact_after_drive_unc_and_mixed_prefixes() -> None:
    prefixes = ("C:", "D:", r"\\server\share", "//server/share", "C:/folder")
    checked = 0
    for prefix in prefixes:
        for field in STRONG_FIELDS:
            encoded_forms = (
                "".join(f"\\u{ord(character):04x}" for character in field),
                "".join(f"\\U{ord(character):08x}" for character in field),
                "".join(f"\\x{ord(character):02x}" for character in field),
            )
            for encoded in encoded_forms:
                key = f"{prefix}{encoded}"
                assert redact_diagnostic_value({key: SECRET})[key] == "[REDACTED]"
                checked += 1

    assert checked == 645


@pytest.mark.parametrize(
    "key",
    (
        "C:\\pɑssword",
        "C:\\tᴏken",
        "C:\\seϲret",
        "C:\\credentıal",
        "C:\\paꜱꜱword",
        "C:\\passᴡord",
    ),
)
def test_unicode_terminal_confusables_redact(key: str) -> None:
    assert redact_diagnostic_value({key: SECRET}) == {key: "[REDACTED]"}


@pytest.mark.parametrize("suffix", (".csv", ".yaml", ".tar.gz", ":stream"))
def test_extension_and_ads_annotations_keep_strong_terminal_prefix(suffix: str) -> None:
    key = f"C:\\email{suffix}"

    assert redact_diagnostic_value({key: SECRET}) == {key: "[REDACTED]"}


def test_layered_metadata_suffixes_redact_in_bounded_matrix() -> None:
    suffixes = (
        ".tar.gz.old",
        ".json.bak.extra",
        "-backup-backup-backup",
        "_value.json.old",
    )
    checked = 0
    for prefix in PATH_PREFIXES[:8]:
        for field in STRONG_FIELDS:
            for suffix in suffixes:
                key = f"{prefix}{field}{suffix}"
                assert redact_diagnostic_value({key: SECRET})[key] == "[REDACTED]"
                checked += 1

    assert checked == 1_376


def test_generic_path_words_remain_byte_identical_in_mapping_context() -> None:
    checked = 0
    for prefix in PATH_PREFIXES:
        for terminal in ("binary-tools", "string-utils", "key"):
            path = f"{prefix}{terminal}"
            assert redact_diagnostic_value({path: path}) == {path: path}
            assert redact_diagnostic_value({"path": path}) == {"path": path}
            checked += 1

    assert checked == 33


def test_path_key_component_budget_is_deterministic_and_contextual() -> None:
    at_limit = "C:\\" + "\\".join(["part"] * 32)
    over_limit = "C:\\" + "\\".join(["part"] * 33)

    assert redact_diagnostic_value({at_limit: at_limit}) == {at_limit: at_limit}
    assert redact_diagnostic_value({over_limit: SECRET}) == {over_limit: "[REDACTED]"}
    assert redact_diagnostic_value({"path": over_limit}) == {"path": over_limit}


@pytest.mark.parametrize(
    "key",
    (
        r"C:\password",
        r"\\server\share\token",
        r"C:\pa\u0073sword",
        "／／server∕share⁄api_key",
        r"C:\pa\qssword",
        r"C:\password\.",
        r"\\server\share\authorization_header-backup",
        r"C:\u0070\u0061\u0073\u0073\u0077\u006f\u0072\u0064",
        "C:\\credentıal",
        r"C:\p\qa\qs\qs\qword",
        r"C:\password.tar.gz.old",
    ),
)
def test_path_shaped_key_secret_never_crosses_diagnostics_http(key: str) -> None:
    collect = AsyncMock(
        return_value=snapshot(
            sources=(
                source_status(
                    readiness_reason={"nested": {key: SECRET}},
                ),
            ),
        )
    )

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    assert SECRET not in response.text
    assert "[REDACTED]" in response.json()["sources"][0]["reason"]
    collect.assert_awaited_once_with(OPERATOR)
