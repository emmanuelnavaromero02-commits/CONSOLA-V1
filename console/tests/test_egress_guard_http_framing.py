from __future__ import annotations

import asyncio
import os
import socket
import ssl
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.services import egress_guard


HOST = "egress-test.invalid"
CONSOLE_ROOT = Path(__file__).resolve().parents[1]


class _Server:
    """Threaded test server; every accepted connection runs ``response`` in its own thread."""

    def __init__(
        self,
        response: bytes | Callable[[socket.socket], None] | None = b"",
        *,
        read_request: bool = True,
        tls: ssl.SSLContext | None = None,
    ) -> None:
        self.response = response
        self.read_request = read_request
        self.tls = tls
        self.received = bytearray()
        self.accepted = 0
        self.peer_closed: list[float] = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(32)
        self.port = self._listener.getsockname()[1]
        self._threads: list[threading.Thread] = []
        self._accept = threading.Thread(target=self._loop, daemon=True)
        self._accept.start()

    def url(self, path: str = "/api", scheme: str = "http") -> str:
        return f"{scheme}://{HOST}:{self.port}{path}"

    def _loop(self) -> None:
        while True:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            self.accepted += 1
            worker = threading.Thread(target=self._one, args=(conn,), daemon=True)
            self._threads.append(worker)
            worker.start()

    def _read(self, conn: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        length = 0
        for line in head.split(b"\r\n")[1:]:
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value.strip())
        while len(rest) < length:
            chunk = conn.recv(65536)
            if not chunk:
                break
            rest += chunk
        self.received.extend(head + b"\r\n\r\n" + rest)

    def _one(self, conn: socket.socket) -> None:
        conn.settimeout(10)
        try:
            if self.tls is not None:
                conn = self.tls.wrap_socket(conn, server_side=True)
            if self.read_request:
                self._read(conn)
            if callable(self.response):
                self.response(conn)
            elif self.response:
                conn.sendall(self.response)
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        self._listener.close()
        for worker in self._threads:
            worker.join(timeout=10)


def _wait_for_peer_close(server: _Server, hold: float) -> Callable[[socket.socket], None]:
    def respond(conn: socket.socket) -> None:
        conn.settimeout(hold)
        try:
            while conn.recv(1024):
                pass
        except OSError:
            pass
        server.peer_closed.append(time.monotonic())

    return respond


def _resolve_to(address: str):
    def getaddrinfo(host, port, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))]

    return getaddrinfo


