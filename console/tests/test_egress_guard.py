from __future__ import annotations

import socket

import pytest

from app.services import egress_guard
from app.services.adapters.base import AdapterConfigurationError
from app.services.adapters.http_writeback import HttpWriteBackAdapter


def test_egress_guard_blocks_ssrf_and_prod_http(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(egress_guard.EgressGuardError, match="https"):
        egress_guard.validate_url("http://example.com/openapi.json", label="OpenAPI spec URL")

    with pytest.raises(egress_guard.EgressGuardError, match="credentials"):
        egress_guard.validate_url("https://user:pass@example.com/openapi.json", label="OpenAPI spec URL")

    with pytest.raises(egress_guard.EgressGuardError, match="metadata"):
        egress_guard.validate_url("https://169.254.169.254/latest/meta-data", label="OpenAPI spec URL")

    monkeypatch.setattr(
        egress_guard.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(egress_guard.EgressGuardError, match="non-public"):
        egress_guard.validate_url("https://api.example.com/openapi.json", label="OpenAPI spec URL")

    monkeypatch.setattr(
        egress_guard.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ],
    )
    with pytest.raises(egress_guard.EgressGuardError, match="non-public"):
        egress_guard.validate_url("https://api.example.com/openapi.json", label="OpenAPI spec URL")

    monkeypatch.setattr(
        egress_guard.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    assert egress_guard.validate_url("https://api.example.com/openapi.json", label="OpenAPI spec URL")


@pytest.mark.asyncio
async def test_http_writeback_blocks_imds_before_sending(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    async def fail_open(*_args, **_kwargs):
        raise AssertionError("HTTP client should not be opened for blocked URL")

    monkeypatch.setattr("httpx.AsyncClient", fail_open)
    adapter = HttpWriteBackAdapter()

    with pytest.raises(AdapterConfigurationError, match="metadata"):
        await adapter.execute(
            {"writeback_path": "/latest/meta-data", "item_id": "item-1"},
            {
                "base_url": "https://169.254.169.254",
                "auth_method": "bearer_token",
                "token": "secret-token",
            },
        )
