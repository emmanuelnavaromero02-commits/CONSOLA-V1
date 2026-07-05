from __future__ import annotations

import pytest

from app.domains.security.cors import allowed_origins


def test_allowed_origins_dev_default_keeps_console_and_workspace_local_origins():
    assert allowed_origins({"APP_ENV": "test"}) == [
        "http://localhost:8000",
        "http://localhost:8001",
    ]


def test_allowed_origins_prod_requires_explicit_env():
    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS must be set"):
        allowed_origins({"APP_ENV": "production"})


def test_allowed_origins_prod_rejects_empty_env():
    with pytest.raises(RuntimeError, match="empty value in production"):
        allowed_origins({"APP_ENV": "prod", "ALLOWED_ORIGINS": " "})


def test_allowed_origins_dev_empty_env_warns_and_falls_back():
    warnings: list[str] = []

    assert allowed_origins(
        {"APP_ENV": "test", "ALLOWED_ORIGINS": ""},
        warn=warnings.append,
    ) == ["http://localhost:8000", "http://localhost:8001"]
    assert warnings == [
        "ALLOWED_ORIGINS is set but empty; falling back to the "
        "localhost default. This is only safe in dev/test."
    ]


def test_allowed_origins_filters_wildcard_with_warning():
    warnings: list[str] = []

    assert allowed_origins(
        {
            "APP_ENV": "production",
            "ALLOWED_ORIGINS": "https://console.example.com, *, https://workspace.example.com",
        },
        warn=warnings.append,
    ) == ["https://console.example.com", "https://workspace.example.com"]
    assert warnings == [
        "ALLOWED_ORIGINS=* is incompatible with "
        "allow_credentials=True; ignoring wildcard entry"
    ]


def test_allowed_origins_ignores_empty_chunks():
    assert allowed_origins(
        {
            "APP_ENV": "production",
            "ALLOWED_ORIGINS": "https://console.example.com, ,https://workspace.example.com,",
        }
    ) == ["https://console.example.com", "https://workspace.example.com"]