@pytest.fixture(autouse=True)
def _pinned_resolution(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(egress_guard.socket, "getaddrinfo", _resolve_to("127.0.0.1"))


@pytest.fixture(params=["sync", "async"])
def send(request) -> Callable[..., egress_guard.PinnedHTTPResponse]:
    def call(url: str, **kwargs) -> egress_guard.PinnedHTTPResponse:
        kwargs.setdefault("allow_private_hosts", {HOST})
        kwargs.setdefault("timeout", 5.0)
        method = kwargs.pop("method", "GET")
        if request.param == "sync":
            return egress_guard.pinned_request_sync(method, url, **kwargs)
        return asyncio.run(egress_guard.pinned_request(method, url, **kwargs))

    call.transport = request.param  # type: ignore[attr-defined]
    return call


def _request_head(server: _Server) -> list[bytes]:
    return bytes(server.received).split(b"\r\n\r\n", 1)[0].split(b"\r\n")


def test_content_length_response_and_pinned_request_shape(send):
    server = _Server(
        b"HTTP/1.1 201 Created\r\nContent-Length: 11\r\nX-Multi: a\r\nX-Multi: b\r\n\r\n"
        b'{"ok":true}'
    )
    try:
        response = send(server.url("/api/v1?x=1"), method="POST", json_body={"a": 1}, headers={"X-Api-Key": "k"})
    finally:
        server.close()
    assert response.status_code == 201 and response.json() == {"ok": True}
    assert response.headers["x-multi"] == "a, b"
    lines = _request_head(server)
    assert lines[0] == b"POST /api/v1?x=1 HTTP/1.1"
    headers = {line.split(b":", 1)[0].lower(): line.split(b":", 1)[1].strip() for line in lines[1:]}
    assert headers[b"host"] == f"{HOST}:{server.port}".encode()
    assert headers[b"accept-encoding"] == b"identity"
    assert headers[b"connection"] == b"close"
    assert headers[b"content-type"] == b"application/json"
    assert headers[b"content-length"] == b"7"
    assert bytes(server.received).endswith(b'{"a":1}')


def test_chunked_response_is_reassembled(send):
    server = _Server(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"4;ext=1\r\nabcd\r\n3\r\nefg\r\n0\r\n\r\n"
    )
    try:
        response = send(server.url())
    finally:
        server.close()
    assert response.content == b"abcdefg"


def test_close_delimited_response_reads_until_eof(send):
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nstreamed-body")
    try:
        response = send(server.url())
    finally:
        server.close()
    assert response.content == b"streamed-body"


@pytest.mark.parametrize(
    "method,payload,status,body",
    [
        ("HEAD", b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n", 200, b""),
        ("GET", b"HTTP/1.1 204 No Content\r\n\r\n", 204, b""),
        ("GET", b"HTTP/1.1 304 Not Modified\r\n\r\n", 304, b""),
    ],
)
def test_responses_without_a_body_do_not_wait_for_one(send, method, payload, status, body):
    def respond(conn: socket.socket) -> None:
        conn.sendall(payload)
        time.sleep(3)

    server = _Server(respond)
    started = time.monotonic()
    try:
        response = send(server.url(), method=method, timeout=5)
        elapsed = time.monotonic() - started
    finally:
        server.close()
    assert (response.status_code, response.content) == (status, body)
    assert elapsed < 2


@pytest.mark.parametrize(
    "method,payload,body",
    [
        ("HEAD", b"HTTP/1.1 200 OK\r\nContent-Length: 5000000\r\n\r\n", b""),
        ("GET", b"HTTP/1.1 204 No Content\r\nContent-Length: 5000000\r\n\r\n", b""),
        ("GET", b"HTTP/1.1 304 Not Modified\r\nContent-Length: 5000000\r\n\r\n", b""),
        (
            "GET",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 5000000\r\n\r\n"
            b"2\r\nok\r\n0\r\n\r\n",
            b"ok",
        ),
    ],
    ids=["head", "204", "304", "chunked-overrides-length"],
)
def test_size_limit_ignores_a_length_that_does_not_frame_the_body(send, method, payload, body):
    server = _Server(payload)
    try:
        response = send(server.url(), method=method, max_bytes=64)
    finally:
        server.close()
    assert response.content == body


@pytest.mark.parametrize(
    "payload",
    [
        b"HTTP/1.1 200 OK\r\nContent-Length: 4096\r\n\r\n" + b"x" * 4096,
        b"HTTP/1.1 200 OK\r\n\r\n" + b"x" * 4096,
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n800\r\n" + b"x" * 2048 + b"\r\n0\r\n\r\n",
    ],
    ids=["content-length", "close-delimited", "chunked"],
)
def test_oversized_responses_are_rejected_after_dispatch(send, payload):
    server = _Server(payload)
    try:
        with pytest.raises(egress_guard.EgressGuardError, match="size limit") as error:
            send(server.url(), max_bytes=1024)
    finally:
        server.close()
    assert error.value.request_dispatched is True


@pytest.mark.parametrize("size_line", [b"-1", b"zz", b"+4", b"1_0", b" "])
def test_invalid_chunk_sizes_fail_without_unbounded_reads(send, size_line):
    def respond(conn: socket.socket) -> None:
        conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + size_line + b"\r\n")
        conn.sendall(b"y" * 256 * 1024)
        time.sleep(3)

    server = _Server(respond)
    started = time.monotonic()
    try:
        with pytest.raises(egress_guard.EgressGuardError) as error:
            send(server.url(), max_bytes=1024, timeout=10)
        elapsed = time.monotonic() - started
    finally:
        server.close()
    assert error.value.request_dispatched is True
    assert elapsed < 2.5


@pytest.mark.parametrize(
    "payload",
    [
        b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabcd",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n8\r\nabcd",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nab\r\n",
        b"HTTP/1.1 2",
        b"",
    ],
    ids=["content-length", "chunk-body", "missing-terminator", "status-line", "no-response"],
)
def test_short_or_broken_responses_are_ambiguous(send, payload):
    server = _Server(payload)
    try:
        with pytest.raises(egress_guard.EgressGuardError) as error:
            send(server.url(), method="POST", json_body={"a": 1})
    finally:
        server.close()
    assert error.value.request_dispatched is True


def test_continue_then_final_response(send):
    server = _Server(
        b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
    )
    try:
        response = send(server.url(), method="POST", body=b"x", content_type="text/plain")
    finally:
        server.close()
    assert response.status_code == 200 and response.content == b"ok"


@pytest.mark.parametrize(
    "interim",
    [b"HTTP/1.1 103 Early Hints\r\nLink: </a.css>; rel=preload\r\n\r\n", b"HTTP/1.1 101 Switching Protocols\r\n\r\n"],
    ids=["103", "101"],
)
def test_other_interim_responses_are_ambiguous(send, interim):
    server = _Server(interim + b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    try:
        with pytest.raises(egress_guard.EgressGuardError) as error:
            send(server.url())
    finally:
        server.close()
    assert error.value.request_dispatched is True


def test_oversized_response_headers_are_rejected(send):
    many = b"".join(b"X-H%d: " % index + b"v" * 900 + b"\r\n" for index in range(90))
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n" + many + b"\r\n")
    try:
        with pytest.raises(egress_guard.EgressGuardError, match="headers too large") as error:
            send(server.url())
    finally:
        server.close()
    assert error.value.request_dispatched is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"headers": {"X-Api-Key": "k\r\nX-Injected: 1"}},
        {"headers": {"X-Api-Key": "k\nX-Injected: 1"}},
        {"headers": {"X-Api-Key": "k\r\n X-Folded"}},
        {"headers": {"X-Bad Name": "v"}},
        {"headers": {"X-Api-Key\r\nX-Injected": "v"}},
        {"headers": {"X-Nul": "a\x00b"}},
        {"headers": {"X-Ctl": "a\x01b"}},
        {"headers": {"X-Esc": "\x1b[0m"}},
        {"headers": {"X-Vt": "a\x0bb"}},
        {"headers": {"X-Ff": "a\x0cb"}},
        {"headers": {"X-Del": "a\x7fb"}},
        {"method": "POST", "body": b"x", "content_type": "text/plain\x01"},
        {"method": "GET\r\nX-Injected: 1"},
        {"path": "/api\r\nX-Injected: 1"},
        {"path": "/api\tx"},
        {"path": "/api?q=\x7f"},
        {"method": "POST", "body": b"x", "content_type": "text/plain\r\nX-Injected: 1"},
    ],
)
def test_injection_is_refused_before_anything_reaches_the_wire(send, kwargs):
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
    kwargs = dict(kwargs)
    path = kwargs.pop("path", "/api")
    try:
        with pytest.raises(egress_guard.EgressGuardError) as error:
            send(server.url(path), **kwargs)
        time.sleep(0.2)
    finally:
        server.close()
    assert error.value.request_dispatched is False
    assert server.accepted == 0 and bytes(server.received) == b""


