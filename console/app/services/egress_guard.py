from __future__ import annotations

import asyncio
import functools
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlsplit


_BLOCKED_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")
_METADATA_HOSTS = {"metadata.google.internal", "instance-data", "169.254.169.254"}
_DEFAULT_MAX_BYTES = 2 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_MAX_RESPONSE_HEADER_BYTES = 64 * 1024
_MAX_CHUNK_SIZE_LINE = 1024
# Framing is owned by the transport; callers may never set these.
_TRANSPORT_OWNED_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "accept-encoding",
}
_HEADER_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_METHOD_RE = re.compile(r"[A-Z]{1,16}")
_HEX_DIGITS = frozenset(b"0123456789abcdefABCDEF")
_EGRESS_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="egress")


class EgressGuardError(ValueError):

    def __init__(self, message: str, *, request_dispatched: bool = False) -> None:
        super().__init__(message)
        self.request_dispatched = request_dispatched


@dataclass
class PinnedHTTPResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    @property
    def is_redirect(self) -> bool:
        return self.status_code in {301, 302, 303, 307, 308}

    def json(self) -> dict[str, Any]:
        payload = json.loads(self.text)
        return payload if isinstance(payload, dict) else {}


def is_production_env() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {
        "production",
        "prod",
    }


def _host_set(values: set[str] | list[str] | tuple[str, ...] | None) -> set[str]:
    return {
        str(item).strip().rstrip(".").lower()
        for item in (values or [])
        if str(item).strip()
    }


def _cidr_list(
    values: list[str] | tuple[str, ...] | None,
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in values or []:
        try:
            networks.append(ipaddress.ip_network(str(item).strip(), strict=False))
        except ValueError:
            continue
    return networks


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in _BLOCKED_SHARED_ADDRESS_SPACE
    )


