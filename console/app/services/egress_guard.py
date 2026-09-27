from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, quote, urlparse, urlsplit

import h11


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
# RFC 3986 pchar plus "/" (and "?" in the query); "%" keeps existing escapes intact.
_PATH_SAFE = "/%:@!$&'()*+,;=-._~"
_QUERY_SAFE = _PATH_SAFE + "?"
_BODY_METHODS = {"POST", "PUT", "PATCH"}


class EgressGuardError(ValueError):

    def __init__(self, message: str, *, request_dispatched: bool = False) -> None:
        super().__init__(message)
        self.request_dispatched = request_dispatched


class EgressResponseTimeout(EgressGuardError):
    """The deadline expired after the request started going out."""

    def __init__(self) -> None:
        super().__init__("response timed out", request_dispatched=True)


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


@dataclass(frozen=True)
class _PreparedRequest:
    method: str
    target: str
    host: str
    port: int
    https: bool
    headers: tuple[tuple[str, bytes], ...]
    body: bytes


def _request_target(parsed: SplitResult) -> str:
    path = quote(parsed.path or "/", safe=_PATH_SAFE)
    if not parsed.query:
        return path
    return f"{path}?{quote(parsed.query, safe=_QUERY_SAFE)}"


def _authority(host: str, port: int, https: bool) -> str:
    try:
        name = host.encode("ascii").decode("ascii")
    except UnicodeEncodeError:
        name = host.encode("idna").decode("ascii")
    if ":" in name:
        name = f"[{name}]"
    return name if port == (443 if https else 80) else f"{name}:{port}"


def _request_headers(
    method: str,
    headers: dict[str, str] | None,
    *,
    body: bytes,
    content_type: str,
) -> list[tuple[str, bytes]]:
    selected: list[tuple[str, bytes]] = []
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
        selected.append((name, value.encode("utf-8")))
        names.add(lowered)
    if "accept" not in names:
        selected.insert(0, ("Accept", b"*/*"))
    if content_type and (body or method in _BODY_METHODS):
        if any(ch in content_type for ch in "\r\n\x00"):
            raise EgressGuardError("request header is not valid HTTP")
        selected.append(("Content-Type", content_type.encode("utf-8")))
    if body or method in _BODY_METHODS:
        selected.append(("Content-Length", str(len(body)).encode("ascii")))
    selected.append(("Connection", b"close"))
    return selected


def _prepare_request(
    method: str,
    url: str,
    headers: dict[str, str] | None,
    *,
    body: bytes,
    content_type: str,
) -> _PreparedRequest:
    verb = str(method or "").upper()
    if not _METHOD_RE.fullmatch(verb):
        raise EgressGuardError("request method is not valid HTTP")
    text = str(url or "")
    # urlsplit silently drops TAB/CR/LF; refuse them instead of sending an altered URL.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise EgressGuardError("request is not valid HTTP")
    parsed = urlsplit(text)
    https = parsed.scheme == "https"
    try:
        port = parsed.port or (443 if https else 80)
    except ValueError:
        raise EgressGuardError("request is not valid HTTP") from None
    host = (parsed.hostname or "").rstrip(".").lower()
    header_list = [
        ("Host", _authority(host, port, https).encode("ascii")),
        ("Accept-Encoding", b"identity"),
        *_request_headers(verb, headers, body=body, content_type=content_type),
    ]
    return _PreparedRequest(
        method=verb,
        target=_request_target(parsed),
        host=host,
        port=port,
        https=https,
        headers=tuple(header_list),
        body=body,
    )


def _resolve_or_raise(url: str, **policy: Any) -> str:
    reason, address = resolve_url_address(url, **policy)
    if reason:
        raise EgressGuardError(reason)
    return address


def _encode_body(
    body: bytes, json_body: Any, content_type: str
) -> tuple[bytes, str]:
    if json_body is not None:
        encoded = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False)
        return encoded.encode("utf-8"), content_type or "application/json"
    return body or b"", content_type