@pytest.mark.parametrize(
    "path,wire",
    [
        ("/specs/café.yaml", b"/specs/caf%C3%A9.yaml"),
        ("/odata/v2/User?$filter=city eq 'León'", b"/odata/v2/User?$filter=city%20eq%20'Le%C3%B3n'"),
        ("/already%20encoded/a%2Fb?x=%41&y=1", b"/already%20encoded/a%2Fb?x=%41&y=1"),
        ("/api /other", b"/api%20/other"),
    ],
)
def test_non_ascii_and_spaces_are_percent_encoded(send, path, wire):
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    try:
        response = send(server.url(path))
    finally:
        server.close()
    assert response.status_code == 200
    assert _request_head(server)[0] == b"GET " + wire + b" HTTP/1.1"


@pytest.mark.parametrize(
    "name", ["Host", "content-length", "Transfer-Encoding", "Connection", "Accept-Encoding"]
)
def test_callers_cannot_override_transport_framing(send, name):
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
    try:
        with pytest.raises(egress_guard.EgressGuardError, match="owned by the transport") as error:
            send(server.url(), method="POST", body=b"abc", headers={name: "evil.example"})
        time.sleep(0.2)
    finally:
        server.close()
    assert error.value.request_dispatched is False
    assert server.accepted == 0 and bytes(server.received) == b""


