"""Defensive guard for SAP HCM ad-hoc Knowledge Bit SQL."""
from __future__ import annotations

import re
from urllib.parse import unquote


_FORBIDDEN_RE = re.compile(
    r"\b(ATTACH|INSTALL|LOAD|PRAGMA|COPY|CREATE|DROP|DELETE|UPDATE|INSERT|"
    r"TRUNCATE|ALTER|SET|EXPORT|IMPORT_DATABASE|IMPORT|CALL)\b",
    re.IGNORECASE,
)
_READ_FN_RE = re.compile(
    r"\b(read_parquet|read_csv)\s*\(\s*(['\"])(?P<path>[^'\"]+)\2",
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(r"(--|/\*)")
_QUOTED_RE = re.compile(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")")


def _mask_quoted(sql: str) -> str:
    return _QUOTED_RE.sub("''", sql)


def validate_kb_sql(sql: str, allowed_bucket_prefix: str) -> tuple[bool, str | None]:
    """Validate ad-hoc DuckDB SQL before it reaches query_kb.

    We allow read-only SELECT/WITH statements and only permit file readers
    against this cartridge's MinIO raw prefix. The guard is deliberately
    conservative because query_kb is reachable by LLM tool calls.
    """
    if not isinstance(sql, str) or not sql.strip():
        return False, "empty SQL"

    stripped = sql.strip()
    if not re.match(r"^(SELECT|WITH)\b", stripped, re.IGNORECASE):
        return False, "Only SELECT/WITH queries allowed in query_kb"

    masked = _mask_quoted(stripped)

    if "\x00" in stripped:
        return False, "NUL byte is not allowed"
    if ";" in masked:
        return False, "Multiple statements are not allowed"
    if _COMMENT_RE.search(masked):
        return False, "SQL comments are not allowed"

    match = _FORBIDDEN_RE.search(masked)
    if match:
        return False, f"Forbidden DuckDB keyword in query_kb: {match.group(1).upper()}"

    normalized_prefix = allowed_bucket_prefix.rstrip("/") + "/"
    for fn in _READ_FN_RE.finditer(stripped):
        name = fn.group(1).lower()
        raw_path = unquote(fn.group("path")).replace("\\", "/")
        if raw_path.lower().startswith(("file:", "/", "../", "~")):
            return False, f"{name} may only read from the cartridge S3 prefix"
        if not raw_path.startswith(normalized_prefix):
            return False, f"{name} path must start with {normalized_prefix}"

    return True, None
