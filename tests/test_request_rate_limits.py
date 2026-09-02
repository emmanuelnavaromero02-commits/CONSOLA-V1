from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services.request_rate_limits import (
    api_rate_limit_action,
    client_ip,
    rate_limit,
    rate_limit_api_surface,
    rate_limit_app_content_capability,
    rate_limit_disabled,
    trusted_proxy_ips,
)


def _request(host: str = "10.0.0.10", forwarded_for: str = ""):
    headers = {}
    if forwarded_for:
        headers["x-forwarded-for"] = forwarded_for
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers=headers,
    )


def test_rate_limit_disabled_only_for_test_or_explicit_false():
    assert rate_limit_disabled({"APP_ENV": "test"}) is True
    assert rate_limit_disabled({"APP_ENV": "testing"}) is True
    assert rate_limit_disabled({"APP_ENV": "production"}) is False
    assert rate_limit_disabled({"APP_ENV": "production", "RATE_LIMIT_ENABLED": "false"}) is True
    assert rate_limit_disabled({"APP_ENV": "production", "RATE_LIMIT_ENABLED": "1"}) is False


def test_client_ip_only_trusts_forwarded_for_from_declared_proxy():
    request = _request(host="10.0.0.10", forwarded_for="203.0.113.1, 10.0.0.10")

    assert client_ip(request, trusted_proxies=frozenset()) == "10.0.0.10"
    assert client_ip(request, trusted_proxies=frozenset({"10.0.0.10"})) == "203.0.113.1"


def test_trusted_proxy_ips_parses_comma_separated_env():
    env = {"TRUSTED_PROXY_IPS": "10.0.0.1, 10.0.0.2,, "}

    assert trusted_proxy_ips(env) == frozenset({"10.0.0.1", "10.0.0.2"})


def test_api_rate_limit_action_matches_registered_prefixes():
    assert api_rate_limit_action("/api/copilot/chat") == "/api/copilot"
    assert api_rate_limit_action("/studio/import") == "/studio/import"
    assert api_rate_limit_action("/studio/import/foo") == "/studio/import"
    assert api_rate_limit_action("/apps/demo/content") is None
    assert api_rate_limit_action("/apps/demo/content/") is None
    assert api_rate_limit_action("/healthz") is None


def test_rate_limit_raises_429_when_backend_rejects(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    limiter = MagicMock()
    limiter.check = MagicMock(return_value=False)

    async def _check(*args, **kwargs):
        return False

    limiter.check = _check

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            rate_limit(
                _request(),
                "/auth/login",
                "user@example.com",
                limiter_factory=lambda: limiter,
            )
        )

    assert exc.value.status_code == 429


def test_anonymous_api_surface_deduplicates_the_shared_ip_bucket(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    calls: list[str] = []

    class Limiter:
        async def check(self, key, *_args, **_kwargs):
            calls.append(key)
            return True

    asyncio.run(
        rate_limit_api_surface(
            _request(),
            "/api/copilot/chat",
            None,
            limiter_factory=Limiter,
            trusted_proxies=frozenset(),
        )
    )

    assert calls == ["/api/copilot:10.0.0.10:-"]


def test_app_content_limit_uses_the_authenticated_capability_user(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    calls: list[str] = []

    class Limiter:
        async def check(self, key, *_args, **_kwargs):
            calls.append(key)
            return True

    asyncio.run(
        rate_limit_app_content_capability(
            {"user": "42", "jti": "signed-nonce"},
            limiter_factory=Limiter,
        )
    )

    assert calls == ["/apps/content:user:42"]
