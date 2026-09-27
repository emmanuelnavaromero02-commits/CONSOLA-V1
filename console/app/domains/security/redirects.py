from __future__ import annotations

from urllib.parse import quote


_MAX_NEXT_LENGTH = 2048


def safe_login_next(path: str) -> str | None:
    """Return `path` quoted for `/login?next=`, or None when it is not a plain local path."""
    if not isinstance(path, str) or not path.startswith("/") or len(path) > _MAX_NEXT_LENGTH:
        return None
    if "//" in path or "\\" in path:
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in path):
        return None
    return quote(path, safe="/")
