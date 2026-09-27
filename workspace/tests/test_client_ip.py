from __future__ import annotations

import asyncio
import importlib
import ipaddress
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)

from app.services.client_ip import client_ip, trusted_proxy_ips  # noqa: E402

ALB_SUBNETS = frozenset({"10.0.1.0/24", "10.0.4.0/24"})
ALB_NODE = "10.0.1.20"


def _request(host: str = ALB_NODE, forwarded_for: str = ""):
    headers = {"x-forwarded-for": forwarded_for} if forwarded_for else {}
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers)


@pytest.mark.parametrize(
    "forwarded_for,expected",
    [
        ("198.51.100.7", "198.51.100.7"),
        ("1.1.1.1, 198.51.100.7", "198.51.100.7"),
        ("10.0.1.99, 198.51.100.7", "198.51.100.7"),
        ("1.1.1.1, 198.51.100.7, 10.0.4.12", "198.51.100.7"),
        ("2001:db8::1", "2001:db8::1"),
        ("::ffff:198.51.100.7", "198.51.100.7"),
        ("198.51.100.7:4431", ALB_NODE),
        ("198.51.100.7, not-an-ip", ALB_NODE),
        ("", ALB_NODE),
        ("10.0.1.8, 10.0.4.9", "10.0.1.8"),
    ],
)
def test_client_ip_walks_forwarded_for_from_the_right(forwarded_for, expected):
    assert client_ip(_request(forwarded_for=forwarded_for), trusted_proxies=ALB_SUBNETS) == expected


def test_forwarded_for_is_ignored_from_untrusted_peers_or_without_proxies():
    for host in ("198.51.100.9", "172.18.0.5", "unknown"):
        assert client_ip(_request(host, "203.0.113.50"), trusted_proxies=ALB_SUBNETS) == host
    assert client_ip(_request(forwarded_for="203.0.113.50"), trusted_proxies=frozenset()) == ALB_NODE


def test_trusted_proxy_ips_parses_addresses_and_networks():
    assert trusted_proxy_ips({"TRUSTED_PROXY_IPS": "10.0.1.0/24, 10.0.0.9,, bogus, fd00::/8"}) == frozenset(
        {
            ipaddress.ip_network("10.0.1.0/24"),
            ipaddress.ip_network("10.0.0.9/32"),
            ipaddress.ip_network("fd00::/8"),
        }
    )


def test_workspace_rate_limit_keys_use_the_client_behind_the_proxy(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.0.1.0/24,10.0.4.0/24")
    main = importlib.reload(importlib.import_module("app.main"))
    monkeypatch.setattr(main, "_rate_limit_disabled", lambda: False)
    seen: list[str] = []

    class Limiter:
        async def check(self, key, limit, window, sensitive=False):
            seen.append(key)
            return True

    monkeypatch.setattr(main, "get_rate_limiter", lambda: Limiter())
    path = next(iter(main.WORKSPACE_RATE_LIMITS))
    for client in ("198.51.100.1", "198.51.100.2"):
        request = _request(forwarded_for=f"10.0.1.99, {client}")
        asyncio.run(main._rate_limit_workspace_surface(request, path, {"id": 7}))
    assert f"{path}:198.51.100.1:-" in seen
    assert f"{path}:198.51.100.2:-" in seen
    assert not any(ALB_NODE in key for key in seen)
    monkeypatch.delenv("TRUSTED_PROXY_IPS")
    importlib.reload(main)