def test_slow_response_hits_the_total_deadline(send):
    def trickle(conn: socket.socket) -> None:
        conn.sendall(b"HTTP/1.1 200 OK\r\n")
        for index in range(40):
            try:
                conn.sendall(b"X-Slow-%d: 1\r\n" % index)
            except OSError:
                return
            time.sleep(0.2)

    server = _Server(trickle)
    started = time.monotonic()
    try:
        with pytest.raises(egress_guard.EgressResponseTimeout, match="timed out") as error:
            send(server.url(), timeout=1.0)
        elapsed = time.monotonic() - started
    finally:
        server.close()
    assert error.value.request_dispatched is True
    assert elapsed < 3.0


def test_connect_failure_is_not_reported_as_dispatched(send):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    with pytest.raises(OSError) as error:
        send(f"http://{HOST}:{port}/api", method="POST", body=b"x", timeout=2)
    assert not isinstance(error.value, egress_guard.EgressGuardError)


def test_blocked_destination_never_connects(send, monkeypatch):
    monkeypatch.setattr(egress_guard.socket, "getaddrinfo", _resolve_to("10.0.0.7"))
    monkeypatch.setattr(
        egress_guard.socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail("no connection expected"),
    )
    with pytest.raises(egress_guard.EgressGuardError, match="non-public") as error:
        send("http://internal.example/api", allow_private_hosts=None)
    assert error.value.request_dispatched is False


def test_sync_and_async_paths_put_the_same_bytes_on_the_wire():
    payload = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nX-A: 1\r\n\r\n3\r\nabc\r\n0\r\n\r\n"
    sync_server = _Server(payload)
    async_server = _Server(payload)
    headers = {"X-Api-Key": "k", "Authorization": "Bearer pasted ", "X-Tabbed": "\ta\tb\t", "X-Blank": " "}
    kwargs = {"json_body": {"k": "v"}, "headers": headers, "allow_private_hosts": {HOST}, "timeout": 5}
    try:
        sync_response = egress_guard.pinned_request_sync("POST", sync_server.url(), **kwargs)
        async_response = asyncio.run(egress_guard.pinned_request("POST", async_server.url(), **kwargs))
    finally:
        sync_server.close()
        async_server.close()
    assert sync_response == async_response
    assert bytes(sync_server.received).replace(str(sync_server.port).encode(), b"PORT") == bytes(
        async_server.received
    ).replace(str(async_server.port).encode(), b"PORT")
    head = _request_head(sync_server)
    assert b"Authorization: Bearer pasted" in head
    assert b"X-Tabbed: a\tb" in head
    assert any(line.rstrip(b" ") == b"X-Blank:" for line in head)


# ---------------------------------------------------------------- one verdict on response heads


def _outcome(call: Callable[[], egress_guard.PinnedHTTPResponse]) -> tuple:
    try:
        response = call()
    except egress_guard.EgressGuardError as exc:
        return ("error", str(exc), exc.request_dispatched)
    return ("ok", response.status_code, response.headers, response.content)


