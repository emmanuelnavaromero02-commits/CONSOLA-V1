"""Closed DuckDB table-function policy for externally supplied SELECT SQL."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

import sqlglot
from sqlglot import exp


POLICY_ERROR = "SQL blocked by safety policy"
_STORAGE_FUNCTIONS = {"read_parquet"}
_SAFE_GENERATORS = {"generate_series", "unnest"}
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


def _read_parquet_path(
    function: exp.Func,
    *,
    expected_bucket: str | None,
    allow_bucket_placeholder: bool,
) -> str:
    arguments = list(function.expressions)
    if not arguments:
        _deny()
    path = arguments[0]
    if not isinstance(path, exp.Literal) or not path.is_string:
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
    return _canonical_storage_path(
        str(path.this),
        expected_bucket=expected_bucket,
        allow_bucket_placeholder=allow_bucket_placeholder,
    )


def _relation_functions(tree: exp.Expression) -> list[exp.Func]:
    functions: list[exp.Func] = []
    seen: set[int] = set()

    def add(function: exp.Func) -> None:
        identity = id(function)
        if identity not in seen:
            seen.add(identity)
            functions.append(function)

    for table in tree.find_all(exp.Table):
        relation = table.this
        if isinstance(relation, exp.Func):
            add(relation)
            continue
        if isinstance(relation, exp.Identifier) and relation.args.get("quoted"):
            # DuckDB treats quoted, unqualified relation names such as
            # ``'secret.csv'`` as replacement scans. SQLGlot intentionally
            # normalizes single and double quotes here, so a filename cannot
            # be distinguished from a quoted identifier by extension alone.
            # Public SQL has no authorized unqualified physical tables;
            # registered Gold relations remain qualified (pggold.<table>).
            if table.args.get("db") is None and table.args.get("catalog") is None:
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


def _validate_table_function_query(
    sql: str,
    *,
    expected_bucket: str | None = None,
    allow_bucket_placeholder: bool = True,
) -> tuple[StorageRead, ...]:
    """Validate a complete DuckDB statement and return authorized storage reads."""
    try:
        statements = sqlglot.parse(
            sql or "",
            read="duckdb",
            error_level=sqlglot.ErrorLevel.RAISE,
        )
    except Exception:
        _deny()
    if len(statements) != 1 or statements[0] is None:
        _deny()

    reads: list[StorageRead] = []
    for function in _relation_functions(statements[0]):
        name = _canonical_name(function)
        if name in _SAFE_GENERATORS:
            continue
        if name not in _STORAGE_FUNCTIONS:
            _deny()
        reads.append(
            StorageRead(
                _read_parquet_path(
                    function,
                    expected_bucket=expected_bucket,
                    allow_bucket_placeholder=allow_bucket_placeholder,
                )
            )
        )
    return tuple(reads)


def validate_table_function_query(
    sql: str,
    *,
    expected_bucket: str | None = None,
    allow_bucket_placeholder: bool = True,
) -> tuple[StorageRead, ...]:
    """Fail closed without surfacing parser, function, SQL, or path details."""
    try:
        return _validate_table_function_query(
            sql,
            expected_bucket=expected_bucket,
            allow_bucket_placeholder=allow_bucket_placeholder,
        )
    except TableFunctionPolicyError:
        raise
    except Exception:
        raise TableFunctionPolicyError() from None
