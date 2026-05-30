"""Defensive guard for SAP SuccessFactors ad-hoc Knowledge Bit SQL."""
from __future__ import annotations

import re
from urllib.parse import unquote


_MAX_LIMIT = 5000
_FORBIDDEN_RE = re.compile(
    r"\b(ATTACH|INSTALL|LOAD|PRAGMA|COPY|CREATE|DROP|DELETE|UPDATE|INSERT|"
    r"TRUNCATE|ALTER|SET|EXPORT|IMPORT_DATABASE|IMPORT|CALL)\b",
    re.IGNORECASE,
)
_READER_FN_RE = re.compile(
    r"\b(read_[a-z0-9_]+|parquet_scan|csv_auto|csv_scan)\s*\(",
    re.IGNORECASE,
)
_READ_FN_RE = re.compile(
    r"\b(read_parquet|read_csv)\s*\(\s*(['\"])(?P<path>[^'\"]+)\2",
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(r"(--|/\*)")
_QUOTED_RE = re.compile(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")")
_LIMIT_RE = re.compile(r"\bLIMIT\s+(?P<value>[^\s,)]+)", re.IGNORECASE)
_TAUTOLOGY_RE = re.compile(
    r"\b(?:OR|AND)\s+(?P<left>\d+)\s*=\s*(?P=left)\b",
    re.IGNORECASE,
)


def _mask_quoted(sql: str) -> str:
    return _QUOTED_RE.sub("''", sql)


def _prefixes(allowed_bucket_prefix: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if isinstance(allowed_bucket_prefix, str):
        allowed = (allowed_bucket_prefix,)
    else:
        allowed = tuple(allowed_bucket_prefix)
    return tuple(p.rstrip("/") + "/" for p in allowed)


def has_limit_clause(sql: str) -> bool:
    return bool(re.search(r"\bLIMIT\b", _mask_quoted(sql), re.IGNORECASE))


def _validate_limit_clause(masked_sql: str) -> tuple[bool, str | None]:
    for match in _LIMIT_RE.finditer(masked_sql):
        raw_value = match.group("value")
        if not raw_value.isdigit():
            return False, "LIMIT must be a positive integer"
        value = int(raw_value)
        if value < 1:
            return False, "LIMIT must be a positive integer"
        if value > _MAX_LIMIT:
            return False, f"LIMIT exceeds max {_MAX_LIMIT}"
    return True, None


def validate_kb_sql(sql: str, allowed_bucket_prefix: str | tuple[str, ...] | list[str]) -> tuple[bool, str | None]:
    """Validate ad-hoc DuckDB SQL before it reaches query_kb."""
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
    if _TAUTOLOGY_RE.search(masked):
        return False, "SQL tautology predicates are not allowed"

    limit_ok, limit_err = _validate_limit_clause(masked)
    if not limit_ok:
        return False, limit_err

    match = _FORBIDDEN_RE.search(masked)
    if match:
        return False, f"Forbidden DuckDB keyword in query_kb: {match.group(1).upper()}"

    read_calls = list(_READER_FN_RE.finditer(stripped))
    allowed_read_names = {"read_parquet", "read_csv"}
    for call in read_calls:
        fn = call.group(1).lower()
        if fn not in allowed_read_names:
            return False, f"Forbidden DuckDB reader function in query_kb: {fn}"

    direct_literal_calls = list(_READ_FN_RE.finditer(stripped))
    if len(direct_literal_calls) != len(read_calls):
        return False, "DuckDB readers must use a direct string literal path"

    normalized_prefixes = _prefixes(allowed_bucket_prefix)
    for fn in _READ_FN_RE.finditer(stripped):
        name = fn.group(1).lower()
        raw_path = unquote(fn.group("path")).replace("\\", "/")
        if raw_path.lower().startswith(("file:", "/", "../", "~", "http:", "https:")):
            return False, f"{name} may only read from the cartridge S3 prefixes"
        if "/../" in raw_path or raw_path.endswith("/.."):
            return False, f"{name} path traversal is not allowed"
        if not any(raw_path.startswith(prefix) for prefix in normalized_prefixes):
            return False, f"{name} path must start with one of {normalized_prefixes}"

    return True, None
