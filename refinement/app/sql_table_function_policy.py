"""Closed DuckDB table-function policy for externally supplied SELECT SQL."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType, Tokenizer

try:
    from app.sql_scope_policy import resolved_cte_table_ids
except ModuleNotFoundError:
    from refinement.app.sql_scope_policy import resolved_cte_table_ids


POLICY_ERROR = "SQL blocked by safety policy"
_STORAGE_FUNCTIONS = {"read_parquet"}
_SAFE_GENERATORS = {"generate_series", "unnest"}
_SENSITIVE_SCALAR_FUNCTIONS = {"current_setting", "getvariable"}
_READ_PARQUET_OPTIONS = {"hive_partitioning", "union_by_name"}
_STORAGE_URI_RE = re.compile(
    r"^(?P<scheme>s3|gs)://(?P<bucket>\{bucket\}|[a-z0-9][a-z0-9.-]{0,62})/"
    r"(?P<key>[A-Za-z0-9_.*=:/-]+)$"
)


class TableFunctionPolicyError(ValueError):
    """A generic, public-safe refusal from the SQL table-function sandbox."""

    def __init__(self) -> None:
        super().__init__(POLICY_ERROR)


@dataclass(frozen=True)
class StorageRead:
    path: str


def _deny() -> None:
    raise TableFunctionPolicyError()


def _canonical_name(function: exp.Func) -> str:
    if isinstance(function, exp.Anonymous):
        raw = function.this
        if isinstance(raw, exp.Identifier):
            if raw.args.get("quoted"):
                _deny()
            value = str(raw.this or "")
        else:
            value = str(raw or "")
    elif isinstance(function, exp.GenerateSeries):
        value = "generate_series"
    elif isinstance(function, exp.Unnest):
        value = "unnest"
    else:
        value = str(getattr(function, "key", "") or "")
    if not value or not value.isascii():
        _deny()
    normalized = unicodedata.normalize("NFKC", value)
    if normalized != value:
        _deny()
    return value.casefold()


def _canonical_storage_path(
    value: str,
    *,
    expected_bucket: str | None,
    allow_bucket_placeholder: bool,
) -> str:
    if not value or value != value.strip() or not value.isascii():
        _deny()
    if unicodedata.normalize("NFKC", value) != value:
        _deny()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _deny()
    match = _STORAGE_URI_RE.fullmatch(value)
    if not match:
        _deny()
    bucket = match.group("bucket")
    if bucket == "{bucket}":
        if not allow_bucket_placeholder:
            _deny()
    elif expected_bucket and bucket != expected_bucket:
        _deny()
    key = match.group("key")
    if "//" in key or any(part in {"", ".", ".."} for part in key.split("/")):
        _deny()
    return value


def _read_parquet_paths(
    function: exp.Func,
    *,
    expected_bucket: str | None,
    allow_bucket_placeholder: bool,
    allow_server_resolved_path_list: bool,
) -> tuple[str, ...]:
    arguments = list(function.expressions)
    if not arguments:
        _deny()
    path_expression = arguments[0]
    if isinstance(path_expression, exp.Literal) and path_expression.is_string:
        paths = [path_expression]
    elif (
        allow_server_resolved_path_list
        and isinstance(path_expression, exp.Array)
        and path_expression.expressions
        and all(
            isinstance(item, exp.Literal) and item.is_string
            for item in path_expression.expressions
        )
    ):
        paths = list(path_expression.expressions)
    else:
        _deny()
    seen_options: set[str] = set()
    for option in arguments[1:]:
        if not isinstance(option, exp.EQ):
            _deny()
        key = option.this
        if not isinstance(key, exp.Column) or key.table:
            _deny()
        option_name = str(key.name or "").casefold()
        if option_name not in _READ_PARQUET_OPTIONS or option_name in seen_options:
            _deny()
        if not isinstance(option.expression, exp.Boolean):
            _deny()
        seen_options.add(option_name)
    return tuple(
        _canonical_storage_path(
            str(path.this),
            expected_bucket=expected_bucket,
            allow_bucket_placeholder=allow_bucket_placeholder,
        )
        for path in paths
    )


def _relation_functions(tree: exp.Expression) -> list[exp.Func]:
    functions: list[exp.Func] = []
    seen: set[int] = set()

    def add(function: exp.Func) -> None:
        identity = id(function)
        if identity not in seen:
            seen.add(identity)
            functions.append(function)

    resolved_ctes = resolved_cte_table_ids(tree)
    for table in tree.find_all(exp.Table):
        relation = table.this
        if isinstance(relation, exp.Func):
            add(relation)
            continue
        if isinstance(relation, exp.Identifier) and relation.args.get("quoted"):
            if table.args.get("db") is None and table.args.get("catalog") is None:
                if id(table) not in resolved_ctes:
                    _deny()

    for relation_owner in (*tree.find_all(exp.From), *tree.find_all(exp.Join)):
        relation = relation_owner.this
        while isinstance(relation, (exp.Alias, exp.Lateral, exp.Paren)):
            relation = relation.this
        if isinstance(relation, exp.Table):
            relation = relation.this
        if isinstance(relation, exp.Func):
            add(relation)
    return functions


def _reject_adjacent_relation_strings(tree: exp.Expression, tokens: list) -> None:
    """Reject DuckDB E-string replacement scans obscured by parser normalization."""
    ambiguous: set[tuple[str, str]] = set()
    for table in tree.find_all(exp.Table):
        if table.args.get("db") is not None or table.args.get("catalog") is not None:
            continue
        relation = table.this
        alias = table.args.get("alias")
        alias_id = alias.this if isinstance(alias, exp.TableAlias) else None
        if (
            isinstance(relation, exp.Identifier)
            and not relation.args.get("quoted")
            and isinstance(alias_id, exp.Identifier)
            and alias_id.args.get("quoted")
        ):
            ambiguous.add(
                (str(relation.this or "").casefold(), str(alias_id.this or ""))
            )
    if not ambiguous:
        return

    for left, right in zip(tokens, tokens[1:]):
        if (
            left.token_type is TokenType.VAR
            and right.token_type is TokenType.STRING
            and left.end + 1 == right.start
            and (left.text.casefold(), right.text) in ambiguous
        ):
            _deny()


def _validate_table_function_query(
    sql: str,
    *,
    expected_bucket: str | None = None,
    allow_bucket_placeholder: bool = True,
    allow_server_resolved_path_list: bool = False,
) -> tuple[StorageRead, ...]:
    """Validate a complete DuckDB statement and return authorized storage reads."""
    try:
        tokens = Tokenizer(dialect="duckdb").tokenize(sql or "")
        statements = sqlglot.parse(
            sql or "",
            read="duckdb",
            error_level=sqlglot.ErrorLevel.RAISE,
        )
    except Exception:
        _deny()
    if len(statements) != 1 or statements[0] is None:
        _deny()

    tree = statements[0]
    if not isinstance(tree, exp.Query):
        _deny()
    _reject_adjacent_relation_strings(tree, tokens)
    for function in tree.find_all(exp.Anonymous):
        if _canonical_name(function) in _SENSITIVE_SCALAR_FUNCTIONS:
            _deny()
    reads: list[StorageRead] = []
    for function in _relation_functions(tree):
        name = _canonical_name(function)
        if name in _SAFE_GENERATORS:
            continue
        if name not in _STORAGE_FUNCTIONS:
            _deny()
        reads.extend(
            StorageRead(path)
            for path in _read_parquet_paths(
                function,
                expected_bucket=expected_bucket,
                allow_bucket_placeholder=allow_bucket_placeholder,
                allow_server_resolved_path_list=allow_server_resolved_path_list,
            )
        )
    return tuple(reads)


def validate_table_function_query(
    sql: str,
    *,
    expected_bucket: str | None = None,
    allow_bucket_placeholder: bool = True,
    allow_server_resolved_path_list: bool = False,
) -> tuple[StorageRead, ...]:
    """Fail closed without surfacing parser, function, SQL, or path details."""
    try:
        return _validate_table_function_query(
            sql,
            expected_bucket=expected_bucket,
            allow_bucket_placeholder=allow_bucket_placeholder,
            allow_server_resolved_path_list=allow_server_resolved_path_list,
        )
    except TableFunctionPolicyError:
        raise
    except Exception:
        raise TableFunctionPolicyError() from None
