from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable

import pytest

from app.services import egress_guard


HOST = "egress-test.invalid"


class _Server:
    def __init__(
        self,
        response: bytes | Callable[[socket.socket], None] = b"",
        *,
        accept_timeout: float = 5.0,
    ) -> None:
        self.response = response
        self.received = bytearray()
        self.accepted = 0
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        self._listener.settimeout(accept_timeout)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def url(self, path: str = "/api") -> str:
        return f"http://{HOST}:{self.port}{path}"

    def _read_request(self, conn: socket.socket) -> None:
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

    def _serve(self) -> None:
        try:
            conn, _ = self._listener.accept()
        except OSError:
            return
        self.accepted += 1
        with conn:
            conn.settimeout(5)
            try:
                self._read_request(conn)
                if callable(self.response):
                    self.response(conn)
                else:
                    conn.sendall(self.response)
            except OSError:
                pass

    def close(self) -> None:
        self._thread.join(timeout=10)
        self._listener.close()


def _resolve_to(address: str):
    def getaddrinfo(host, port, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))]

    return getaddrinfo


@pytest.fixture(autouse=True)
def _pinned_resolution(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(egress_guard.socket, "getaddrinfo", _resolve_to("127.0.0.1"))


def _call(server: _Server, **kwargs) -> egress_guard.PinnedHTTPResponse:
    kwargs.setdefault("allow_private_hosts", {HOST})
    kwargs.setdefault("timeout", 5.0)
    return egress_guard.pinned_request_sync(kwargs.pop("method", "GET"), server.url(kwargs.pop("path", "/api")), **kwargs)


def _request_head(server: _Server) -> list[bytes]:
    return bytes(server.received).split(b"\r\n\r\n", 1)[0].split(b"\r\n")


def test_content_length_response_and_pinned_request_shape():
    server = _Server(
        b"HTTP/1.1 201 Created\r\nContent-Length: 11\r\nX-Multi: a\r\nX-Multi: b\r\n\r\n"
        b'{"ok":true}'
    )
    try:
        response = _call(server, method="POST", path="/api/v1?x=1", json_body={"a": 1}, headers={"X-Api-Key": "k"})
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


def test_chunked_response_is_reassembled():
    server = _Server(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"4;ext=1\r\nabcd\r\n3\r\nefg\r\n0\r\n\r\n"
    )
    try:
        response = _call(server)
    finally:
        server.close()
    assert response.content == b"abcdefg"


def test_close_delimited_response_reads_until_eof():
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nstreamed-body")
    try:
        response = _call(server)
    finally:
        server.close()
    assert response.content == b"streamed-body"


@pytest.mark.parametrize(
    "payload",
    [
        b"HTTP/1.1 200 OK\r\nContent-Length: 4096\r\n\r\n" + b"x" * 4096,
        b"HTTP/1.1 200 OK\r\n\r\n" + b"x" * 4096,
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n800\r\n" + b"x" * 2048 + b"\r\n0\r\n\r\n",
    ],
    ids=["content-length", "close-delimited", "chunked"],
)
def test_oversized_responses_are_rejected_after_dispatch(payload):
    server = _Server(payload)
    try:
        with pytest.raises(egress_guard.EgressGuardError, match="size limit") as error:
            _call(server, max_bytes=1024)
    finally:
        server.close()
    assert error.value.request_dispatched is True


@pytest.mark.parametrize("size_line", [b"-1", b"zz", b"+4", b"1_0", b" "])
def test_invalid_chunk_sizes_fail_without_unbounded_reads(size_line):
    def respond(conn: socket.socket) -> None:
        conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + size_line + b"\r\n")
        conn.sendall(b"y" * 256 * 1024)
        time.sleep(3)

    server = _Server(respond)
    started = time.monotonic()
    try:
        with pytest.raises(egress_guard.EgressGuardError) as error:
            _call(server, max_bytes=1024, timeout=10)
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
def test_short_or_broken_responses_are_ambiguous(payload):
    server = _Server(payload)
    try:
        with pytest.raises(egress_guard.EgressGuardError) as error:
            _call(server, method="POST", json_body={"a": 1})
    finally:
        server.close()
    assert error.value.request_dispatched is True