def _join_headers(pairs: list[tuple[str, str]]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in pairs:
        key = name.strip().lower()
        value = value.strip()
        headers[key] = f"{headers[key]}, {value}" if key in headers else value
    return headers


# ---------------------------------------------------------------- sync (http.client)


class _Deadline:
    """Total deadline for one sync exchange; a watchdog shuts the socket down at expiry."""

    def __init__(self, timeout: float) -> None:
        self.at = time.monotonic() + timeout
        self.fired = threading.Event()
        self._timer: threading.Timer | None = None

    def remaining(self) -> float:
        return self.at - time.monotonic()

    def watch(self, sock: socket.socket) -> None:
        self.cancel()
        self._timer = threading.Timer(max(self.remaining(), 0.0), _abort_socket, (sock, self.fired))
        self._timer.daemon = True
        self._timer.start()

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()

    def expired(self) -> bool:
        return self.fired.is_set() or self.remaining() <= 0


def _abort_socket(sock: socket.socket, fired: threading.Event) -> None:
    fired.set()
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass


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

    def __init__(self, request: _PreparedRequest, *, address: str, deadline: _Deadline) -> None:
        super().__init__(request.host, request.port, timeout=max(deadline.remaining(), 0.001))
        self._pinned_address = address
        self._tls = request.https
        self._deadline = deadline

    def connect(self) -> None:
        # The TCP connect and the TLS handshake share the exchange deadline.
        address, port = self._pinned_address, self.port
        remaining = self._deadline.remaining()
        if remaining <= 0:
            raise TimeoutError("egress deadline exceeded before connecting")
        sock = socket.create_connection((address, port), remaining)
        try:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            if self._tls:
                sock = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=self.host, do_handshake_on_connect=False
                )
            self._deadline.watch(sock)
            if self._tls:
                sock.settimeout(max(self._deadline.remaining(), 0.001))
                sock.do_handshake()
        except BaseException:
            sock.close()
            raise
        self.sock = sock


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
    return _join_headers(pairs)


def _exchange(
    request: _PreparedRequest, max_bytes: int, deadline: _Deadline, *, address: str
) -> PinnedHTTPResponse:
    if deadline.remaining() <= 0:
        raise TimeoutError("egress deadline exceeded before connecting")
    conn = _PinnedHTTPConnection(request, address=address, deadline=deadline)
    try:
        try:
            conn.putrequest(request.method, request.target, skip_host=True, skip_accept_encoding=True)
            for name, value in request.headers:
                conn.putheader(name, value)
        except (ValueError, http.client.HTTPException):
            raise EgressGuardError("request is not valid HTTP") from None
        try:
            conn.connect()
        except OSError:
            if deadline.fired.is_set():
                raise TimeoutError("egress deadline exceeded before sending") from None
            raise
        if deadline.expired():
            raise TimeoutError("egress deadline exceeded before sending")
        try:
            conn.endheaders(request.body or None)
            response = conn.getresponse()
            if 100 <= response.status < 200:
                raise EgressGuardError("unexpected interim response", request_dispatched=True)
            headers = _response_headers(response)
            content = _read_body(response, max_bytes)
            if deadline.fired.is_set():
                raise EgressResponseTimeout()
            return PinnedHTTPResponse(status_code=response.status, headers=headers, content=content)
        except EgressResponseTimeout:
            raise
        except EgressGuardError as exc:
            if deadline.fired.is_set() and exc.request_dispatched:
                raise EgressResponseTimeout() from None
            raise
        except (http.client.HTTPException, OSError, ValueError) as exc:
            if deadline.fired.is_set() or isinstance(exc, TimeoutError):
                raise EgressResponseTimeout() from None
            raise EgressGuardError(
                f"response failed ({type(exc).__name__})", request_dispatched=True
            ) from None
    finally:
        deadline.cancel()
        conn.close()


# ---------------------------------------------------------------- async (asyncio + h11)


def _h11_request_bytes(request: _PreparedRequest) -> tuple[h11.Connection, bytes]:
    client = h11.Connection(h11.CLIENT, max_incomplete_event_size=_MAX_RESPONSE_HEADER_BYTES)
    try:
        wire = client.send(
            h11.Request(method=request.method, target=request.target, headers=list(request.headers))
        )
        if request.body:
            wire += client.send(h11.Data(data=request.body))
        wire += client.send(h11.EndOfMessage())
    except (h11.LocalProtocolError, UnicodeError):
        raise EgressGuardError("request is not valid HTTP") from None
    return client, wire


