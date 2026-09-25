from __future__ import annotations

import re
from urllib.parse import unquote


_MAX_LIMIT = 5000
_FORBIDDEN_RE = re.compile(
    r"\b(ATTACH|INSTALL|LOAD|PRAGMA|COPY|CREATE|DROP|DELETE|UPDATE|INSERT|"
    r"TRUNCATE|ALTER|SET|EXPORT|IMPORT_DATABASE|IMPORT|CALL)\b",
    re.IGNORECASE,
)
_METADATA_EXFIL_RE = re.compile(
    r"\b(current_setting|duckdb_settings|duckdb_secrets|duckdb_extensions|"
    r"duckdb_functions|duckdb_tables|duckdb_columns|duckdb_databases|"
    r"pragma_[a-z0-9_]+|database_list|information_schema)\b",
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
_FORBIDDEN_FN_RE = re.compile(
    r"\b(glob|sniff_csv|parquet_[a-z0-9_]+|query_table|query|read_text|read_blob|getenv|"
    r"iceberg_[a-z0-9_]+|delta_scan|sqlite_scan|postgres_scan|mysql_scan)\s*\(",
    re.IGNORECASE,
)
_PATH_LIKE_RE = re.compile(
    r"^(?:[a-z][a-z0-9+.-]*://|/|~|\.{1,2}[/\\]|[a-z]:[/\\])|\.\.[/\\]|[/\\]\.\."
    r"|\.(?:csv|tsv|parquet|json|jsonl|ndjson|txt|gz|zst|xlsx?|db|duckdb|sqlite|arrow|feather|avro)$",
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(r"(--|/\*)")
_QUOTED_RE = re.compile(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")")
_QUOTED_CALL_RE = re.compile(r"''\s*\(")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_RELATION_KEYWORDS = frozenset(
    {"FROM", "JOIN", "TABLE", "PIVOT", "UNPIVOT", "PIVOT_WIDER", "PIVOT_LONGER", "SUMMARIZE", "DESCRIBE", "SHOW"}
)
_EXPRESSION_KEYWORDS = frozenset(
    {
        "SELECT", "WHERE", "GROUP", "HAVING", "QUALIFY", "WINDOW", "ORDER", "BY", "LIMIT", "OFFSET",
        "UNION", "INTERSECT", "EXCEPT", "VALUES", "ON", "USING", "AS", "WITH", "CASE", "WHEN", "THEN",
        "ELSE", "END", "IN", "AND", "OR", "NOT", "LIKE", "ILIKE", "GLOB", "SIMILAR", "BETWEEN", "IS",
        "FILTER", "OVER", "PARTITION", "DISTINCT", "ALL", "ANY", "SOME", "EXISTS", "ESCAPE", "COLLATE",
    }
)
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


def _canonical_s3_path(path: str) -> str:
    raw_path = path
    for _ in range(3):
        decoded = unquote(raw_path).replace("\\", "/")
        if decoded == raw_path:
            break
        raw_path = decoded
    if raw_path.lower().startswith(("file:", "/", "../", "~", "http:", "https:")):
        return raw_path
    if not raw_path.startswith("s3://"):
        return raw_path
    scheme, rest = raw_path[:5], raw_path[5:]
    parts = [part for part in rest.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        return raw_path
    return scheme + "/".join(parts)


def _has_exact_scope(path: str, required_scope: str | None) -> bool:
    if not required_scope:
        return True
    if not path.startswith("s3://"):
        return False
    parts = [part for part in path[5:].split("/") if part]
    scope_parts = [part for part in required_scope.strip("/").split("/") if part]
    if not scope_parts:
        return False
    return any(parts[idx : idx + len(scope_parts)] == scope_parts for idx in range(len(parts)))


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


def _mask_keep_length(sql: str) -> str:
    return _QUOTED_RE.sub(lambda m: m.group(0)[0] + " " * (len(m.group(0)) - 2) + m.group(0)[-1], sql)


def _relation_position(masked: str, start: int) -> bool:
    depth = 0
    i = start - 1
    while i >= 0:
        ch = masked[i]
        if ch in ")]":
            depth += 1
            i -= 1
            continue
        if ch in "([":
            if depth:
                depth -= 1
                i -= 1
                continue
            before = _WORD_RE.findall(masked[:i].rstrip()[-64:])
            return bool(before) and before[-1].upper() in _RELATION_KEYWORDS
        if ch.isalnum() or ch in "_$":
            end = i + 1
            while i >= 0 and (masked[i].isalnum() or masked[i] in "_$"):
                i -= 1
            if depth == 0:
                word = masked[i + 1 : end].upper()
                if word in _RELATION_KEYWORDS:
                    return True
                if word in _EXPRESSION_KEYWORDS:
                    return False
            continue
        i -= 1
    return False


def _stray_path_literal(sql: str, reader_spans: list[tuple[int, int]]) -> str | None:
    masked = _mask_keep_length(sql)
    for literal in _QUOTED_RE.finditer(sql):
        start, end = literal.span()
        if any(start >= s and end <= e for s, e in reader_spans):
            continue
        if not _relation_position(masked, start):
            continue
        body = literal.group(0)[1:-1]
        if _PATH_LIKE_RE.search(unquote(body).strip()):
            return body
    return None


def validate_kb_sql(
    sql: str,
    allowed_bucket_prefix: str | tuple[str, ...] | list[str],
    *,
    required_scope: str | None = None,
    require_limit: bool = False,
) -> tuple[bool, str | None]:
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

    if _METADATA_EXFIL_RE.search(stripped):
        return False, "DuckDB metadata/settings access (PRAGMA/current_setting/duckdb_settings) is not allowed in query_kb"

    if require_limit and not has_limit_clause(stripped):
        return False, f"Interactive queries must include a LIMIT clause (max {_MAX_LIMIT})"

    limit_ok, limit_err = _validate_limit_clause(masked)
    if not limit_ok:
        return False, limit_err

    match = _FORBIDDEN_RE.search(masked)
    if match:
        return False, f"Forbidden DuckDB keyword in query_kb: {match.group(1).upper()}"

    if _QUOTED_CALL_RE.search(masked):
        return False, "Quoted function names are not allowed in query_kb"

    forbidden_fn = _FORBIDDEN_FN_RE.search(stripped)
    if forbidden_fn:
        return False, f"Forbidden DuckDB function in query_kb: {forbidden_fn.group(1).lower()}"

    read_calls = list(_READER_FN_RE.finditer(stripped))
    allowed_read_names = {"read_parquet", "read_csv"}
    for call in read_calls:
        fn = call.group(1).lower()
        if fn not in allowed_read_names:
            return False, f"Forbidden DuckDB reader function in query_kb: {fn}"

    direct_literal_calls = list(_READ_FN_RE.finditer(stripped))
    if len(direct_literal_calls) != len(read_calls):
        return False, "DuckDB readers must use a direct string literal path"

    reader_spans = [call.span() for call in _READ_FN_RE.finditer(stripped)]
    if _stray_path_literal(stripped, reader_spans) is not None:
        return False, "File paths may only appear as the first argument of read_parquet/read_csv"

    normalized_prefixes = _prefixes(allowed_bucket_prefix)
    for fn in _READ_FN_RE.finditer(stripped):
        name = fn.group(1).lower()
        raw_path = _canonical_s3_path(fn.group("path"))
        if raw_path.lower().startswith(("file:", "/", "../", "~", "http:", "https:")):
            return False, f"{name} may only read from the cartridge S3 prefixes"
        if "/../" in raw_path or raw_path.endswith("/.."):
            return False, f"{name} path traversal is not allowed"
        if not any(raw_path.startswith(prefix) for prefix in normalized_prefixes):
            return False, f"{name} path must start with one of {normalized_prefixes}"
        if not _has_exact_scope(raw_path, required_scope):
            return False, f"{name} path must stay inside the active tenant/workspace scope"

    return True, None
