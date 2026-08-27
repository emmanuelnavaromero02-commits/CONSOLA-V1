"""No-network SSRF regression probes for every packaged cartridge."""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parents[1]
PACKAGED_CARTRIDGES = (
    "banxico",
    "hubspot",
    "inegi",
    "salesforce",
    "replicon",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
    "sec_edgar",
)


def _load_guard(cartridge: str):
    name = f"_fseg_{cartridge}_egress_guard"
    path = REPO / "cartridges" / cartridge / "app" / "core" / "egress_guard.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _dns(*addresses: str):
    def resolve(_host, port, *, type):
        assert type == socket.SOCK_STREAM
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, type, 6, "", (address, port))
            for address in addresses
        ]

    return resolve


@pytest.mark.parametrize("cartridge", PACKAGED_CARTRIDGES)
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/health",
        "http://[::1]/api/health",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://localhost:8000/",
        "file:///etc/hostname",
        "gopher://public.example/1",
        "https://synthetic:canary@public.example/",
    ],
)
def test_all_cartridges_block_literal_loopback_imds_and_schemes(
    cartridge, url
):
    guard = _load_guard(cartridge)
    with pytest.raises(guard.EgressGuardError):
        guard.resolve_public_url(url)


@pytest.mark.parametrize("cartridge", PACKAGED_CARTRIDGES)
@pytest.mark.parametrize(
    "addresses", [("10.0.0.8",), ("192.168.1.10",), ("93.184.216.34", "10.1.2.3")]
)
def test_all_cartridges_fail_closed_on_private_or_mixed_dns(
    cartridge, addresses, monkeypatch
):
    guard = _load_guard(cartridge)
    monkeypatch.setattr(guard.socket, "getaddrinfo", _dns(*addresses))

    with pytest.raises(guard.EgressGuardError, match="non-public"):
        guard.resolve_public_url("http://console:8000/api/health")


@pytest.mark.parametrize("cartridge", PACKAGED_CARTRIDGES)
def test_all_cartridges_pin_validated_dns_and_tls_identity(cartridge, monkeypatch):
    guard = _load_guard(cartridge)
    monkeypatch.setattr(
        guard.socket, "getaddrinfo", _dns("93.184.216.34", "93.184.216.35")
    )
    adapter = guard.PinnedHTTPAdapter()
    captured = {}

    class PoolManager:
        def connection_from_host(self, **kwargs):
            captured.update(kwargs)
            return object()

    adapter.poolmanager = PoolManager()
    request = requests.Request(
        "GET", "https://api.vendor.example:8443/v1/canary"
    ).prepare()

    adapter.get_connection_with_tls_context(request, True, {}, None)

    assert captured["host"] == "93.184.216.34"
    assert captured["port"] == 8443
    assert captured["scheme"] == "https"
    assert captured["pool_kwargs"]["assert_hostname"] == "api.vendor.example"
    assert captured["pool_kwargs"]["server_hostname"] == "api.vendor.example"
    assert request.headers["Host"] == "api.vendor.example:8443"


@pytest.mark.parametrize("cartridge", PACKAGED_CARTRIDGES)
def test_all_cartridges_block_redirects_without_following(
    cartridge, monkeypatch
):
    guard = _load_guard(cartridge)
    response = requests.Response()
    response.status_code = 300
    response.headers["Location"] = "http://169.254.169.254/latest/meta-data"
    response._content = b""
    response._content_consumed = True

    def synthetic_redirect(_session, _method, _url, **kwargs):
        assert kwargs["allow_redirects"] is False
        return response

    monkeypatch.setattr(requests.Session, "request", synthetic_redirect)

    with pytest.raises(guard.EgressGuardError, match="redirects"):
        guard.guarded_session().get("https://api.vendor.example/start")


@pytest.mark.parametrize("cartridge", PACKAGED_CARTRIDGES)
def test_all_cartridges_reject_proxy_bypass(cartridge):
    guard = _load_guard(cartridge)
    adapter = guard.PinnedHTTPAdapter()
    request = requests.Request("GET", "https://api.vendor.example/v1").prepare()

    with pytest.raises(guard.EgressGuardError, match="proxies"):
        adapter.get_connection_with_tls_context(
            request, True, {"https": "http://127.0.0.1:8080"}, None
        )


@pytest.mark.parametrize(
    ("cartridge", "client"),
    [
        ("banxico", "banxico_client.py"),
        ("hubspot", "hubspot_client.py"),
        ("inegi", "inegi_client.py"),
        ("salesforce", "salesforce_client.py"),
        ("replicon", "replicon_client.py"),
        ("sap_hcm", "sap_client.py"),
        ("sap_s4hana", "sap_client.py"),
        ("sap_successfactors", "sap_client.py"),
        ("sec_edgar", "sec_client.py"),
    ],
)
def test_test_connection_and_extraction_clients_use_guarded_transport(
    cartridge, client
):
    source = (
        REPO / "cartridges" / cartridge / "app" / "core" / client
    ).read_text(encoding="utf-8")

    assert "guarded_session" in source
    assert "requests.get(" not in source
    assert "requests.post(" not in source


def test_hubspot_egress_allowlist_is_opt_in_and_defaults_closed(monkeypatch):
    # The hubspot cartridge gained an opt-in, default-empty egress allowlist
    # (OMEGA_EGRESS_ALLOWED_HOSTS) so the release acceptance run can reach its
    # fake HubSpot upstream on host.docker.internal. Unset -> production is
    # unchanged (full SSRF protection); set -> only the named host is reachable.
    guard = _load_guard("hubspot")
    monkeypatch.setattr(guard.socket, "getaddrinfo", _dns("192.168.65.2"))
    mock_url = "http://host.docker.internal:18030/crm/v3/owners"

    monkeypatch.delenv("OMEGA_EGRESS_ALLOWED_HOSTS", raising=False)
    with pytest.raises(guard.EgressGuardError, match="non-public"):
        guard.resolve_public_url(mock_url)

    monkeypatch.setenv("OMEGA_EGRESS_ALLOWED_HOSTS", "host.docker.internal")
    target = guard.resolve_public_url(mock_url)
    assert target.host == "host.docker.internal"
    assert target.address == "192.168.65.2"
    assert target.port == 18030


def test_hubspot_egress_allowlist_never_relaxes_metadata_or_localhost(monkeypatch):
    guard = _load_guard("hubspot")
    monkeypatch.setenv(
        "OMEGA_EGRESS_ALLOWED_HOSTS",
        "169.254.169.254,localhost,metadata.google.internal",
    )
    for url in (
        "http://169.254.169.254/latest/meta-data",
        "http://localhost:8000/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ):
        with pytest.raises(guard.EgressGuardError):
            guard.resolve_public_url(url)


@pytest.mark.parametrize(
    "cartridge", [c for c in PACKAGED_CARTRIDGES if c != "hubspot"]
)
def test_egress_allowlist_escape_hatch_is_scoped_to_hubspot(cartridge, monkeypatch):
    # The escape hatch is deliberately hubspot-only; the other guards ignore the
    # env var and keep blocking private destinations.
    guard = _load_guard(cartridge)
    monkeypatch.setattr(guard.socket, "getaddrinfo", _dns("192.168.65.2"))
    monkeypatch.setenv("OMEGA_EGRESS_ALLOWED_HOSTS", "host.docker.internal")
    with pytest.raises(guard.EgressGuardError, match="non-public"):
        guard.resolve_public_url("http://host.docker.internal:18030/crm/v3/owners")
