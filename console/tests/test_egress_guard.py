from __future__ import annotations

import socket

import pytest

from app.services import egress_guard
from app.services.adapters.base import AdapterConfigurationError, AdapterExecutionError
from app.services.adapters.circuit_breaker import CartridgeCircuitBreaker
from app.services.adapters.http_writeback import HttpWriteBackAdapter


@pytest.fixture(autouse=True)
def _reset_http_writeback_breaker():
    CartridgeCircuitBreaker.reset("external")
    yield
    CartridgeCircuitBreaker.reset("external")


def test_egress_guard_blocks_ssrf_and_prod_http(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(egress_guard.EgressGuardError, match="https"):
        egress_guard.validate_url(
            "http://example.com/openapi.json", label="OpenAPI spec URL"
        )

    with pytest.raises(egress_guard.EgressGuardError, match="credentials"):
        egress_guard.validate_url(
            "https://user:pass@example.com/openapi.json", label="OpenAPI spec URL"
        )

    with pytest.raises(egress_guard.EgressGuardError, match="metadata"):
        egress_guard.validate_url(
            "https://169.254.169.254/latest/meta-data", label="OpenAPI spec URL"
        )

    monkeypatch.setattr(
        egress_guard.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    with pytest.raises(egress_guard.EgressGuardError, match="non-public"):
        egress_guard.validate_url(
            "https://api.example.com/openapi.json", label="OpenAPI spec URL"
        )

    monkeypatch.setattr(
        egress_guard.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ],
    )
    with pytest.raises(egress_guard.EgressGuardError, match="non-public"):
        egress_guard.validate_url(
            "https://api.example.com/openapi.json", label="OpenAPI spec URL"
        )

    monkeypatch.setattr(
        egress_guard.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    assert egress_guard.validate_url(
        "https://api.example.com/openapi.json", label="OpenAPI spec URL"
    )


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


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", (0, 101, 302, 600, 999))
async def test_http_writeback_unconfirmed_post_status_is_ambiguous(
    monkeypatch, status_code
):
    async def response(*_args, **_kwargs):
        return egress_guard.PinnedHTTPResponse(status_code, {}, b"")

    monkeypatch.setattr(egress_guard, "pinned_request", response)
    with pytest.raises(AdapterExecutionError) as exc:
        await HttpWriteBackAdapter().execute(
            {"writeback_path": "/writeback", "item_id": "item-1"},
            {
                "base_url": "https://writeback.example.invalid",
                "auth_method": "bearer_token",
                "token": "secret-token",
            },
        )

    assert exc.value.status_code == status_code
    assert exc.value.outcome_ambiguous is True


@pytest.mark.asyncio
async def test_http_writeback_post_dispatch_guard_is_ambiguous(monkeypatch):
    async def response(*_args, **_kwargs):
        raise egress_guard.EgressGuardError(
            "response exceeds size limit", request_dispatched=True
        )

    monkeypatch.setattr(egress_guard, "pinned_request", response)
    with pytest.raises(AdapterExecutionError) as exc:
        await HttpWriteBackAdapter().execute(
            {"writeback_path": "/writeback", "item_id": "item-1"},
            {
                "base_url": "https://writeback.example.invalid",
                "auth_method": "bearer_token",
                "token": "secret-token",
            },
        )

    assert exc.value.outcome_ambiguous is True