def _both_paths(payload: bytes, **kwargs) -> dict[str, tuple]:
    kwargs = {"allow_private_hosts": {HOST}, "timeout": 5, "max_bytes": 64, **kwargs}
    outcomes = {}
    for transport in ("sync", "async"):
        server = _Server(payload)
        try:
            if transport == "sync":
                outcomes[transport] = _outcome(lambda: egress_guard.pinned_request_sync("GET", server.url(), **kwargs))
            else:
                outcomes[transport] = _outcome(
                    lambda: asyncio.run(egress_guard.pinned_request("GET", server.url(), **kwargs))
                )
        finally:
            server.close()
    return outcomes


_OK = b"HTTP/1.1 200 OK\r\n"


def _fields(count: int) -> bytes:
    return _OK + b"".join(b"X-H%d: v\r\n" % index for index in range(count - 1)) + b"Content-Length: 2\r\n\r\nok"


_REJECTED_HEADS = {
    "conflicting-content-length": _OK + b"Content-Length: 2\r\nContent-Length: 3\r\n\r\nokk",
    "conflicting-length-list": _OK + b"Content-Length: 2, 3\r\n\r\nokk",
    "negative-length": _OK + b"Content-Length: -1\r\n\r\nok",
    "empty-length": _OK + b"Content-Length:\r\n\r\nok",
    "space-before-colon": _OK + b"Content-Length : 2\r\n\r\nok",
    "gzip-then-chunked": _OK + b"Transfer-Encoding: gzip, chunked\r\n\r\n2\r\nok\r\n0\r\n\r\n",
    "duplicated-transfer-encoding": _OK
    + b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nok\r\n0\r\n\r\n",
    "identity-transfer-encoding": _OK + b"Transfer-Encoding: identity\r\nContent-Length: 2\r\n\r\nok",
    "nul-in-value": _OK + b"X-A: a\x00b\r\nContent-Length: 2\r\n\r\nok",
    "bare-cr-in-value": _OK + b"X-A: a\rX-B: b\r\nContent-Length: 2\r\n\r\nok",
    "vertical-tab-in-value": _OK + b"X-A: a\x0bb\r\nContent-Length: 2\r\n\r\nok",
    "non-token-name": _OK + b"X(A): b\r\nContent-Length: 2\r\n\r\nok",
    "continuation-first": _OK + b" X-A: b\r\nContent-Length: 2\r\n\r\nok",
    "line-without-colon": _OK + b"From nobody\r\nContent-Length: 2\r\n\r\nok",
    "http-2-status-line": b"HTTP/2.0 200 OK\r\nContent-Length: 2\r\n\r\nok",
    "http-1.2-status-line": b"HTTP/1.2 200 OK\r\nContent-Length: 2\r\n\r\nok",
    "not-http": b"NOT HTTP\r\n\r\n",
    "eof-inside-head": _OK + b"Content-Length: 2\r\n",
}


@pytest.mark.parametrize("payload", list(_REJECTED_HEADS.values()), ids=list(_REJECTED_HEADS))
def test_ambiguous_response_heads_are_rejected_by_both_paths(payload):
    outcomes = _both_paths(payload)
    assert outcomes["sync"] == outcomes["async"] == ("error", "response is not valid HTTP", True)


@pytest.mark.parametrize(
    "payload",
    [_fields(100), _fields(101), b"HTTP/1.1 200 " + b"r" * 70000 + b"\r\n\r\n"],
    ids=["100-fields", "101-fields", "oversized-status-line"],
)
def test_header_count_and_size_limits_match_on_both_paths(payload):
    outcomes = _both_paths(payload)
    assert outcomes["sync"] == outcomes["async"] == ("error", "response headers too large", True)