async def _h11_response(
    client: h11.Connection, reader: asyncio.StreamReader, max_bytes: int
) -> PinnedHTTPResponse:
    status = 0
    headers: dict[str, str] = {}
    body = bytearray()
    while True:
        event = client.next_event()
        if event is h11.NEED_DATA:
            client.receive_data(await reader.read(_READ_CHUNK_BYTES))
        elif isinstance(event, h11.InformationalResponse):
            if event.status_code != 100:
                raise EgressGuardError("unexpected interim response", request_dispatched=True)
        elif isinstance(event, h11.Response):
            status = event.status_code
            pairs = [(name.decode("latin-1"), value.decode("latin-1")) for name, value in event.headers]
            if sum(len(name) + len(value) + 4 for name, value in pairs) > _MAX_RESPONSE_HEADER_BYTES:
                raise EgressGuardError("response headers too large", request_dispatched=True)
            headers = _join_headers(pairs)
            declared = headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                raise EgressGuardError("response exceeds size limit", request_dispatched=True)
        elif isinstance(event, h11.Data):
            body.extend(event.data)
            if len(body) > max_bytes:
                raise EgressGuardError("response exceeds size limit", request_dispatched=True)
        elif isinstance(event, h11.EndOfMessage):
            return PinnedHTTPResponse(status_code=status, headers=headers, content=bytes(body))
        else:
            raise EgressGuardError("response ended unexpectedly", request_dispatched=True)


async def _exchange_async(
    request: _PreparedRequest, max_bytes: int, timeout: float, *, address: str
) -> PinnedHTTPResponse:
    client, wire = _h11_request_bytes(request)
    writer: asyncio.StreamWriter | None = None
    sending = False
    try:
        async with asyncio.timeout(timeout):
            context = ssl.create_default_context() if request.https else None
            reader, writer = await asyncio.open_connection(
                host=address,
                port=request.port,
                ssl=context,
                server_hostname=request.host if context else None,
            )
            sending = True
            writer.write(wire)
            await writer.drain()
            return await _h11_response(client, reader, max_bytes)
    except EgressGuardError:
        raise
    except TimeoutError:
        if sending:
            raise EgressResponseTimeout() from None
        raise TimeoutError("egress deadline exceeded before sending") from None
    except h11.RemoteProtocolError as exc:
        reason = "response headers too large" if exc.error_status_hint == 431 else "response is not valid HTTP"
        raise EgressGuardError(reason, request_dispatched=True) from None
    except (OSError, EOFError, asyncio.IncompleteReadError) as exc:
        if not sending:
            raise
        raise EgressGuardError(f"response failed ({type(exc).__name__})", request_dispatched=True) from None
    finally:
        # Cancellation, success or failure: the socket is released immediately.
        if writer is not None:
            writer.transport.abort()


# ---------------------------------------------------------------- public API


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
    payload, content_type = _encode_body(body, json_body, content_type)
    request = _prepare_request(method, url, headers, body=payload, content_type=content_type)
    address = _resolve_or_raise(
        url,
        label=label,
        allow_private_hosts=allow_private_hosts,
        allow_private_cidrs=allow_private_cidrs,
        allow_private=allow_private,
        allow_http_hosts=allow_http_hosts,
        require_https_in_prod=require_https_in_prod,
    )
    return await _exchange_async(request, max_bytes, timeout, address=address)


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
    deadline = _Deadline(timeout)
    payload, content_type = _encode_body(body, json_body, content_type)
    request = _prepare_request(method, url, headers, body=payload, content_type=content_type)
    address = _resolve_or_raise(
        url,
        label=label,
        allow_private_hosts=allow_private_hosts,
        allow_private_cidrs=allow_private_cidrs,
        allow_private=allow_private,
        allow_http_hosts=allow_http_hosts,
        require_https_in_prod=require_https_in_prod,
    )
    return _exchange(request, max_bytes, deadline, address=address)
