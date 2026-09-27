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
from typing import Any, cast
from urllib.parse import SplitResult, quote, urlparse, urlsplit

import h11


_BLOCKED_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")
_METADATA_HOSTS = {"metadata.google.internal", "instance-data", "169.254.169.254"}
_DEFAULT_MAX_BYTES = 2 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_MAX_RESPONSE_HEADER_BYTES = 64 * 1024
_MAX_CHUNK_SIZE_LINE = 1024
# http.client refuses a 100th header line (its limit counts the blank terminator); both paths share it.
_MAX_RESPONSE_HEADER_FIELDS = 99
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


@dataclass(frozen=True)
class _Destination:
    """A URL that passed every check that does not need DNS."""

    label: str
    host: str
    port: int
    allow_private: bool
    allowed_hosts: frozenset[str]
    allowed_cidrs: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]


def _destination(
    url: str,
    *,
    label: str = "URL",
    allow_private_hosts: set[str] | list[str] | tuple[str, ...] | None = None,
    allow_private_cidrs: list[str] | tuple[str, ...] | None = None,
    allow_private: bool = False,
    allow_http_hosts: set[str] | list[str] | tuple[str, ...] | None = None,
    require_https_in_prod: bool = True,
) -> tuple[str, _Destination | None]:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"}:
        return f"{label} must use http or https", None
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return f"{label} host is required", None
    allowed_hosts = _host_set(allow_private_hosts)
    allowed_http_hosts = _host_set(allow_http_hosts)
    if (
        require_https_in_prod
        and is_production_env()
        and parsed.scheme != "https"
        and host not in allowed_http_hosts
    ):
        return f"{label} must use https in production", None
    if parsed.username or parsed.password:
        return f"{label} must not include credentials", None
    if host in _METADATA_HOSTS:
        return f"{label} metadata hosts are blocked", None
    if host in {"localhost"} or host.endswith(".localhost") or host.endswith(".local"):
        if not allow_private and host not in allowed_hosts:
            return f"{label} host is not public", None
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        return f"{label} validation failed: {type(exc).__name__}", None
    return "", _Destination(
        label=label,
        host=host,
        port=port,
        allow_private=allow_private,
        allowed_hosts=frozenset(allowed_hosts),
        allowed_cidrs=tuple(_cidr_list(allow_private_cidrs)),
    )


def _resolution_failure(label: str, exc: Exception) -> str:
    if isinstance(exc, socket.gaierror):
        return f"{label} host could not be resolved"
    return f"{label} validation failed: {type(exc).__name__}"