@pytest.mark.parametrize(
    "payload,headers,content",
    [
        (_fields(99), {**{f"x-h{index}": "v" for index in range(98)}, "content-length": "2"}, b"ok"),
        (_OK + b"Content-Length: 2\r\nContent-Length: 2\r\n\r\nok", {"content-length": "2"}, b"ok"),
        (_OK + b"Content-Length: 2, 2\r\n\r\nokEXTRA", {"content-length": "2"}, b"ok"),
        (
            _OK + b"Transfer-Encoding: Chunked \r\n\r\n2\r\nok\r\n0\r\n\r\n",
            {"transfer-encoding": "chunked"},
            b"ok",
        ),
        (_OK + b"X-Folded: a\r\n  b\r\nContent-Length: 2\r\n\r\nok", {"x-folded": "a b", "content-length": "2"}, b"ok"),
        (b"HTTP/1.1 200\r\nContent-Length: 2\r\n\r\nok", {"content-length": "2"}, b"ok"),
    ],
    ids=["99-fields", "repeated-equal-length", "equal-length-list", "chunked-case-and-blanks", "folded", "no-reason"],
)
def test_unambiguous_heads_frame_the_same_body_on_both_paths(payload, headers, content):
    outcomes = _both_paths(payload)
    assert outcomes["sync"] == outcomes["async"] == ("ok", 200, headers, content)


# ---------------------------------------------------------------- deadlines and cancellation


def _slow_connect(monkeypatch, delay: float) -> None:
    real_connect = socket.create_connection
    real_open = asyncio.open_connection

    def slow_create_connection(address, timeout=None, *args, **kwargs):
        time.sleep(delay)
        return real_connect(address, timeout, *args, **kwargs)

    async def slow_open_connection(*args, **kwargs):
        await asyncio.sleep(delay)
        return await real_open(*args, **kwargs)

    monkeypatch.setattr(egress_guard.socket, "create_connection", slow_create_connection)
    monkeypatch.setattr(egress_guard.asyncio, "open_connection", slow_open_connection)


def test_deadline_spent_while_connecting_is_not_dispatched(send, monkeypatch):
    _slow_connect(monkeypatch, 1.2)
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    try:
        with pytest.raises(TimeoutError) as error:
            send(server.url(), method="POST", json_body={"n": 1}, timeout=1.0)
        time.sleep(0.5)
    finally:
        server.close()
    assert not isinstance(error.value, egress_guard.EgressGuardError)
    assert b"POST" not in bytes(server.received)


def _slow_resolver(monkeypatch, delay: float) -> list[str]:
    threads: list[str] = []

    def getaddrinfo(host, port, *_args, **_kwargs):
        threads.append(threading.current_thread().name)
        time.sleep(delay)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 0))]

    monkeypatch.setattr(egress_guard.socket, "getaddrinfo", getaddrinfo)
    return threads


def test_slow_dns_does_not_stall_the_event_loop(monkeypatch):
    threads = _slow_resolver(monkeypatch, 1.0)
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")

    async def scenario() -> tuple[egress_guard.PinnedHTTPResponse, float]:
        gaps: list[float] = []
        done = asyncio.Event()

        async def ticker() -> None:
            last = time.monotonic()
            while not done.is_set():
                await asyncio.sleep(0.02)
                gaps.append(time.monotonic() - last)
                last = time.monotonic()

        ticking = asyncio.create_task(ticker())
        try:
            response = await egress_guard.pinned_request("GET", server.url(), timeout=5, allow_private_hosts={HOST})
        finally:
            done.set()
            await ticking
        return response, max(gaps)

    try:
        response, worst_gap = asyncio.run(scenario())
    finally:
        server.close()
    assert response.content == b"ok"
    assert worst_gap < 0.3, f"event loop stalled {worst_gap:.2f}s during DNS"
    assert threads and threading.main_thread().name not in threads


def test_the_async_deadline_covers_dns(monkeypatch):
    _slow_resolver(monkeypatch, 1.0)
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")

    async def scenario() -> tuple[BaseException | None, float]:
        started = time.monotonic()
        try:
            await egress_guard.pinned_request(
                "POST", server.url(), json_body={"n": 1}, timeout=0.5, allow_private_hosts={HOST}
            )
        except TimeoutError as exc:
            return exc, time.monotonic() - started
        return None, time.monotonic() - started

    try:
        error, elapsed = asyncio.run(scenario())
    finally:
        server.close()
    assert isinstance(error, TimeoutError) and not isinstance(error, egress_guard.EgressGuardError)
    assert elapsed < 0.9
    assert server.accepted == 0