def resolve_url_address(
    url: str,
    *,
    label: str = "URL",
    allow_private_hosts: set[str] | list[str] | tuple[str, ...] | None = None,
    allow_private_cidrs: list[str] | tuple[str, ...] | None = None,
    allow_private: bool = False,
    allow_http_hosts: set[str] | list[str] | tuple[str, ...] | None = None,
    require_https_in_prod: bool = True,
) -> tuple[str, str]:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"}:
        return f"{label} must use http or https", ""
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return f"{label} host is required", ""
    allowed_hosts = _host_set(allow_private_hosts)
    allowed_http_hosts = _host_set(allow_http_hosts)
    if (
        require_https_in_prod
        and is_production_env()
        and parsed.scheme != "https"
        and host not in allowed_http_hosts
    ):
        return f"{label} must use https in production", ""
    if parsed.username or parsed.password:
        return f"{label} must not include credentials", ""
    if host in _METADATA_HOSTS:
        return f"{label} metadata hosts are blocked", ""
    if host in {"localhost"} or host.endswith(".localhost") or host.endswith(".local"):
        if not allow_private and host not in allowed_hosts:
            return f"{label} host is not public", ""

    try:
        addresses = [
            item[4][0]
            for item in socket.getaddrinfo(
                host,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        ]
    except socket.gaierror:
        return f"{label} host could not be resolved", ""
    except Exception as exc:
        return f"{label} validation failed: {type(exc).__name__}", ""

    allowed_cidrs = _cidr_list(allow_private_cidrs)
    first_address = ""
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return f"{label} resolved to an invalid address", ""
        if (
            allow_private
            or host in allowed_hosts
            or any(ip in cidr for cidr in allowed_cidrs)
        ):
            first_address = first_address or address
            continue
        if _blocked_ip(ip):
            return f"{label} resolved to a non-public address", ""
        first_address = first_address or address
    if not first_address:
        return f"{label} host could not be resolved", ""
    return "", first_address


def validation_error(url: str, **kwargs: Any) -> str:
    reason, _address = resolve_url_address(url, **kwargs)
    return reason


def validate_url(url: str, **kwargs: Any) -> str:
    reason, _address = resolve_url_address(url, **kwargs)
    if reason:
        raise EgressGuardError(reason)
    return str(url)


class _StrictHTTPResponse(http.client.HTTPResponse):
    def _read_next_chunk_size(self) -> int:
        line = self.fp.readline(_MAX_CHUNK_SIZE_LINE + 1)
        if len(line) > _MAX_CHUNK_SIZE_LINE:
            raise http.client.LineTooLong("chunk size")
        size = line.split(b";", 1)[0].strip(b" \t\r\n")
        if not size or len(size) > 16 or not set(size) <= _HEX_DIGITS:
            self._close_conn()
            raise ValueError("invalid chunk size")
        return int(size, 16)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    response_class = _StrictHTTPResponse

    def __init__(self, host: str, port: int, *, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_address = address

    def connect(self) -> None:
        address, port = self._pinned_address, self.port
        self.sock = socket.create_connection((address, port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    response_class = _StrictHTTPResponse

    def __init__(
        self, host: str, port: int, *, address: str, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_address = address
        self._pinned_context = context

    def connect(self) -> None:
        address, port = self._pinned_address, self.port
        sock = socket.create_connection((address, port), self.timeout)
        try:
            self.sock = self._pinned_context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def _request_headers(
    method: str,
    headers: dict[str, str] | None,
    *,
    body: bytes,
    content_type: str,
) -> list[tuple[str, str]]:
    selected: list[tuple[str, str]] = []
    names: set[str] = set()
    for raw_name, raw_value in (headers or {}).items():
        name, value = str(raw_name), str(raw_value)
        lowered = name.lower()
        if lowered in _TRANSPORT_OWNED_HEADERS:
            raise EgressGuardError(f"request header {name} is owned by the transport")
        if not _HEADER_NAME_RE.fullmatch(name) or any(ch in value for ch in "\r\n\x00"):
            raise EgressGuardError("request header is not valid HTTP")
        if content_type and lowered == "content-type":
            continue
        selected.append((name, value))
        names.add(lowered)
    if "accept" not in names:
        selected.insert(0, ("Accept", "*/*"))
    if content_type and (body or method in {"POST", "PUT", "PATCH"}):
        if any(ch in content_type for ch in "\r\n\x00"):
            raise EgressGuardError("request header is not valid HTTP")
        selected.append(("Content-Type", content_type))
    if body or method in {"POST", "PUT", "PATCH"}:
        selected.append(("Content-Length", str(len(body))))
    selected.append(("Connection", "close"))
    return selected


def _abort_socket(sock: socket.socket, fired: threading.Event) -> None:
    fired.set()
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass


def _read_body(response: http.client.HTTPResponse, max_bytes: int) -> bytes:
    if response.length is not None and response.length > max_bytes:
        raise EgressGuardError("response exceeds size limit", request_dispatched=True)
    body = bytearray()
    while True:
        chunk = response.read(min(_READ_CHUNK_BYTES, max_bytes + 1 - len(body)))
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > max_bytes:
            raise EgressGuardError("response exceeds size limit", request_dispatched=True)
    if response.length:
        raise EgressGuardError("response ended before its declared length", request_dispatched=True)
    return bytes(body)


def _response_headers(response: http.client.HTTPResponse) -> dict[str, str]:
    pairs = response.getheaders()
    if sum(len(name) + len(value) + 4 for name, value in pairs) > _MAX_RESPONSE_HEADER_BYTES:
        raise EgressGuardError("response headers too large", request_dispatched=True)
    headers: dict[str, str] = {}
    for name, value in pairs:
        key = name.strip().lower()
        headers[key] = f"{headers[key]}, {value.strip()}" if key in headers else value.strip()
    return headers


def _exchange(
    method: str,
    url: str,
    headers: dict[str, str] | None,
    body: bytes,
    max_bytes: int,
    deadline: float,
    *,
    address: str,
    content_type: str = "",
) -> PinnedHTTPResponse:
    verb = str(method or "").upper()
    if not _METHOD_RE.fullmatch(verb):
        raise EgressGuardError("request method is not valid HTTP")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    https = parsed.scheme == "https"
    port = parsed.port or (443 if https else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    request_headers = _request_headers(verb, headers, body=body, content_type=content_type)
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise TimeoutError("egress deadline exceeded before connecting")
    if https:
        conn: http.client.HTTPConnection = _PinnedHTTPSConnection(
            host, port, address=address, timeout=budget, context=ssl.create_default_context()
        )
    else:
        conn = _PinnedHTTPConnection(host, port, address=address, timeout=budget)
    try:
        try:
            conn.putrequest(verb, path)
            for name, value in request_headers:
                conn.putheader(name, value)
        except (ValueError, http.client.HTTPException):
            raise EgressGuardError("request is not valid HTTP") from None
        conn.connect()
        fired = threading.Event()
        watchdog = threading.Timer(max(deadline - time.monotonic(), 0.0), _abort_socket, (conn.sock, fired))
        watchdog.daemon = True
        watchdog.start()
        try:
            conn.endheaders(body or None)
            response = conn.getresponse()
            if 100 <= response.status < 200:
                raise EgressGuardError("unexpected interim response", request_dispatched=True)
            response_headers = _response_headers(response)
            content = _read_body(response, max_bytes)
            if fired.is_set():
                raise EgressGuardError("response timed out", request_dispatched=True)
            return PinnedHTTPResponse(
                status_code=response.status, headers=response_headers, content=content
            )
        except EgressGuardError as exc:
            if fired.is_set() and exc.request_dispatched:
                raise EgressGuardError("response timed out", request_dispatched=True) from None
            raise
        except (http.client.HTTPException, OSError, ValueError) as exc:
            reason = "response timed out" if fired.is_set() else f"response failed ({type(exc).__name__})"
            raise EgressGuardError(reason, request_dispatched=True) from None
        finally:
            watchdog.cancel()
    finally:
        conn.close()


def _pinned_call(
    method: str,
    url: str,
    *,
    label: str,
    headers: dict[str, str] | None,
    body: bytes,
    json_body: Any,
    content_type: str,
    max_bytes: int,
    deadline: float,
    allow_private_hosts: set[str] | list[str] | tuple[str, ...] | None,
    allow_private_cidrs: list[str] | tuple[str, ...] | None,
    allow_private: bool,
    allow_http_hosts: set[str] | list[str] | tuple[str, ...] | None,
    require_https_in_prod: bool,
) -> PinnedHTTPResponse:
    if json_body is not None:
        body = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        content_type = content_type or "application/json"
    reason, address = resolve_url_address(
        url,
        label=label,
        allow_private_hosts=allow_private_hosts,
        allow_private_cidrs=allow_private_cidrs,
        allow_private=allow_private,
        allow_http_hosts=allow_http_hosts,
        require_https_in_prod=require_https_in_prod,
    )
    if reason:
        raise EgressGuardError(reason)
    return _exchange(
        method,
        url,
        headers,
        body or b"",
        max_bytes,
        deadline,
        address=address,
        content_type=content_type,
    )


async def pinned_request(
    method: str,
    url: str,
    *,
    label: str = "URL",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    json_body: Any = None,
    content_type: str = "",
    max_bytes: int = _DEFAULT_MAX_BYTES,
    timeout: float = 15.0,
    allow_private_hosts: set[str] | list[str] | tuple[str, ...] | None = None,
    allow_private_cidrs: list[str] | tuple[str, ...] | None = None,
    allow_private: bool = False,
    allow_http_hosts: set[str] | list[str] | tuple[str, ...] | None = None,
    require_https_in_prod: bool = True,
) -> PinnedHTTPResponse:
    call = functools.partial(
        _pinned_call,
        method,
        url,
        label=label,
        headers=headers,
        body=body,
        json_body=json_body,
        content_type=content_type,
        max_bytes=max_bytes,
        deadline=time.monotonic() + timeout,
        allow_private_hosts=allow_private_hosts,
        allow_private_cidrs=allow_private_cidrs,
        allow_private=allow_private,
        allow_http_hosts=allow_http_hosts,
        require_https_in_prod=require_https_in_prod,
    )
    return await asyncio.get_running_loop().run_in_executor(_EGRESS_EXECUTOR, call)


def pinned_request_sync(
    method: str,
    url: str,
    *,
    label: str = "URL",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    json_body: Any = None,
    content_type: str = "",
    max_bytes: int = _DEFAULT_MAX_BYTES,
    timeout: float = 15.0,
    allow_private_hosts: set[str] | list[str] | tuple[str, ...] | None = None,
    allow_private_cidrs: list[str] | tuple[str, ...] | None = None,
    allow_private: bool = False,
    allow_http_hosts: set[str] | list[str] | tuple[str, ...] | None = None,
    require_https_in_prod: bool = True,
) -> PinnedHTTPResponse:
    return _pinned_call(
        method,
        url,
        label=label,
        headers=headers,
        body=body,
        json_body=json_body,
        content_type=content_type,
        max_bytes=max_bytes,
        deadline=time.monotonic() + timeout,
        allow_private_hosts=allow_private_hosts,
        allow_private_cidrs=allow_private_cidrs,
        allow_private=allow_private,
        allow_http_hosts=allow_http_hosts,
        require_https_in_prod=require_https_in_prod,
    )
