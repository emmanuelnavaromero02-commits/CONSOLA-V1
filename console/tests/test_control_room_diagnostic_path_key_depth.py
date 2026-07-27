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


SECRET = "long-path-metadata-never-publish"
LONG_EXTENSION = ".configurationbackup"
PREFIXES = ("C:\\", "D:/", "\\\\server\\share\\", "//server/share/")


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = OPERATOR
        return await call_next(request)

    app.dependency_overrides[require_authenticated] = lambda: OPERATOR
    app.include_router(routes.router)
    return TestClient(app)


def test_long_metadata_extension_redacts_all_strong_fields_in_four_paths() -> None:
    checked = 0
    for prefix in PREFIXES:
        for field in sorted(_SECRET_KEYS | _PII_KEYS):
            key = f"{prefix}{field}{LONG_EXTENSION}"
            assert redact_diagnostic_value({key: SECRET}) == {key: "[REDACTED]"}
            checked += 1

    assert checked == 172


@pytest.mark.parametrize("field", ("password", "email"))
def test_long_extension_redacts_direct_and_nested_values(field: str) -> None:
    key = f"C:\\{field}{LONG_EXTENSION}"
    direct = redact_diagnostic_value({key: SECRET})
    nested = redact_diagnostic_value({"outer": [{"details": {key: SECRET}}]})

    assert direct == {key: "[REDACTED]"}
    assert SECRET not in json.dumps(nested)


def test_single_character_malformed_escape_components_recompose() -> None:
    key = r"C:\pa\q\ssword"

    assert redact_diagnostic_value({key: SECRET}) == {key: "[REDACTED]"}


@pytest.mark.parametrize("field", ("password", "email"))
def test_long_extension_secret_never_crosses_diagnostics_http(field: str) -> None:
    key = f"C:\\{field}{LONG_EXTENSION}"
    collect = AsyncMock(
        return_value=snapshot(
            sources=(source_status(readiness_reason={key: SECRET}),),
        )
    )

    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = _client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    assert SECRET not in response.text
    assert response.json()["sources"][0].get("reason") is None


def test_long_metadata_on_generic_or_business_path_is_byte_identical() -> None:
    checked = 0
    for prefix in PREFIXES:
        for terminal in ("OMEGA", "binary-tools", "string-utils", "key"):
            path = f"{prefix}{terminal}{LONG_EXTENSION}"
            assert redact_diagnostic_value({path: path}) == {path: path}
            assert redact_diagnostic_value({"path": path}) == {"path": path}
            checked += 1

    assert checked == 16


def test_strong_words_inside_benign_path_names_remain_byte_identical() -> None:
    terminals = (
        "authorization-guide",
        "cookie-policy",
        "dsn-guide",
        "database_url_docs",
    )
    checked = 0
    for prefix in PREFIXES:
        for terminal in terminals:
            path = f"{prefix}{terminal}"
            assert redact_diagnostic_value({path: path}) == {path: path}
            assert redact_diagnostic_value({"path": path}) == {"path": path}
            checked += 1

    assert checked == 16
