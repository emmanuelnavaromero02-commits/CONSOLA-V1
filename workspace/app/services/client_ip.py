from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable, Mapping

from fastapi import Request

ProxyNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
ProxyAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _proxy_network(value: object) -> ProxyNetwork | None:
    if isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return value
    try:
        return ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError:
        return None


def _address(value: object) -> ProxyAddress | None:
    try:
        address = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _is_trusted(address: ProxyAddress, networks: frozenset[ProxyNetwork]) -> bool:
    return any(address.version == network.version and address in network for network in networks)


def trusted_proxy_ips(env: Mapping[str, str] | None = None) -> frozenset[ProxyNetwork]:
    values = env if env is not None else os.environ
    networks = (
        _proxy_network(entry)
        for entry in values.get("TRUSTED_PROXY_IPS", "").split(",")
        if entry.strip()
    )
    return frozenset(network for network in networks if network is not None)


def client_ip(
    request: Request,
    *,
    trusted_proxies: Iterable[ProxyNetwork | str] | None = None,
) -> str:
    real_ip = request.client.host if request.client else "unknown"
    raw = trusted_proxy_ips() if trusted_proxies is None else trusted_proxies
    networks = frozenset(n for n in (_proxy_network(item) for item in raw) if n is not None)
    peer = _address(real_ip)
    if not networks or peer is None or not _is_trusted(peer, networks):
        return real_ip
    entries = request.headers.get("x-forwarded-for", "").split(",")
    origin = None
    for entry in reversed(entries):
        address = _address(entry)
        if address is None:
            return real_ip
        if not _is_trusted(address, networks):
            return str(address)
        origin = address
    return str(origin) if origin is not None else real_ip