def test_time_spent_resolving_counts_against_the_deadline(send, monkeypatch):
    _slow_resolver(monkeypatch, 1.0)
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    try:
        with pytest.raises(TimeoutError) as error:
            send(server.url(), method="POST", json_body={"n": 1}, timeout=0.5)
        time.sleep(0.2)
    finally:
        server.close()
    assert not isinstance(error.value, egress_guard.EgressGuardError)
    assert server.accepted == 0


def test_async_resolution_applies_the_same_address_policy(monkeypatch):
    monkeypatch.setattr(egress_guard.socket, "getaddrinfo", _resolve_to("169.254.169.254"))
    with pytest.raises(egress_guard.EgressGuardError) as error:
        asyncio.run(egress_guard.pinned_request("GET", "http://public.example/api", timeout=2))
    assert str(error.value) == egress_guard.resolve_url_address("http://public.example/api")[0]
    assert error.value.request_dispatched is False


def test_cancelled_async_calls_release_their_sockets_and_block_nobody():
    slow = _Server(None)
    slow.response = _wait_for_peer_close(slow, hold=15)
    fast = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
    kwargs = {"allow_private_hosts": {HOST}}

    async def scenario() -> tuple[int, float, egress_guard.PinnedHTTPResponse, float]:
        cancelled = 0
        for _ in range(12):
            try:
                await asyncio.wait_for(
                    egress_guard.pinned_request("POST", slow.url(), json_body={"tool": "x"}, timeout=120, **kwargs),
                    0.3,
                )
            except TimeoutError:
                cancelled += 1
        cancelled_at = time.monotonic()
        started = time.monotonic()
        response = await egress_guard.pinned_request("GET", fast.url("/mcp/tools"), timeout=3, **kwargs)
        return cancelled, cancelled_at, response, time.monotonic() - started

    try:
        cancelled, cancelled_at, response, elapsed = asyncio.run(scenario())
        deadline = time.monotonic() + 5
        while len(slow.peer_closed) < 12 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        fast.close()
        slow.close()
    assert cancelled == 12
    assert response.status_code == 200 and elapsed < 1.0
    assert len(slow.peer_closed) == 12, "every cancelled call must close its socket"
    assert max(slow.peer_closed) - cancelled_at < 2.0
    assert not [thread.name for thread in threading.enumerate() if thread.name.startswith("egress")]


def test_async_timeout_is_per_call_even_with_other_calls_in_flight():
    hang = _Server(None)
    hang.response = _wait_for_peer_close(hang, hold=10)
    kwargs = {"allow_private_hosts": {HOST}}

    async def scenario() -> list[tuple[str, float]]:
        async def one(timeout: float) -> tuple[str, float]:
            started = time.monotonic()
            try:
                await egress_guard.pinned_request("GET", hang.url(), timeout=timeout, **kwargs)
                return "ok", time.monotonic() - started
            except egress_guard.EgressResponseTimeout:
                return "timeout", time.monotonic() - started

        return await asyncio.gather(*(one(0.5) for _ in range(20)))

    try:
        results = asyncio.run(scenario())
    finally:
        hang.close()
    assert {outcome for outcome, _ in results} == {"timeout"}
    assert max(elapsed for _, elapsed in results) < 1.5


def test_a_cancelled_call_does_not_delay_process_exit():
    hang = _Server(None)
    hang.response = _wait_for_peer_close(hang, hold=15)
    script = textwrap.dedent(
        f"""
        import asyncio, os, socket, sys
        sys.path.insert(0, {str(CONSOLE_ROOT)!r})
        os.environ["APP_ENV"] = "test"
        from app.services import egress_guard
        egress_guard.socket.getaddrinfo = lambda host, port, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 0))
        ]

        async def main():
            try:
                await asyncio.wait_for(
                    egress_guard.pinned_request(
                        "POST", {hang.url()!r}, json_body={{}}, timeout=120,
                        allow_private_hosts={{{HOST!r}}},
                    ),
                    0.5,
                )
            except TimeoutError:
                print("cancelled", flush=True)

        asyncio.run(main())
        """
    )
    started = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "APP_ENV": "test"},
        )
        elapsed = time.monotonic() - started
    finally:
        hang.close()
    assert result.returncode == 0, result.stderr[-2000:]
    assert "cancelled" in result.stdout
    assert elapsed < 8, f"process exit took {elapsed:.1f}s"


