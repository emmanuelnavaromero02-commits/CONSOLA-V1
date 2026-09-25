from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import parse_qsl, urlsplit


WEB_URL = re.compile(r"(?i)\bhttps?://[^\s\"'<>,]+")
_INTERNAL_WEB_PATH = re.compile(
    r"(?i)(?<![a-z0-9])/(?:data|etc|internal|mnt|opt|private|root|run|srv|tmp|usr|var)"
    r"(?:/|$)"
)
_TECHNICAL_WEB_FILE = re.compile(
    r"(?i)(?:^|/)[a-z0-9_.-]+\."
    r"(?:cfg|conf|csv|db|duckdb|env|hcl|ini|js|json|jsx|key|log|parquet|pem|"
    r"properties|py|sh|sql|sqlite|tf|toml|ts|tsx|tsv|txt|ya?ml)(?:$|[?#])"
)
_BARE_INTERNAL_HOST = re.compile(
    r"(?i)(?<![a-z0-9.-])(?P<host>localhost(?:\.localdomain)?|ip6-localhost|"
    r"[a-z0-9.-]+\.(?:corp|home\.arpa|internal|lan|local|localdomain|localhost|svc)|"
    r"0x[0-9a-f]+|(?:\d{1,3}\.){1,3}\d+)(?::\d{1,5})?(?![a-z0-9.-])"
)
_BRACKETED_IPV6 = re.compile(r"(?i)\[(?P<host>[0-9a-f:.%]+)\](?::\d{1,5})?")
_BARE_IPV6 = re.compile(
    r"(?i)(?<![0-9a-f:])(?P<host>[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,7})" r"(?![0-9a-f:])"
)
_PUBLIC_NEXT_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")
_INTERNAL_ROUTE_ROOTS = frozenset(
    {
        "data",
        "etc",
        "internal",
        "mnt",
        "opt",
        "private",
        "root",
        "run",
        "srv",
        "tmp",
        "usr",
        "var",
    }
)
_RESOURCE_QUERY_KEYS = frozenset(
    {"artifact", "download", "file", "home", "path", "resource", "source", "src"}
)
_REDIRECT_QUERY_KEYS = frozenset({"next", "redirect", "return"})
_MAX_NESTED_URL_DEPTH = 4


def _is_internal_web_host(hostname: str | None) -> bool:
    if not hostname:
        return True
    host = hostname.rstrip(".").casefold()
    if (
        host in {"ip6-localhost", "localhost", "localhost.localdomain"}
        or "." not in host
        or host.endswith(
            (
                ".corp",
                ".home.arpa",
                ".internal",
                ".lan",
                ".local",
                ".localdomain",
                ".localhost",
                ".svc",
            )
        )
    ):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return False
    return not address.is_global


def _internal_ip_literal(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return not address.is_global


def contains_internal_host_literal(value: str) -> bool:

    if any(
        _is_internal_web_host(match.group("host"))
        for match in _BARE_INTERNAL_HOST.finditer(value)
    ):
        return True
    if any(
        _internal_ip_literal(match.group("host"))
        for match in _BRACKETED_IPV6.finditer(value)
    ):
        return True
    return any(
        _internal_ip_literal(match.group("host"))
        for match in _BARE_IPV6.finditer(value)
    )


def _is_safe_web_route(value: str) -> bool:
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return False
    segments = [segment for segment in value.split("/") if segment]
    return (
        bool(segments)
        and all(
            segment not in {".", ".."} and _PUBLIC_NEXT_SEGMENT.fullmatch(segment)
            for segment in segments
        )
        and not any(segment.casefold() in _INTERNAL_ROUTE_ROOTS for segment in segments)
    )


def web_url_contains_sensitive_resource(
    url: str,
    *,
    contains_embedded_resource: Callable[[str], bool],
    _depth: int = 0,
) -> bool:

    if _depth >= _MAX_NESTED_URL_DEPTH:
        return True
    try:
        parsed = urlsplit(url)
    except ValueError:
        return True
    if parsed.scheme.casefold() not in {"http", "https"}:
        return True
    if _is_internal_web_host(parsed.hostname):
        return True
    if _INTERNAL_WEB_PATH.search(parsed.path) or _TECHNICAL_WEB_FILE.search(
        parsed.path
    ):
        return True
    for key, nested in parse_qsl(parsed.query, keep_blank_values=True):
        query_key = key.casefold()
        if WEB_URL.fullmatch(nested):
            if web_url_contains_sensitive_resource(
                nested,
                contains_embedded_resource=contains_embedded_resource,
                _depth=_depth + 1,
            ):
                return True
            continue
        if query_key in _REDIRECT_QUERY_KEYS and _is_safe_web_route(nested):
            continue
        if contains_embedded_resource(nested) or (
            query_key in _RESOURCE_QUERY_KEYS
            and bool(nested)
            and ("/" in nested or "\\" in nested)
        ):
            return True
    fragment = parsed.fragment.removeprefix("#")
    if WEB_URL.fullmatch(fragment):
        return web_url_contains_sensitive_resource(
            fragment,
            contains_embedded_resource=contains_embedded_resource,
            _depth=_depth + 1,
        )
    if _is_safe_web_route(fragment):
        return False
    return bool(fragment and contains_embedded_resource(fragment))


__all__ = (
    "WEB_URL",
    "contains_internal_host_literal",
    "web_url_contains_sensitive_resource",
)
