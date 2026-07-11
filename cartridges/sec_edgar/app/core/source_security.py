from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ALLOWED_HOST = "data.sec.gov"
BASE_URL = "https://data.sec.gov"


class SourceSecurityError(ValueError):
    pass


def validate_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise SourceSecurityError("SEC EDGAR URL must use HTTPS")
    if parsed.hostname != ALLOWED_HOST:
        raise SourceSecurityError("SEC EDGAR host is not allowlisted")
    if not parsed.path.startswith(("/submissions/", "/api/xbrl/companyfacts/")):
        raise SourceSecurityError("SEC EDGAR path is not allowlisted")
    return url


def sanitize_source_url(url: str) -> str:
    parsed = urlsplit(validate_url(url))
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"token", "api_key", "apikey"}
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))[:512]
