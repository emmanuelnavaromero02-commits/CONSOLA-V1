from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ALLOWED_HOST = "www.inegi.org.mx"
BASE_URL = "https://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml"


class SourceSecurityError(ValueError):
    pass


def validate_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise SourceSecurityError("INEGI URL must use HTTPS")
    if parsed.hostname != ALLOWED_HOST:
        raise SourceSecurityError("INEGI host is not allowlisted")
    return url


def sanitize_source_url(url: str) -> str:
    parsed = urlsplit(validate_url(url))
    path_parts = [part if part != "__TOKEN__" else "redacted" for part in parsed.path.split("/")]
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "token"
    ]
    sanitized = urlunsplit((parsed.scheme, parsed.netloc, "/".join(path_parts), urlencode(query), ""))
    return sanitized[:512]
