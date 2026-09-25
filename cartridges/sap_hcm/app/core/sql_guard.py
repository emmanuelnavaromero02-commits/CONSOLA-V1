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
_PATH_LIKE_RE = re.compile(
    r"^(?:[a-z][a-z0-9+.-]*://|/|~|\.{1,2}[/\\]|[a-z]:[/\\])|\.\.[/\\]|[/\\]\.\."
    r"|\.(?:csv|tsv|parquet|json|jsonl|ndjson|txt|gz|zst|xlsx?|db|duckdb|sqlite|arrow|feather|avro)$",
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


_TABLE_REF_TYPES = frozenset({"BASE_TABLE", "TABLE_FUNCTION", "SUBQUERY", "JOIN", "EMPTY", "EXPRESSION_LIST", "PIVOT"})
_READER_TABLE_FUNCTIONS = frozenset({"read_parquet", "read_csv"})
_SAFE_TABLE_FUNCTIONS = frozenset({"range", "generate_series", "unnest"})
_STATIC_CONTAINERS = frozenset({"struct_pack", "list_value", "row"})
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BLOCKED_FUNCTION_PREFIXES = (
    "read_", "parquet_", "duckdb_", "pragma_", "iceberg_", "delta_", "sqlite_", "postgres_", "mysql_", "st_read",
)
_BLOCKED_FUNCTIONS = frozenset(
    {
        "glob", "sniff_csv", "query", "query_table", "getenv", "current_setting", "getvariable",
        "which_secret", "json_serialize_sql", "json_serialize_plan", "json_deserialize_sql",
        "json_execute_serialized_sql", "current_query", "csv_scan", "csv_auto", "json_scan", "load", "install",
    }
)
_CATALOG_CACHE: dict[str, frozenset[str]] = {}


def _catalog() -> dict[str, frozenset[str]]:
    if not _CATALOG_CACHE:
        import duckdb

        conn = duckdb.connect()
        try:
            relations = conn.execute(
                "SELECT lower(view_name) FROM duckdb_views() UNION SELECT lower(table_name) FROM duckdb_tables() "
                "UNION SELECT lower(schema_name) FROM duckdb_schemas()"
            ).fetchall()
            functions = conn.execute(
                "SELECT lower(function_name) FROM duckdb_functions() "
                "WHERE function_type IN ('table', 'pragma', 'table_macro') "
                "EXCEPT SELECT lower(function_name) FROM duckdb_functions() "
                "WHERE function_type IN ('scalar', 'aggregate', 'macro')"
            ).fetchall()
        finally:
            conn.close()
        _CATALOG_CACHE["relations"] = frozenset(row[0] for row in relations) | {"information_schema", "pg_catalog"}
        _CATALOG_CACHE["table_functions"] = frozenset(row[0] for row in functions)
    return _CATALOG_CACHE


def _parse_tree(sql: str) -> dict | None:
    import json

    import duckdb

    conn = duckdb.connect()
    try:
        raw = conn.execute("SELECT json_serialize_sql(?::VARCHAR)", [sql]).fetchone()[0]
    finally:
        conn.close()
    tree = json.loads(raw)
    if not isinstance(tree, dict) or tree.get("error") or len(tree.get("statements") or []) != 1:
        return None
    return tree


def _walk(root: object):
    stack = [root]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _usable_name(name: str) -> bool:
    return bool(_IDENTIFIER_RE.fullmatch(name)) and name.lower() not in _catalog()["relations"]


def _cte_names(tree: object) -> set[str] | None:
    names: set[str] = set()
    for node in _walk(tree):
        cte_map = node.get("cte_map")
        if isinstance(cte_map, dict):
            for entry in cte_map.get("map") or []:
                key = str((entry or {}).get("key") or "")
                if not _usable_name(key):
                    return None
                names.add(key.lower())
        if node.get("type") == "RECURSIVE_CTE_NODE":
            key = str(node.get("cte_name") or "")
            if not _usable_name(key):
                return None
            names.add(key.lower())
    return names


def _constant_text(node: object) -> str | None:
    if isinstance(node, dict) and node.get("class") == "COLUMN_REF":
        names = node.get("column_names") or []
        return str(names[0]) if len(names) == 1 else None
    if not isinstance(node, dict) or node.get("class") != "CONSTANT":
        return None
    value = node.get("value") or {}
    if value.get("is_null") or (value.get("type") or {}).get("id") != "VARCHAR":
        return None
    return str(value.get("value"))


def _is_static(node: object) -> bool:
    if not isinstance(node, dict):
        return False
    kind = node.get("class")
    if kind == "CONSTANT":
        return True
    if kind == "CAST":
        return _is_static(node.get("child"))
    if kind == "FUNCTION" and node.get("function_name") in _STATIC_CONTAINERS:
        return all(_is_static(child) or _constant_text(child) is not None for child in node.get("children") or [])
    return False


def _reader_paths(function: dict) -> list[str] | None:
    children = function.get("children") or []
    if not children:
        return None
    first = children[0]
    text = _constant_text(first)
    if text is None:
        return None
    for option in children[1:]:
        if not isinstance(option, dict):
            return None
        if option.get("class") == "COMPARISON":
            if (option.get("left") or {}).get("class") != "COLUMN_REF" or not _is_static(option.get("right")):
                return None
        elif not option.get("alias") or not _is_static(option):
            return None
    return [text]


def _path_violation(path: str, prefixes: tuple[str, ...], required_scope: str | None) -> str | None:
    raw_path = _canonical_s3_path(path)
    if "?" in raw_path or "#" in raw_path:
        return "DuckDB reader paths cannot carry query strings or fragments"
    if raw_path.lower().startswith(("file:", "/", "../", "~", "http:", "https:")):
        return "DuckDB readers may only read from the cartridge S3 prefixes"
    if "/../" in raw_path or raw_path.endswith("/.."):
        return "DuckDB reader path traversal is not allowed"
    if not any(raw_path.startswith(prefix) for prefix in prefixes):
        return f"DuckDB reader path must start with one of {prefixes}"
    if not _has_exact_scope(raw_path, required_scope):
        return "DuckDB reader path must stay inside the active tenant/workspace scope"
    return None


def _node_violation(
    node: dict,
    tables: set[str],
    prefixes: tuple[str, ...],
    required_scope: str | None,
) -> str | None:
    kind = node.get("type")
    if isinstance(kind, str) and "class" not in node and "query_location" in node and "alias" in node:
        if kind not in _TABLE_REF_TYPES:
            return f"Unsupported relation in query_kb: {kind.lower()}"
        if kind == "BASE_TABLE":
            name = str(node.get("table_name") or "")
            if (
                node.get("schema_name")
                or node.get("catalog_name")
                or not _IDENTIFIER_RE.fullmatch(name)
                or name.lower() not in tables
            ):
                return "Only CTEs and read_parquet/read_csv sources are allowed in query_kb"
        if kind == "TABLE_FUNCTION":
            function = node.get("function") or {}
            name = str(function.get("function_name") or "").lower()
            if function.get("schema") not in ("", "main", None):
                return "Schema-qualified table functions are not allowed in query_kb"
            if name in _READER_TABLE_FUNCTIONS:
                paths = _reader_paths(function)
                if paths is None:
                    return "DuckDB readers must use direct string literal paths and constant options"
                for path in paths:
                    found = _path_violation(path, prefixes, required_scope)
                    if found:
                        return found
            elif name not in _SAFE_TABLE_FUNCTIONS:
                return f"Forbidden DuckDB table function in query_kb: {name}"
    if node.get("class") in ("FUNCTION", "WINDOW", "AGGREGATE"):
        name = str(node.get("function_name") or "").lower()
        if (
            name in _BLOCKED_FUNCTIONS
            or name.startswith(_BLOCKED_FUNCTION_PREFIXES)
            or (name in _catalog()["table_functions"] and name not in _SAFE_TABLE_FUNCTIONS)
        ):
            if not node.get("_reader_call"):
                return f"Forbidden DuckDB function in query_kb: {name}"
    return None


def _ast_violation(
    sql: str,
    prefixes: tuple[str, ...],
    required_scope: str | None,
    allowed_tables: frozenset[str] = frozenset(),
) -> str | None:
    try:
        tree = _parse_tree(sql)
    except Exception:  # noqa: BLE001
        return "SQL could not be parsed"
    if tree is None:
        return "Only a single SELECT/WITH statement is allowed in query_kb"
    ctes = _cte_names(tree)
    if ctes is None:
        return "CTE names must be plain identifiers in query_kb"
    tables = ctes | {name.lower() for name in allowed_tables}
    for node in _walk(tree["statements"]):
        if node.get("type") == "TABLE_FUNCTION" and "class" not in node:
            function = node.get("function")
            if isinstance(function, dict) and str(function.get("function_name") or "").lower() in _READER_TABLE_FUNCTIONS:
                function["_reader_call"] = True
        found = _node_violation(node, tables, prefixes, required_scope)
        if found:
            return found
    return None


def validate_kb_sql(
    sql: str,
    allowed_bucket_prefix: str | tuple[str, ...] | list[str],
    *,
    required_scope: str | None = None,
    require_limit: bool = False,
    allowed_tables: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
) -> tuple[bool, str | None]:
    if not isinstance(sql, str) or not sql.strip():
        return False, "empty SQL"

    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
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
        raw_path = _canonical_s3_path(fn.group("path"))
        if "?" in raw_path or "#" in raw_path:
            return False, f"{name} paths cannot carry query strings or fragments"
        if raw_path.lower().startswith(("file:", "/", "../", "~", "http:", "https:")):
            return False, f"{name} may only read from the cartridge S3 prefixes"
        if "/../" in raw_path or raw_path.endswith("/.."):
            return False, f"{name} path traversal is not allowed"
        if not any(raw_path.startswith(prefix) for prefix in normalized_prefixes):
            return False, f"{name} path must start with one of {normalized_prefixes}"
        if not _has_exact_scope(raw_path, required_scope):
            return False, f"{name} path must stay inside the active tenant/workspace scope"

    if any(not _usable_name(str(name)) for name in allowed_tables):
        return False, "Runtime table names must be plain identifiers"
    violation = _ast_violation(stripped, normalized_prefixes, required_scope, frozenset(allowed_tables))
    if violation:
        return False, violation

    return True, None
