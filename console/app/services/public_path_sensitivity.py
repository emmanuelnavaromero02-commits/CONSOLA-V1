"""Bounded path and resource-locator checks for public response copy."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, unquote_plus

from app.services.public_web_resource_sensitivity import (
    WEB_URL,
    contains_internal_host_literal,
    web_url_contains_sensitive_resource,
)


_WINDOWS_PATH = re.compile(
    r"(?<![a-z0-9])[a-z]:[\\/]+[^\s\"'<>|]+",
    re.IGNORECASE,
)
_WINDOWS_DRIVE_RELATIVE_PATH = re.compile(
    r"(?<![a-z0-9])[a-z]:[^\\/\s\"'<>|:]+[\\/]+[^\s\"'<>|]+",
    re.IGNORECASE,
)
_WINDOWS_DRIVE_REFERENCE = re.compile(
    r"(?<![a-z0-9])[a-z]:(?!//)[^\s\"'<>|:/\\]+",
    re.IGNORECASE,
)
_UNC_PATH = re.compile(
    r"(?<![a-z0-9\\])\\{2,}[^\\/\s\"'<>|]+[\\/]+[^\\/\s\"'<>|]+",
    re.IGNORECASE,
)
_FORWARD_UNC_PATH = re.compile(
    r"(?<![a-z0-9:])//[^/\s\"'<>|]+/[^/\s\"'<>|]+",
    re.IGNORECASE,
)
_ENVIRONMENT_PATH = re.compile(
    r"(?i)(?<![a-z0-9_$%])"
    r"(?:\$(?:env:)?[a-z_][a-z0-9_]*|\$\{[a-z_][a-z0-9_]*\}|"
    r"%[a-z_][a-z0-9_]*%|~[a-z0-9_-]*)[\\/]+[^\s\"'<>|]+"
)
_POSIX_PATH = re.compile(
    r"(?<![a-z0-9/])/(?!/)(?:[^/\s\"'<>]+/)*[^/\s\"'<>]+",
    re.IGNORECASE,
)
_INTERNAL_POSIX_PATH = re.compile(
    r"(?i)(?<![a-z0-9])/(?:etc|home|opt|private|root|run|srv|tmp|users|usr|var)"
    r"(?:/|$)"
)
_FILE_REFERENCE = re.compile(
    r"(?i)(?:^|[^a-z0-9])[a-z0-9_.-]+\."
    r"(?:cfg|conf|csv|db|duckdb|env|hcl|ini|js|json|jsx|key|log|parquet|pem|"
    r"properties|py|sh|sql|sqlite|tf|toml|ts|tsx|tsv|txt|ya?ml)"
    r"(?:$|[^a-z0-9])"
)
_TECHNICAL_BASENAME = re.compile(r"(?i)(?:^|[^a-z0-9])dockerfile(?:$|[^a-z0-9])")
_NON_WEB_URI = re.compile(
    r"(?i)(?<![a-z0-9+.-])(?!https?://)" r"[a-z][a-z0-9+.-]*(?::[a-z][a-z0-9+.-]*)?://"
)
_TECHNICAL_SCHEME_REFERENCE = re.compile(
    r"(?i)(?<![a-z0-9+.-])(?:jdbc:[a-z][a-z0-9+.-]*:|postgresql?:|redis:)"
    r"(?!//)[^\s\"'<>]+"
)
_LABELED_RELATIVE_PATH = re.compile(
    r"(?i)\b(?:directory|file|file[_ -]?path|filepath|path|ruta)\s*[:=]\s*"
    r"[^\s\"'<>|]+[\\/][^\s\"'<>|]+"
)
_INTERNAL_RELATIVE_PATH = re.compile(
    r"(?i)(?:^|[\s\"'(<])(?:app[\\/]+services|config|console[\\/]+(?:app|tests)|"
    r"internal|migrations[\\/]+versions|private|scripts|sql[\\/]+(?:bootstrap|seeds?)|"
    r"src|tests?)"
    r"[\\/]+[^\s\"'<>|]+"
)
_RELATIVE_TRAVERSAL_PATH = re.compile(r"(?i)(?:^|[\s\"'(<])\.{1,2}[\\/]+[^\s\"'<>|]+")
_RESOURCE_IDENTIFIER = re.compile(
    r"(?i)(?<![a-z0-9])(?:arn:aws(?:-[a-z0-9-]+)?:|urn:[a-z0-9][a-z0-9.-]*:)"
    r"[^\s\"'<>]+"
)
_ESCAPED_CODEPOINT = re.compile(
    r"\\(?:u(?P<unicode>[0-9a-f]{4})|x(?P<byte>[0-9a-f]{2}))",
    re.IGNORECASE,
)


def _decode_escaped_codepoints(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        encoded = match.group("unicode") or match.group("byte")
        return chr(int(encoded, 16))

    return _ESCAPED_CODEPOINT.sub(replace, value)


def public_encoding_variants(value: str) -> tuple[str, ...]:
    """Decode percent and escaped codepoints for at most four bounded passes."""

    variants: list[str] = []
    frontier = [value]
    for _ in range(4):
        next_frontier: list[str] = []
        for candidate in frontier:
            if candidate not in variants:
                variants.append(candidate)
            for decoded in (
                unquote(candidate),
                unquote_plus(candidate),
                _decode_escaped_codepoints(candidate),
                unicodedata.normalize("NFKC", candidate),
            ):
                if decoded not in variants and decoded not in next_frontier:
                    variants.append(decoded)
                    next_frontier.append(decoded)
        if not next_frontier:
            break
        frontier = next_frontier
    return tuple(variants)


def _contains_embedded_resource(value: str) -> bool:
    return bool(
        _POSIX_PATH.search(value)
        or _FORWARD_UNC_PATH.search(value)
        or _WINDOWS_PATH.search(value)
        or _WINDOWS_DRIVE_RELATIVE_PATH.search(value)
        or _UNC_PATH.search(value)
        or _ENVIRONMENT_PATH.search(value)
        or _NON_WEB_URI.search(value)
        or _TECHNICAL_SCHEME_REFERENCE.search(value)
        or _RESOURCE_IDENTIFIER.search(value)
        or _FILE_REFERENCE.search(value)
        or _INTERNAL_RELATIVE_PATH.search(value)
        or contains_internal_host_literal(value)
    )


def contains_public_path_or_resource(value: str) -> bool:
    """Reject filesystem paths and non-web resource locators, including encodings."""

    for candidate in public_encoding_variants(value):
        if any(
            web_url_contains_sensitive_resource(
                match.group(0),
                contains_embedded_resource=_contains_embedded_resource,
            )
            for match in WEB_URL.finditer(candidate)
        ):
            return True
        if (
            _NON_WEB_URI.search(candidate)
            or _TECHNICAL_SCHEME_REFERENCE.search(candidate)
            or _RESOURCE_IDENTIFIER.search(candidate)
            or contains_internal_host_literal(candidate)
            or _WINDOWS_PATH.search(candidate)
            or _WINDOWS_DRIVE_RELATIVE_PATH.search(candidate)
            or _WINDOWS_DRIVE_REFERENCE.search(candidate)
            or _UNC_PATH.search(candidate)
            or _ENVIRONMENT_PATH.search(candidate)
            or _LABELED_RELATIVE_PATH.search(candidate)
            or _INTERNAL_RELATIVE_PATH.search(candidate)
            or _RELATIVE_TRAVERSAL_PATH.search(candidate)
        ):
            return True
        without_web_urls = WEB_URL.sub("", candidate)
        if (
            _FORWARD_UNC_PATH.search(without_web_urls)
            or _POSIX_PATH.search(without_web_urls)
            or _FILE_REFERENCE.search(without_web_urls)
            or _TECHNICAL_BASENAME.search(without_web_urls)
        ):
            return True
    return False


__all__ = ("contains_public_path_or_resource", "public_encoding_variants")
