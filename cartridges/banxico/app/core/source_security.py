from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ALLOWED_HOST = "www.banxico.org.mx"
BASE_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1"


class SourceSecurityError(ValueError):
    pass


def validate_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise SourceSecurityError("Banxico URL must use HTTPS")
    if parsed.hostname != ALLOWED_HOST:
        raise SourceSecurityError("Banxico host is not allowlisted")
    return url


def sanitize_source_url(url: str) -> str:
    parsed = urlsplit(validate_url(url))
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "token"
    ]
    sanitized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    return sanitized[:512]
