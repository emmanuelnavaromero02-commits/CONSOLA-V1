"""Central SSRF/egress guard for outbound HTTP(S) requests."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


_BLOCKED_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")
_METADATA_HOSTS = {"metadata.google.internal", "instance-data", "169.254.169.254"}
_DEFAULT_MAX_BYTES = 2 * 1024 * 1024


class EgressGuardError(ValueError):
    """Raised when an outbound URL is not safe to request."""


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
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _host_set(values: set[str] | list[str] | tuple[str, ...] | None) -> set[str]:
    return {str(item).strip().rstrip(".").lower() for item in (values or []) if str(item).strip()}


def _cidr_list(values: list[str] | tuple[str, ...] | None) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
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
    if require_https_in_prod and is_production_env() and parsed.scheme != "https" and host not in allowed_http_hosts:
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
        if allow_private or host in allowed_hosts or any(ip in cidr for cidr in allowed_cidrs):
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


async def _read_pinned_http_response(reader: asyncio.StreamReader, max_bytes: int) -> PinnedHTTPResponse:
    header_bytes = await reader.readuntil(b"\r\n\r\n")
    if len(header_bytes) > 65536:
        raise EgressGuardError("response headers too large")
    header_text = header_bytes.decode("iso-8859-1", errors="replace")
    lines = header_text.split("\r\n")
    status_parts = lines[0].split(" ", 2)
    status_code = int(status_parts[1]) if len(status_parts) > 1 and status_parts[1].isdigit() else 0
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()

    content_length = headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise EgressGuardError("response exceeds size limit")
    body = bytearray()
    if headers.get("transfer-encoding", "").lower() == "chunked":
        while True:
            line = await reader.readline()
            chunk_size = int(line.split(b";", 1)[0].strip() or b"0", 16)
            if chunk_size == 0:
                await reader.readline()
                break
            if len(body) + chunk_size > max_bytes:
                raise EgressGuardError("response exceeds size limit")
            body.extend(await reader.readexactly(chunk_size))
            await reader.readexactly(2)
    elif content_length and content_length.isdigit():
        body.extend(await reader.readexactly(int(content_length)))
    else:
        while True:
            chunk = await reader.read(min(65536, max_bytes + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_bytes:
                raise EgressGuardError("response exceeds size limit")
    return PinnedHTTPResponse(status_code=status_code, headers=headers, content=bytes(body))


def _read_pinned_http_response_sync(stream: Any, max_bytes: int) -> PinnedHTTPResponse:
    header_parts: list[bytes] = []
    header_size = 0
    while True:
        line = stream.readline(65537)
        if not line:
            break
        header_parts.append(line)
        header_size += len(line)
        if header_size > 65536:
            raise EgressGuardError("response headers too large")
        if line in {b"\r\n", b"\n"}:
            break
    header_bytes = b"".join(header_parts)
    header_text = header_bytes.decode("iso-8859-1", errors="replace")
    lines = header_text.split("\r\n")
    status_parts = lines[0].split(" ", 2) if lines else []
    status_code = int(status_parts[1]) if len(status_parts) > 1 and status_parts[1].isdigit() else 0
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()

    content_length = headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise EgressGuardError("response exceeds size limit")
    body = bytearray()
    if headers.get("transfer-encoding", "").lower() == "chunked":
        while True:
            line = stream.readline(65536)
            chunk_size = int(line.split(b";", 1)[0].strip() or b"0", 16)
            if chunk_size == 0:
                stream.readline(65536)
                break
            if len(body) + chunk_size > max_bytes:
                raise EgressGuardError("response exceeds size limit")
            body.extend(stream.read(chunk_size))
            stream.read(2)
    elif content_length and content_length.isdigit():
        body.extend(stream.read(int(content_length)))
    else:
        while True:
            chunk = stream.read(min(65536, max_bytes + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_bytes:
                raise EgressGuardError("response exceeds size limit")
    return PinnedHTTPResponse(status_code=status_code, headers=headers, content=bytes(body))


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
    if json_body is not None:
        body = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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

    parsed = urlparse(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    ssl_context = ssl.create_default_context() if parsed.scheme == "https" else None
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            host=address,
            port=port,
            ssl=ssl_context,
            server_hostname=host if ssl_context else None,
        ),
        timeout=timeout,
    )
    try:
        request_headers = {
            "Host": host if parsed.port in (None, 80, 443) else f"{host}:{parsed.port}",
            "Connection": "close",
            "Accept": "*/*",
            **(headers or {}),
        }
        if body:
            request_headers["Content-Length"] = str(len(body))
            if content_type:
                request_headers["Content-Type"] = content_type
        header_blob = "".join(f"{name}: {value}\r\n" for name, value in request_headers.items())
        writer.write(f"{method.upper()} {path} HTTP/1.1\r\n{header_blob}\r\n".encode("utf-8") + body)
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        return await asyncio.wait_for(_read_pinned_http_response(reader, max_bytes), timeout=timeout)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


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
    if json_body is not None:
        body = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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

    parsed = urlparse(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    ssl_context = ssl.create_default_context() if parsed.scheme == "https" else None
    sock = socket.create_connection((address, port), timeout=timeout)
    try:
        if ssl_context:
            sock = ssl_context.wrap_socket(sock, server_hostname=host)
        sock.settimeout(timeout)
        request_headers = {
            "Host": host if parsed.port in (None, 80, 443) else f"{host}:{parsed.port}",
            "Connection": "close",
            "Accept": "*/*",
            **(headers or {}),
        }
        if body:
            request_headers["Content-Length"] = str(len(body))
            if content_type:
                request_headers["Content-Type"] = content_type
        header_blob = "".join(f"{name}: {value}\r\n" for name, value in request_headers.items())
        sock.sendall(f"{method.upper()} {path} HTTP/1.1\r\n{header_blob}\r\n".encode("utf-8") + body)
        with sock.makefile("rb") as stream:
            return _read_pinned_http_response_sync(stream, max_bytes)
    finally:
        sock.close()