def _pinned_address(destination: _Destination, addrinfo: Any) -> tuple[str, str]:
    """Address policy shared by both transports: every resolved address must be allowed."""
    label = destination.label
    first_address = ""
    for item in addrinfo:
        address = item[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return f"{label} resolved to an invalid address", ""
        if (
            destination.allow_private
            or destination.host in destination.allowed_hosts
            or any(ip in cidr for cidr in destination.allowed_cidrs)
        ):
            first_address = first_address or address
            continue
        if _blocked_ip(ip):
            return f"{label} resolved to a non-public address", ""
        first_address = first_address or address
    if not first_address:
        return f"{label} host could not be resolved", ""
    return "", first_address


def _resolve(destination: _Destination) -> tuple[str, str]:
    try:
        addrinfo = socket.getaddrinfo(destination.host, destination.port, type=socket.SOCK_STREAM)
        return _pinned_address(destination, addrinfo)
    except Exception as exc:
        return _resolution_failure(destination.label, exc), ""


async def _resolve_async(destination: _Destination) -> str:
    try:
        addrinfo = await asyncio.get_running_loop().getaddrinfo(
            destination.host, destination.port, type=socket.SOCK_STREAM
        )
        reason, address = _pinned_address(destination, addrinfo)
    except Exception as exc:
        reason, address = _resolution_failure(destination.label, exc), ""
    if reason:
        raise EgressGuardError(reason)
    return address


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
    reason, destination = _destination(
        url,
        label=label,
        allow_private_hosts=allow_private_hosts,
        allow_private_cidrs=allow_private_cidrs,
        allow_private=allow_private,
        allow_http_hosts=allow_http_hosts,
        require_https_in_prod=require_https_in_prod,
    )
    if destination is None:
        return reason, ""
    return _resolve(destination)


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


def _header_value(value: str) -> bytes:
    # Surrounding blanks are not part of a field value; the only control character allowed inside is HTAB.
    text = value.strip(" \t")
    if any((ch < " " and ch != "\t") or ch == "\x7f" for ch in text):
        raise EgressGuardError("request header is not valid HTTP")
    return text.encode("utf-8")


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
        if not _HEADER_NAME_RE.fullmatch(name):
            raise EgressGuardError("request header is not valid HTTP")
        encoded = _header_value(value)
        if content_type and lowered == "content-type":
            continue
        selected.append((name, encoded))
        names.add(lowered)
    if "accept" not in names:
        selected.insert(0, ("Accept", b"*/*"))
    if content_type and (body or method in _BODY_METHODS):
        selected.append(("Content-Type", _header_value(content_type)))
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


def _destination_or_raise(url: str, **policy: Any) -> _Destination:
    reason, destination = _destination(url, **policy)
    if destination is None:
        raise EgressGuardError(reason)
    return destination


_SSL_CONTEXT: ssl.SSLContext | None = None
_SSL_CONTEXT_LOCK = threading.Lock()


def _ssl_context() -> ssl.SSLContext:
    """The verifying client context, built once: loading the trust store is too slow to repeat per call."""
    global _SSL_CONTEXT
    context = _SSL_CONTEXT
    if context is None:
        with _SSL_CONTEXT_LOCK:
            if _SSL_CONTEXT is None:
                _SSL_CONTEXT = ssl.create_default_context()
            context = _SSL_CONTEXT
    return context


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


# ---------------------------------------------------------------- response heads (both paths)
#
# h11 is the single judge of a response head: the async path parses with it directly and the sync
# path re-parses the exact head bytes http.client consumed, then adopts h11's framing decision.


def _protocol_failure(exc: h11.RemoteProtocolError) -> EgressGuardError:
    reason = "response headers too large" if exc.error_status_hint == 431 else "response is not valid HTTP"
    return EgressGuardError(reason, request_dispatched=True)


def _interim(event: h11.InformationalResponse) -> None:
    if event.status_code != 100:
        raise EgressGuardError("unexpected interim response", request_dispatched=True)


def _final_head(event: h11.Response) -> dict[str, str]:
    if event.http_version not in (b"1.0", b"1.1"):
        raise EgressGuardError("response is not valid HTTP", request_dispatched=True)
    pairs = [(name.decode("latin-1"), value.decode("latin-1")) for name, value in event.headers]
    head_bytes = len(event.reason) + sum(len(name) + len(value) + 4 for name, value in pairs)
    if len(pairs) > _MAX_RESPONSE_HEADER_FIELDS or head_bytes > _MAX_RESPONSE_HEADER_BYTES:
        raise EgressGuardError("response headers too large", request_dispatched=True)
    return _join_headers(pairs)


def _declared_length_exceeds(method: str, status: int, headers: dict[str, str], max_bytes: int) -> bool:
    if method == "HEAD" or status in (204, 304) or "transfer-encoding" in headers:
        return False
    declared = headers.get("content-length", "")
    return declared.isdigit() and int(declared) > max_bytes


def _parse_head(method: str, head: bytes) -> tuple[h11.Response, dict[str, str]]:
    parser = h11.Connection(h11.CLIENT)
    parser.send(h11.Request(method=method, target="/", headers=[("Host", "egress")]))
    parser.send(h11.EndOfMessage())
    parser.receive_data(head)
    try:
        while True:
            event = parser.next_event()
            if isinstance(event, h11.InformationalResponse):
                _interim(event)
            elif isinstance(event, h11.Response):
                return event, _final_head(event)
            else:
                raise EgressGuardError("response is not valid HTTP", request_dispatched=True)
    except h11.RemoteProtocolError as exc:
        raise _protocol_failure(exc) from None


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


class _HeadRecorder:
    """Keeps the bytes http.client reads while parsing the head."""

    def __init__(self, fp: Any) -> None:
        self.fp = fp
        self.head = bytearray()

    def readline(self, limit: int = -1) -> bytes:
        line = self.fp.readline(limit)
        self.head.extend(line)
        return line

    def __getattr__(self, name: str) -> Any:
        return getattr(self.fp, name)


class _StrictHTTPResponse(http.client.HTTPResponse):
    egress_headers: dict[str, str]

    def begin(self) -> None:
        if self.headers is not None:
            return
        recorder = _HeadRecorder(self.fp)
        self.fp = recorder
        try:
            super().begin()
        except http.client.LineTooLong:
            raise EgressGuardError("response headers too large", request_dispatched=True) from None
        except http.client.RemoteDisconnected:
            raise
        except (http.client.BadStatusLine, http.client.UnknownProtocol):
            raise EgressGuardError("response is not valid HTTP", request_dispatched=True) from None
        except http.client.HTTPException as exc:
            if type(exc) is http.client.HTTPException and "headers" in str(exc):
                raise EgressGuardError("response headers too large", request_dispatched=True) from None
            raise
        finally:
            if self.fp is recorder:
                self.fp = recorder.fp
        event, self.egress_headers = _parse_head(self._method, bytes(recorder.head))
        # Frame the body exactly as h11 would (it rejected every ambiguous head above).
        fields = dict(event.headers)
        self.chunked = b"transfer-encoding" in fields
        self.chunk_left = None
        length = fields.get(b"content-length")
        self.length = int(length) if length is not None and not self.chunked else None
        if self._method == "HEAD" or self.status in (204, 304) or 100 <= self.status < 200:
            self.length = 0
        if not self.chunked and self.length is None:
            self.will_close = True

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
                sock = _ssl_context().wrap_socket(
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
            response = cast(_StrictHTTPResponse, conn.getresponse())
            if 100 <= response.status < 200:
                raise EgressGuardError("unexpected interim response", request_dispatched=True)
            headers = response.egress_headers
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
    client: h11.Connection, reader: asyncio.StreamReader, method: str, max_bytes: int
) -> PinnedHTTPResponse:
    status = 0
    headers: dict[str, str] = {}
    body = bytearray()
    while True:
        event = client.next_event()
        if event is h11.NEED_DATA:
            client.receive_data(await reader.read(_READ_CHUNK_BYTES))
        elif isinstance(event, h11.InformationalResponse):
            _interim(event)
        elif isinstance(event, h11.Response):
            status = event.status_code
            headers = _final_head(event)
            if _declared_length_exceeds(method, status, headers, max_bytes):
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
    request: _PreparedRequest, max_bytes: int, timeout: float, *, destination: _Destination
) -> PinnedHTTPResponse:
    client, wire = _h11_request_bytes(request)
    writer: asyncio.StreamWriter | None = None
    sending = False
    try:
        # DNS, connect, TLS and the whole exchange share one deadline; nothing blocks the loop.
        async with asyncio.timeout(timeout):
            address = await _resolve_async(destination)
            context = _ssl_context() if request.https else None
            reader, writer = await asyncio.open_connection(
                host=address,
                port=request.port,
                ssl=context,
                server_hostname=request.host if context else None,
            )
            sending = True
            writer.write(wire)
            await writer.drain()
            return await _h11_response(client, reader, request.method, max_bytes)
    except EgressGuardError:
        raise
    except TimeoutError:
        if sending:
            raise EgressResponseTimeout() from None
        raise TimeoutError("egress deadline exceeded before sending") from None
    except h11.RemoteProtocolError as exc:
        raise _protocol_failure(exc) from None
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
    destination = _destination_or_raise(
        url,
        label=label,
        allow_private_hosts=allow_private_hosts,
        allow_private_cidrs=allow_private_cidrs,
        allow_private=allow_private,
        allow_http_hosts=allow_http_hosts,
        require_https_in_prod=require_https_in_prod,
    )
    return await _exchange_async(request, max_bytes, timeout, destination=destination)


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
    destination = _destination_or_raise(
        url,
        label=label,
        allow_private_hosts=allow_private_hosts,
        allow_private_cidrs=allow_private_cidrs,
        allow_private=allow_private,
        allow_http_hosts=allow_http_hosts,
        require_https_in_prod=require_https_in_prod,
    )
    reason, address = _resolve(destination)
    if reason:
        raise EgressGuardError(reason)
    return _exchange(request, max_bytes, deadline, address=address)