# ---------------------------------------------------------------- TLS


@pytest.fixture(scope="module")
def tls_material(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("egress-tls")
    material: dict[str, Path] = {}
    for name in (HOST, "other.invalid"):
        cert, key = root / f"{name}.pem", root / f"{name}.key"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                "-keyout", str(key), "-out", str(cert), "-subj", f"/CN={name}",
                "-addext", f"subjectAltName=DNS:{name}",
            ],
            check=True,
            capture_output=True,
        )
        material[name] = cert
        material[f"{name}.key"] = key
    return material


@pytest.fixture
def trust(monkeypatch, tls_material) -> list[ssl.SSLContext]:
    real = ssl.create_default_context
    built: list[ssl.SSLContext] = []

    def trusting(*args, **kwargs):
        context = real(*args, **kwargs)
        context.load_verify_locations(str(tls_material[HOST]))
        context.load_verify_locations(str(tls_material["other.invalid"]))
        built.append(context)
        return context

    monkeypatch.setattr(egress_guard.ssl, "create_default_context", trusting)
    monkeypatch.setattr(egress_guard, "_SSL_CONTEXT", None)
    return built


def _server_tls(tls_material, name: str, seen_sni: list[str]) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(tls_material[name]), str(tls_material[f"{name}.key"]))
    context.sni_callback = lambda _sock, server_name, _ctx: seen_sni.append(server_name)
    return context


def test_tls_verifies_the_url_hostname_over_the_pinned_address(send, trust, tls_material):
    seen: list[str] = []
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok", tls=_server_tls(tls_material, HOST, seen))
    try:
        response = send(server.url("/x?q=1", scheme="https"))
    finally:
        server.close()
    assert response.content == b"ok"
    assert seen == [HOST]
    assert _request_head(server)[0] == b"GET /x?q=1 HTTP/1.1"


def test_the_verifying_context_is_built_once_for_both_paths(trust, tls_material):
    seen: list[str] = []
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok", tls=_server_tls(tls_material, HOST, seen))
    kwargs = {"allow_private_hosts": {HOST}, "timeout": 5}
    try:
        for _ in range(2):
            egress_guard.pinned_request_sync("GET", server.url(scheme="https"), **kwargs)
            asyncio.run(egress_guard.pinned_request("GET", server.url(scheme="https"), **kwargs))
    finally:
        server.close()
    assert len(trust) == 1
    assert seen == [HOST] * 4


def test_tls_rejects_a_certificate_for_another_name_before_sending(send, trust, tls_material):
    seen: list[str] = []
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok", tls=_server_tls(tls_material, "other.invalid", seen))
    try:
        with pytest.raises(ssl.SSLError):
            send(server.url(scheme="https"), method="POST", body=b"x")
        time.sleep(0.2)
    finally:
        server.close()
    assert bytes(server.received) == b""


def test_tls_handshake_is_bounded_by_the_total_deadline(send):
    stop = threading.Event()

    def drip(conn: socket.socket) -> None:
        conn.recv(65536)
        conn.sendall(b"\x16\x03\x03\x40\x00")
        while not stop.is_set():
            conn.sendall(b"\x02")
            time.sleep(0.3)

    server = _Server(drip, read_request=False)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError) as error:
            send(server.url(scheme="https"), timeout=1.0)
        elapsed = time.monotonic() - started
    finally:
        stop.set()
        server.close()
    assert not isinstance(error.value, egress_guard.EgressGuardError)
    assert elapsed < 2.5
