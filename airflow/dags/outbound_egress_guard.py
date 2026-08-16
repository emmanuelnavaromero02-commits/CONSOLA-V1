"""Fail-closed HTTP(S) transport for Airflow-controlled external destinations.

This module lives in the shared DAG folder because generated DAGs and packaged
cartridge DAGs execute in the Airflow image, outside every cartridge's Python
package. Internal service calls deliberately keep their separate transports.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_METADATA_HOSTS = {
    "169.254.169.254",
    "instance-data",
    "metadata.google.internal",
}
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


class EgressGuardError(requests.RequestException):
    """Raised before dispatch when an outbound destination is unsafe."""


@dataclass(frozen=True)
class ResolvedTarget:
    scheme: str
    host: str
    port: int
    address: str

    @property
    def authority(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        default_port = 443 if self.scheme == "https" else 80
        return host if self.port == default_port else f"{host}:{self.port}"


def _blocked_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return not ip.is_global or ip in _SHARED_ADDRESS_SPACE


def resolve_public_url(url: str, *, label: str = "outbound URL") -> ResolvedTarget:
    """Resolve once and return the public address that the adapter must use."""
    try:
        parsed = urlsplit(str(url or ""))
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise EgressGuardError(f"{label} is malformed") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise EgressGuardError(f"{label} must use http or https")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host or not host.isascii():
        raise EgressGuardError(f"{label} host is invalid")
    if parsed.username or parsed.password:
        raise EgressGuardError(f"{label} must not contain credentials")
    if host in _METADATA_HOSTS or host == "localhost" or host.endswith(
        (".localhost", ".local")
    ):
        raise EgressGuardError(f"{label} host is blocked")
    try:
        direct_ip = ipaddress.ip_address(host)
    except ValueError:
        direct_ip = None
    if direct_ip is not None:
        if _blocked_address(str(direct_ip)):
            raise EgressGuardError(f"{label} resolved to a non-public address")
        return ResolvedTarget(scheme, host, port, str(direct_ip))
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise EgressGuardError(f"{label} host could not be resolved") from exc
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise EgressGuardError(f"{label} host could not be resolved")
    if any(_blocked_address(address) for address in addresses):
        raise EgressGuardError(f"{label} resolved to a non-public address")
    return ResolvedTarget(scheme, host, port, addresses[0])


class PinnedHTTPAdapter(HTTPAdapter):
    """Connect to the validated IP while retaining Host, TLS SNI and hostname."""

    def get_connection_with_tls_context(
        self,
        request: requests.PreparedRequest,
        verify: bool | str,
        proxies: dict[str, str] | None = None,
        cert: Any = None,
    ):
        if proxies and any(proxies.values()):
            raise EgressGuardError("outbound proxies are disabled")
        target = resolve_public_url(request.url or "")
        request.headers["Host"] = target.authority
        _host_params, pool_kwargs = self.build_connection_pool_key_attributes(
            request, verify, cert
        )
        if target.scheme == "https":
            pool_kwargs["assert_hostname"] = target.host
            pool_kwargs["server_hostname"] = target.host
        return self.poolmanager.connection_from_host(
            host=target.address,
            port=target.port,
            scheme=target.scheme,
            pool_kwargs=pool_kwargs,
        )


class EgressSession(requests.Session):
    """A requests-compatible session with pinning and redirect denial."""

    def __init__(self, retries: Retry | int | None = None) -> None:
        super().__init__()
        self.trust_env = False
        adapter = PinnedHTTPAdapter(max_retries=retries or 0)
        self.mount("http://", adapter)
        self.mount("https://", adapter)

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs["allow_redirects"] = False
        response = super().request(method, url, **kwargs)
        if 300 <= response.status_code < 400:
            response.close()
            raise EgressGuardError("outbound redirects are blocked")
        return response


def guarded_session(retries: Retry | int | None = None) -> EgressSession:
    return EgressSession(retries=retries)
