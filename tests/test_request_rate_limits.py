from __future__ import annotations

import asyncio
import ipaddress
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


def test_trusted_proxy_ips_parses_addresses_and_networks():
    env = {"TRUSTED_PROXY_IPS": "10.0.0.1, 10.0.1.0/24,, fd00::/8, not-an-ip, 10.0.4.7/24"}

    assert trusted_proxy_ips(env) == frozenset(
        {
            ipaddress.ip_network("10.0.0.1/32"),
            ipaddress.ip_network("10.0.1.0/24"),
            ipaddress.ip_network("fd00::/8"),
            ipaddress.ip_network("10.0.4.0/24"),
        }
    )
    assert trusted_proxy_ips({}) == frozenset()


ALB_SUBNETS = frozenset({"10.0.1.0/24", "10.0.4.0/24"})


@pytest.mark.parametrize(
    "forwarded_for,expected",
    [
        ("198.51.100.7", "198.51.100.7"),
        ("1.1.1.1, 198.51.100.7", "198.51.100.7"),
        ("10.0.1.99, 198.51.100.7", "198.51.100.7"),
        ("garbage, 198.51.100.7", "198.51.100.7"),
        ("1.1.1.1, 198.51.100.7, 10.0.4.12", "198.51.100.7"),
        ("198.51.100.7, 10.0.1.5, 10.0.4.12", "198.51.100.7"),
        ("2001:db8::1", "2001:db8::1"),
        ("::ffff:198.51.100.7", "198.51.100.7"),
        ("198.51.100.7, 2001:db8::1", "2001:db8::1"),
        ("198.51.100.7:4431", "10.0.1.20"),
        ("198.51.100.7, not-an-ip", "10.0.1.20"),
        ("198.51.100.7,", "10.0.1.20"),
        ("", "10.0.1.20"),
        ("10.0.1.8, 10.0.4.9", "10.0.1.8"),
    ],
)
def test_client_ip_walks_forwarded_for_from_the_right(forwarded_for, expected):
    request = _request(host="10.0.1.20", forwarded_for=forwarded_for)

    assert client_ip(request, trusted_proxies=ALB_SUBNETS) == expected
    assert client_ip(request, trusted_proxies=trusted_proxy_ips({"TRUSTED_PROXY_IPS": ",".join(ALB_SUBNETS)})) == expected


def test_client_ip_ignores_forwarded_for_from_untrusted_peers():
    for host in ("198.51.100.9", "172.18.0.5", "10.0.2.15", "unknown", "not-an-ip"):
        request = _request(host=host, forwarded_for="203.0.113.50")
        assert client_ip(request, trusted_proxies=ALB_SUBNETS) == host


def test_client_ip_trusts_ipv6_proxy_networks():
    request = _request(host="fd00::20", forwarded_for="203.0.113.50, fd00::10")

    assert client_ip(request, trusted_proxies=frozenset({"fd00::/8"})) == "203.0.113.50"
    assert client_ip(request, trusted_proxies=ALB_SUBNETS) == "fd00::20"


def test_client_ip_reads_trusted_proxies_from_the_environment(monkeypatch):
    request = _request(host="10.0.4.33", forwarded_for="6.6.6.6, 203.0.113.60")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.0.0.0/16")
    assert client_ip(request) == "203.0.113.60"
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "")
    assert client_ip(request) == "10.0.4.33"


def test_distinct_clients_behind_one_alb_node_get_distinct_rate_limit_keys():
    limiter = MagicMock()
    seen: list[str] = []

    async def check(key, limit, window, sensitive=False):
        seen.append(key)
        return True

    limiter.check = check
    for client in ("198.51.100.1", "198.51.100.2"):
        request = _request(host="10.0.1.20", forwarded_for=f"10.0.1.99, {client}")
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("APP_ENV", "production")
            patch.delenv("RATE_LIMIT_ENABLED", raising=False)
            asyncio.run(
                rate_limit(
                    request,
                    "/auth/login",
                    "user@example.com",
                    limiter_factory=lambda: limiter,
                    trusted_proxies=ALB_SUBNETS,
                )
            )
    assert "/auth/login:198.51.100.1:-" in seen
    assert "/auth/login:198.51.100.2:-" in seen
    assert not any("10.0.1" in key for key in seen)


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
