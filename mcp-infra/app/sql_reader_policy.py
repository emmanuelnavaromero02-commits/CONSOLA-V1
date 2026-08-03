"""Closed DuckDB reader policy for cartridge-supplied SELECT SQL.

The scoping rewriter in ``app.tools.cartridges`` is a regex: it inspects the
first scoped literal and leaves it alone once it already carries a
``tenant_id``. DuckDB evaluates the *effective* argument, so slicing,
concatenation, casts or nested calls can build a path the rewriter never saw
— including ``/etc/passwd`` or another workspace's prefix.

This module replaces that inspection with a closed allowlist over the parsed
tree. A storage reader may only receive direct string literals, and the
resulting path must resolve, server side, to the caller's own
tenant/workspace under an allowed layer of the cartridge it asked for.
Anything else — concatenation, subscripting, casts, parameters, nested
functions, macros, replacement scans, dynamic lists, arithmetic, CALL/COPY, or
any construct not recognised here — is denied.

Errors never carry the SQL, the path or any secret.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType, Tokenizer


POLICY_ERROR = "SQL blocked by safety policy"

_STORAGE_FUNCTIONS = {"read_parquet", "read_csv", "read_csv_auto", "read_json_auto"}
_SAFE_GENERATORS = {"generate_series", "unnest"}
_SENSITIVE_SCALAR_FUNCTIONS = {
    "current_setting",
    "getvariable",
    "getenv",
    "read_blob",
    "read_text",
}
_READER_OPTIONS = {
    "header",
    "all_varchar",
    "hive_partitioning",
    "union_by_name",
    "auto_detect",
}
_ALLOWED_LAYERS = {"raw", "silver", "gold"}
_STORAGE_URI_RE = re.compile(
    r"^s3://(?P<bucket>\{bucket\}|[a-z0-9][a-z0-9.-]{0,62})/"
    r"(?P<key>[A-Za-z0-9_.*=:/-]+)$"
)
_SCOPE_RE = re.compile(
    r"(?:^|/)tenant_id=(?P<tenant>[^/]+)/workspace_id=(?P<workspace>[^/]+)(?:/|$)"
)


class ReaderPolicyError(ValueError):
    """Fail closed without surfacing SQL, paths, parser detail or secrets."""

    def __init__(self) -> None:
        super().__init__(POLICY_ERROR)


@dataclass(frozen=True)
class StorageRead:
    path: str
    layer: str
    root: str


def _deny() -> None:
    raise ReaderPolicyError()


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
        value = str(function.sql_name() or "")
    if not value or not value.isascii():
        _deny()
    if unicodedata.normalize("NFKC", value) != value:
        _deny()
    return value.casefold()


def _canonical_path(value: str, *, expected_bucket: str | None) -> str:
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
    if bucket != "{bucket}" and expected_bucket and bucket != expected_bucket:
        _deny()
    key = match.group("key")
    if "//" in key or any(part in {"", ".", ".."} for part in key.split("/")):
        _deny()
    return value


def _resolve_scope(
    path: str,
    *,
    cartridge_id: str,
    tenant_id: str,
    workspace_id: str,
    shared_roots: frozenset[str],
) -> StorageRead:
    """Validate layer, cartridge root and scope on the effective path."""
    key = _STORAGE_URI_RE.fullmatch(path).group("key")
    parts = key.split("/")
    if len(parts) < 3:
        _deny()
    layer, root = parts[0], parts[1]
    if layer not in _ALLOWED_LAYERS:
        _deny()
    if root != cartridge_id and not (layer == "raw" and root in shared_roots):
        _deny()
    scope = _SCOPE_RE.search(key)
    if not scope:
        _deny()
    if scope.group("tenant") != tenant_id or scope.group("workspace") != workspace_id:
        _deny()
    # A second scope marker anywhere in the key would make the effective prefix
    # ambiguous; only one is ever emitted by the server-side layout.
    if len(_SCOPE_RE.findall(key)) != 1:
        _deny()
    return StorageRead(path=path, layer=layer, root=root)


def _reader_paths(function: exp.Func, *, expected_bucket: str | None) -> list[str]:
    # sqlglot gives read_csv a typed node whose path sits in ``this`` and whose
    # options sit in ``expressions``; the other readers stay anonymous with the
    # path as their first expression. Both shapes resolve to one literal path.
    if isinstance(function, exp.ReadCSV):
        target = function.this
        options = list(function.expressions)
    else:
        arguments = list(function.expressions)
        if not arguments:
            _deny()
        target = arguments[0]
        options = arguments[1:]
    # Only a direct string literal is accepted. Concatenation, subscripting,
    # casts, parameters, lists, arithmetic and nested calls all land here.
    if not (isinstance(target, exp.Literal) and target.is_string):
        _deny()
    seen: set[str] = set()
    for option in options:
        if not isinstance(option, exp.EQ):
            _deny()
        key = option.this
        if not isinstance(key, exp.Column) or key.table:
            _deny()
        name = str(key.name or "").casefold()
        if name not in _READER_OPTIONS or name in seen:
            _deny()
        if not isinstance(option.expression, exp.Boolean):
            _deny()
        seen.add(name)
    return [_canonical_path(str(target.this), expected_bucket=expected_bucket)]


def _relation_functions(tree: exp.Expression) -> list[exp.Func]:
    functions: list[exp.Func] = []
    seen: set[int] = set()

    def add(function: exp.Func) -> None:
        if id(function) not in seen:
            seen.add(id(function))
            functions.append(function)

    cte_names = {
        str(cte.alias_or_name or "").casefold()
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }
    for table in tree.find_all(exp.Table):
        relation = table.this
        if isinstance(relation, exp.Func):
            add(relation)
            continue
        if table.db or table.catalog:
            _deny()
        name = str(table.name or "")
        if not name.isascii() or name.casefold() not in cte_names:
            _deny()
    for owner in (*tree.find_all(exp.From), *tree.find_all(exp.Join)):
        relation = owner.this
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


def _validate(
    sql: str,
    *,
    cartridge_id: str,
    tenant_id: str,
    workspace_id: str,
    expected_bucket: str | None,
    shared_roots: frozenset[str],
) -> tuple[StorageRead, ...]:
    if not tenant_id or not workspace_id:
        _deny()
    try:
        tokens = Tokenizer(dialect="duckdb").tokenize(sql or "")
        statements = sqlglot.parse(
            sql or "",
            read="duckdb",
            error_level=sqlglot.ErrorLevel.RAISE,
            error_message_context=0,
        )
    except Exception:
        raise ReaderPolicyError() from None
    if len(statements) != 1 or statements[0] is None:
        _deny()
    tree = statements[0]
    # CALL, COPY, PRAGMA, ATTACH and every other statement kind fail here.
    if not isinstance(tree, exp.Query):
        _deny()
    if list(tree.find_all(exp.Placeholder)) or list(tree.find_all(exp.Parameter)):
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
        for path in _reader_paths(function, expected_bucket=expected_bucket):
            reads.append(
                _resolve_scope(
                    path,
                    cartridge_id=cartridge_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    shared_roots=shared_roots,
                )
            )
    return tuple(reads)


def validate_cartridge_reader_query(
    sql: str,
    *,
    cartridge_id: str,
    tenant_id: str,
    workspace_id: str,
    expected_bucket: str | None = None,
    shared_roots: frozenset[str] = frozenset(),
) -> tuple[StorageRead, ...]:
    """Validate reader arguments and their effective scope, or fail closed."""
    try:
        return _validate(
            sql,
            cartridge_id=cartridge_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            expected_bucket=expected_bucket,
            shared_roots=shared_roots,
        )
    except ReaderPolicyError:
        raise
    except Exception:
        raise ReaderPolicyError() from None
