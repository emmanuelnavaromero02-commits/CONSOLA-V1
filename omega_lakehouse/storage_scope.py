from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable


# Position of the tenant_id= segment for each storage root; workspace_id= follows it.
#   raw/<cartridge>/<entity>/tenant_id=/workspace_id=/...
#   silver|gold/<cartridge>/<dataset>/tenant_id=/workspace_id=/...
#   knowledge_bits/<cartridge>/<kb>/tenant_id=/workspace_id=/...
#   uploads/<cartridge>/tenant_id=/workspace_id=/...
SCOPE_INDEX_BY_ROOT: dict[str, int] = {
    "raw": 3,
    "silver": 3,
    "gold": 3,
    "knowledge_bits": 3,
    "uploads": 2,
}
READABLE_SCOPED_ROOTS = frozenset({"raw", "silver", "gold", "uploads"})

_SEGMENT = re.compile(r"^[A-Za-z0-9_.:=-]+$")
_GLOB_SEGMENT = re.compile(r"^[A-Za-z0-9_.:=*?-]+$")
_SCOPE_VALUE = r"[A-Za-z0-9_.:-]+"
_REQUIRED_SCOPE = re.compile(
    rf"^tenant_id=(?P<tenant>{_SCOPE_VALUE})/workspace_id=(?P<workspace>{_SCOPE_VALUE})/?$"
)
_GLOB_CHARS = ("*", "?")
_READER_SCHEME_DENY = ("file:", "/", "../", "~", "http:", "https:")


class ReaderUriError(ValueError):
    """A DuckDB reader URI failed validation; ``reason`` names the failed check."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _has_control_char(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def canonical_storage_key(value: object, *, allow_glob: bool = False) -> str:
    raw = str(value or "").strip()
    if (
        not raw
        or raw.startswith("/")
        or "%" in raw
        or "\\" in raw
        or "//" in raw
        or not raw.isascii()
        or unicodedata.normalize("NFKC", raw) != raw
        or _has_control_char(raw)
    ):
        raise ValueError("storage path is not canonical")
    if raw.endswith("/"):
        raw = raw[:-1]
    if not raw:
        raise ValueError("storage path is not canonical")
    parts = raw.split("/")
    segment = _GLOB_SEGMENT if allow_glob else _SEGMENT
    if any(
        not part or part in {".", ".."} or not segment.fullmatch(part) for part in parts
    ):
        raise ValueError("storage path is not canonical")
    return "/".join(parts)


def has_exact_storage_scope(key: object, tenant: object, workspace: object) -> bool:
    tenant_value = str(tenant or "").strip()
    workspace_value = str(workspace or "").strip()
    if not tenant_value or not workspace_value:
        return False
    try:
        clean = canonical_storage_key(key, allow_glob=True)
    except ValueError:
        return False
    parts = clean.split("/")
    scope_index = SCOPE_INDEX_BY_ROOT.get(parts[0])
    if scope_index is None or len(parts) <= scope_index + 1:
        return False
    if any(char in part for part in parts[: scope_index + 2] for char in _GLOB_CHARS):
        return False
    tenant_positions = [
        index for index, part in enumerate(parts) if part.startswith("tenant_id=")
    ]
    workspace_positions = [
        index for index, part in enumerate(parts) if part.startswith("workspace_id=")
    ]
    return (
        tenant_positions == [scope_index]
        and workspace_positions == [scope_index + 1]
        and parts[scope_index] == f"tenant_id={tenant_value}"
        and parts[scope_index + 1] == f"workspace_id={workspace_value}"
    )


def scoped_storage_key(
    value: object,
    context: dict[str, Any] | None,
    *,
    bucket: str | None = None,
    allow_glob: bool = False,
) -> str:
    key = canonical_storage_key(value, allow_glob=allow_glob)
    parts = key.split("/")
    if parts[0] not in READABLE_SCOPED_ROOTS:
        raise ValueError("storage root is not scoped")
    ctx = context if isinstance(context, dict) else {}
    tenant = str(ctx.get("tenant_id") or "").strip()
    workspace = str(ctx.get("workspace_id") or "").strip()
    if not tenant or not workspace:
        raise PermissionError("tenant/workspace storage scope required")
    if not has_exact_storage_scope(key, tenant, workspace):
        raise PermissionError("storage scope mismatch")
    allowed = {
        str(item).strip()
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    if parts[1] not in allowed and "*" not in allowed:
        raise PermissionError("storage cartridge mismatch")
    buckets = {
        str(item).strip()
        for item in (ctx.get("allowed_buckets") or [])
        if str(item).strip()
    }
    if bucket and buckets and bucket not in buckets:
        raise PermissionError("storage bucket mismatch")
    return key


def scoped_storage_allowed(
    value: object,
    context: dict[str, Any] | None,
    *,
    bucket: str | None = None,
) -> bool:
    try:
        scoped_storage_key(value, context, bucket=bucket)
        return True
    except (PermissionError, ValueError):
        return False


def parse_required_scope(marker: object) -> tuple[str, str]:
    match = _REQUIRED_SCOPE.fullmatch(str(marker or ""))
    if not match:
        raise ValueError("required scope marker is malformed")
    return match.group("tenant"), match.group("workspace")


def _reader_prefixes(prefixes: str | Iterable[str]) -> tuple[str, ...]:
    values = (prefixes,) if isinstance(prefixes, str) else tuple(prefixes)
    return tuple(str(prefix).rstrip("/") + "/" for prefix in values if str(prefix))


def require_scoped_reader_uri(
    uri: object,
    *,
    prefixes: str | Iterable[str],
    tenant: str | None,
    workspace: str | None,
) -> str:
    """Validate the exact reader literal; nothing is decoded or normalized.

    ``tenant``/``workspace`` both ``None`` checks shape and prefix only (templates
    validated before a scope is known); any other value demands the exact scope.
    """
    value = uri if isinstance(uri, str) else ""
    if not value or value != value.strip():
        raise ReaderUriError("canonical")
    if "?" in value or "#" in value:
        raise ReaderUriError("query")
    if value.lower().startswith(_READER_SCHEME_DENY):
        raise ReaderUriError("scheme")
    if not value.startswith("s3://"):
        raise ReaderUriError("prefix")
    rest = value[len("s3://") :]
    if ".." in rest.split("/"):
        raise ReaderUriError("traversal")
    try:
        canonical = canonical_storage_key(rest, allow_glob=True)
    except ValueError:
        raise ReaderUriError("canonical") from None
    if canonical != rest or "/" not in canonical:
        raise ReaderUriError("canonical")
    if not any(value.startswith(prefix) for prefix in _reader_prefixes(prefixes)):
        raise ReaderUriError("prefix")
    if tenant is None and workspace is None:
        return value
    key = canonical.split("/", 1)[1]
    if not has_exact_storage_scope(key, tenant, workspace):
        raise ReaderUriError("scope")
    return value


__all__ = [
    "READABLE_SCOPED_ROOTS",
    "ReaderUriError",
    "SCOPE_INDEX_BY_ROOT",
    "canonical_storage_key",
    "has_exact_storage_scope",
    "parse_required_scope",
    "require_scoped_reader_uri",
    "scoped_storage_allowed",
    "scoped_storage_key",
]