def test_continue_then_final_response():
    server = _Server(
        b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
    )
    try:
        response = _call(server, method="POST", body=b"x", content_type="text/plain")
    finally:
        server.close()
    assert response.status_code == 200 and response.content == b"ok"


def test_other_interim_responses_are_ambiguous():
    server = _Server(
        b"HTTP/1.1 103 Early Hints\r\nLink: </a.css>; rel=preload\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
    )
    try:
        with pytest.raises(egress_guard.EgressGuardError, match="interim") as error:
            _call(server)
    finally:
        server.close()
    assert error.value.request_dispatched is True


def test_oversized_response_headers_are_rejected():
    many = b"".join(b"X-H%d: " % index + b"v" * 900 + b"\r\n" for index in range(90))
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n" + many + b"\r\n")
    try:
        with pytest.raises(egress_guard.EgressGuardError, match="headers too large") as error:
            _call(server)
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
        {"method": "GET\r\nX-Injected: 1"},
        {"path": "/api\r\nX-Injected: 1"},
        {"path": "/api /other"},
        {"method": "POST", "body": b"x", "content_type": "text/plain\r\nX-Injected: 1"},
    ],
)
def test_injection_is_refused_before_anything_reaches_the_wire(kwargs):
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n", accept_timeout=1.0)
    try:
        with pytest.raises(egress_guard.EgressGuardError) as error:
            _call(server, **kwargs)
    finally:
        server.close()
    assert error.value.request_dispatched is False
    assert server.accepted == 0 and bytes(server.received) == b""


@pytest.mark.parametrize(
    "name", ["Host", "content-length", "Transfer-Encoding", "Connection", "Accept-Encoding"]
)
def test_callers_cannot_override_transport_framing(name):
    server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n", accept_timeout=1.0)
    try:
        with pytest.raises(egress_guard.EgressGuardError, match="owned by the transport") as error:
            _call(server, method="POST", body=b"abc", headers={name: "evil.example"})
    finally:
        server.close()
    assert error.value.request_dispatched is False
    assert server.accepted == 0 and bytes(server.received) == b""


def test_slow_response_hits_the_total_deadline():
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
        with pytest.raises(egress_guard.EgressGuardError, match="timed out") as error:
            _call(server, timeout=1.0)
        elapsed = time.monotonic() - started
    finally:
        server.close()
    assert error.value.request_dispatched is True
    assert elapsed < 3.0


def test_connect_failure_is_not_reported_as_dispatched():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    with pytest.raises(OSError) as error:
        egress_guard.pinned_request_sync(
            "POST", f"http://{HOST}:{port}/api", body=b"x", allow_private_hosts={HOST}, timeout=2
        )
    assert not isinstance(error.value, egress_guard.EgressGuardError)


def test_blocked_destination_never_connects(monkeypatch):
    monkeypatch.setattr(egress_guard.socket, "getaddrinfo", _resolve_to("10.0.0.7"))
    monkeypatch.setattr(
        egress_guard.socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail("no connection expected"),
    )
    with pytest.raises(egress_guard.EgressGuardError, match="non-public") as error:
        egress_guard.pinned_request_sync("GET", "http://internal.example/api")
    assert error.value.request_dispatched is False


@pytest.mark.asyncio
async def test_sync_and_async_paths_behave_the_same():
    payload = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nX-A: 1\r\n\r\n3\r\nabc\r\n0\r\n\r\n"
    sync_server = _Server(payload)
    async_server = _Server(payload)
    try:
        sync_response = _call(sync_server, method="POST", json_body={"k": "v"})
        async_response = await egress_guard.pinned_request(
            "POST",
            async_server.url(),
            json_body={"k": "v"},
            allow_private_hosts={HOST},
            timeout=5,
        )
    finally:
        sync_server.close()
        async_server.close()
    assert sync_response == async_response
    assert bytes(sync_server.received).replace(str(sync_server.port).encode(), b"PORT") == bytes(
        async_server.received
    ).replace(str(async_server.port).encode(), b"PORT")

    bad_server = _Server(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nab")
    try:
        with pytest.raises(egress_guard.EgressGuardError) as error:
            await egress_guard.pinned_request("GET", bad_server.url(), allow_private_hosts={HOST}, timeout=5)
    finally:
        bad_server.close()
    assert error.value.request_dispatched is True
