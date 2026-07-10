from __future__ import annotations

from .errors import InvalidObjectKey, UnsafePrefixDelete


def _has_control(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def validate_key(key: str) -> str:
    value = str(key or "").strip()
    if not value:
        raise InvalidObjectKey("object key is required", key=key)
    if "://" in value:
        raise InvalidObjectKey("object key must not include a URI scheme", key=key)
    if value.startswith("/"):
        raise InvalidObjectKey("object key must not start with slash", key=key)
    if "\\" in value or "//" in value or ".." in value:
        raise InvalidObjectKey("object key contains an unsafe segment", key=key)
    if _has_control(value):
        raise InvalidObjectKey("object key contains control characters", key=key)
    return value


def validate_prefix(prefix: str, *, for_delete: bool = False, trailing_slash: bool = False) -> str:
    value = str(prefix or "").strip()
    if for_delete and not value:
        raise UnsafePrefixDelete("delete_prefix refuses an empty prefix")
    if value:
        validate_key(value.rstrip("/") if value.endswith("/") else value)
    if for_delete and trailing_slash and not value.endswith("/"):
        raise UnsafePrefixDelete("delete_prefix requires a trailing slash", key=value)
    return value
