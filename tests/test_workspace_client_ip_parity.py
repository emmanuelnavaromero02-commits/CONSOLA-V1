from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import request_rate_limits as console

REPO = Path(__file__).resolve().parents[1]


def _workspace_policy():
    path = REPO / "workspace/app/services/client_ip.py"
    spec = importlib.util.spec_from_file_location("workspace_client_ip_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WORKSPACE = _workspace_policy()
PROXY_SETS = [
    frozenset(),
    frozenset({"10.0.1.0/24", "10.0.4.0/24"}),
    frozenset({"10.0.0.0/16"}),
    frozenset({"10.0.1.20"}),
    frozenset({"fd00::/8", "10.0.1.0/24"}),
    frozenset({"not-an-ip"}),
]
PEERS = ["10.0.1.20", "10.0.4.7", "172.18.0.5", "198.51.100.9", "fd00::20", "unknown", None]
HEADERS = [
    "",
    "198.51.100.7",
    "1.1.1.1, 198.51.100.7",
    "10.0.1.99, 198.51.100.7",
    "1.1.1.1, 198.51.100.7, 10.0.4.12",
    "198.51.100.7, 10.0.1.5, 10.0.4.12",
    "2001:db8::1",
    "::ffff:198.51.100.7",
    "198.51.100.7:4431",
    "198.51.100.7, not-an-ip",
    "198.51.100.7,",
    "10.0.1.8, 10.0.4.9",
    "garbage",
]


def _request(peer, forwarded_for):
    return SimpleNamespace(
        client=SimpleNamespace(host=peer) if peer else None,
        headers={"x-forwarded-for": forwarded_for} if forwarded_for else {},
    )


@pytest.mark.parametrize("proxies", PROXY_SETS, ids=lambda value: ",".join(sorted(value)) or "none")
def test_workspace_client_ip_matches_the_console_helper(proxies):
    for peer, forwarded_for in itertools.product(PEERS, HEADERS):
        request = _request(peer, forwarded_for)
        assert WORKSPACE.client_ip(request, trusted_proxies=proxies) == console.client_ip(
            request, trusted_proxies=proxies
        ), (proxies, peer, forwarded_for)


def test_workspace_and_console_parse_the_same_proxy_list():
    env = {"TRUSTED_PROXY_IPS": "10.0.1.0/24, 10.0.0.9,, bogus, fd00::/8, 10.0.4.7/24"}
    assert WORKSPACE.trusted_proxy_ips(env) == console.trusted_proxy_ips(env)
