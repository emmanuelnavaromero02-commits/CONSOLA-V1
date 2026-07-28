"""Bounded production prefixes for the versioned runtime SQL catalogue."""

from __future__ import annotations

from app.services.public_sql_prefix_shapes import (
    call_prefix,
    copy_prefix,
    grant_or_revoke_prefix,
    pivot_prefix,
    set_prefix,
    update_prefix,
    with_prefix,
)
from app.services.public_sql_runtime_catalog import STATEMENT_HEADS
from app.services.public_sql_select_lexer import SelectToken


_NO_ARGUMENT_HEADS = frozenset(
    {
        "abort",
        "analyse",
        "analyze",
        "begin",
        "checkpoint",
        "cluster",
        "commit",
        "end",
        "rollback",
        "vacuum",
    }
)
_NAME_HEADS = frozenset(
    {
        "close",
        "deallocate",
        "detach",
        "exec",
        "execute",
        "install",
        "listen",
        "load",
        "notify",
        "pragma",
        "savepoint",
        "summarize",
        "table",
        "use",
    }
)
_DDL_HEADS = frozenset({"alter", "create", "drop"})
_DDL_OBJECT_WORDS = frozenset(
    {
        "access",
        "aggregate",
        "cast",
        "collation",
        "conversion",
        "database",
        "domain",
        "event",
        "extension",
        "foreign",
        "function",
        "group",
        "index",
        "language",
        "large",
        "macro",
        "materialized",
        "operator",
        "policy",
        "procedure",
        "publication",
        "role",
        "routine",
        "rule",
        "schema",
        "secret",
        "sequence",
        "server",
        "statistics",
        "subscription",
        "table",
        "tablespace",
        "text",
        "transform",
        "trigger",
        "type",
        "user",
        "view",
    }
)


def _word(token: SelectToken) -> str | None:
    return token.value.casefold() if token.kind == "word" else None


def _qualified_name_end(tokens: tuple[SelectToken, ...], start: int = 0) -> int | None:
    if start >= len(tokens) or tokens[start].kind not in {"identifier", "word"}:
        return None
    index = start + 1
    while (
        index + 1 < len(tokens)
        and tokens[index] == SelectToken("symbol", ".")
        and tokens[index + 1].kind in {"identifier", "word"}
    ):
        index += 2
    return index


def _has_word(
    tokens: tuple[SelectToken, ...], expected: set[str] | frozenset[str]
) -> bool:
    return any(_word(token) in expected for token in tokens)


def _starts_with_words(tokens: tuple[SelectToken, ...], *words: str) -> bool:
    return len(tokens) >= len(words) and all(
        _word(tokens[index]) == word for index, word in enumerate(words)
    )


def _has_name(tokens: tuple[SelectToken, ...], start: int = 0) -> bool:
    return _qualified_name_end(tokens, start) is not None


def _has_target(tokens: tuple[SelectToken, ...], start: int = 0) -> bool:
    return bool(
        start < len(tokens)
        and (
            tokens[start].kind in {"identifier", "string", "word"}
            or tokens[start] == SelectToken("symbol", "(")
        )
    )


def has_complete_statement_prefix(
    head: str,
    tail: tuple[SelectToken, ...],
) -> bool:
    """Return whether ``head + tail`` starts with a complete SQL production."""

    if head in _NO_ARGUMENT_HEADS or head == "select":
        return True
    if head in _NAME_HEADS:
        return _has_target(tail)
    if head in {"desc", "describe"}:
        return _has_name(tail)
    if head == "show":
        return not tail or _has_name(tail)
    if head == "from":
        return _has_target(tail)
    if head == "truncate":
        return (
            _has_name(tail, 1) if _starts_with_words(tail, "table") else _has_name(tail)
        )
    if head in {"pivot", "pivot_wider", "unpivot"}:
        return pivot_prefix(tail)
    if head == "call":
        return call_prefix(tail)
    if head == "set":
        return set_prefix(tail)
    if head == "reset":
        return (
            _has_name(tail, 1)
            if _starts_with_words(tail, "variable")
            else _has_name(tail)
        )
    if head == "copy":
        return copy_prefix(tail)
    if head == "grant":
        return grant_or_revoke_prefix(tail, direction="to")
    if head == "revoke":
        return grant_or_revoke_prefix(tail, direction="from")
    if head == "update":
        return update_prefix(tail)
    if head == "merge":
        return _starts_with_words(tail, "into") and _has_word(tail[1:], {"using"})
    if head == "insert":
        return _starts_with_words(tail, "into")
    if head == "delete":
        return _starts_with_words(tail, "from")
    if head in _DDL_HEADS:
        return bool(tail and _word(tail[0]) in _DDL_OBJECT_WORDS)
    if head == "values":
        return bool(tail and tail[0] == SelectToken("symbol", "("))
    if head == "with":
        return with_prefix(tail)
    if head == "attach":
        return (
            _has_target(tail, 1)
            if _starts_with_words(tail, "database")
            else _has_target(tail)
        )
    if head == "comment":
        return _starts_with_words(tail, "on") and _has_word(tail[1:], {"is"})
    if head == "declare":
        return _has_word(tail, {"cursor"}) and _has_word(tail, {"for"})
    if head == "discard":
        return bool(
            tail
            and _word(tail[0]) in {"all", "plans", "sequences", "temp", "temporary"}
        )
    if head == "do":
        if tail and tail[0].kind == "string":
            return True
        language_end = (
            _qualified_name_end(tail, 1)
            if _starts_with_words(tail, "language")
            else None
        )
        return bool(
            language_end is not None
            and language_end < len(tail)
            and tail[language_end].kind == "string"
        )
    if head == "explain":
        return _has_word(tail, STATEMENT_HEADS)
    if head in {"export", "import"}:
        return bool(tail and _word(tail[0]) in {"database", "foreign"})
    if head in {"fetch", "move"}:
        return _has_word(tail, {"from", "in"})
    if head == "force":
        return bool(tail and _word(tail[0]) in {"checkpoint", "install"})
    if head == "lock":
        target = 1 if _starts_with_words(tail, "table") else 0
        if target < len(tail) and _word(tail[target]) == "only":
            target += 1
        return _has_name(tail, target)
    if head == "prepare":
        return _has_word(tail, {"as", "transaction"})
    if head == "reassign":
        return _starts_with_words(tail, "owned")
    if head == "refresh":
        return _starts_with_words(tail, "materialized", "view")
    if head == "reindex":
        return bool(
            tail
            and _word(tail[0]) in {"database", "index", "schema", "system", "table"}
        )
    if head == "release":
        return (
            _has_name(tail, 1)
            if _starts_with_words(tail, "savepoint")
            else _has_name(tail)
        )
    if head == "security":
        if _starts_with_words(tail, "label", "on"):
            return len(tail) >= 4 and _has_target(tail, 3)
        provider_end = (
            _qualified_name_end(tail, 2)
            if _starts_with_words(tail, "label", "for")
            else None
        )
        return bool(
            provider_end is not None
            and provider_end + 2 < len(tail)
            and _word(tail[provider_end]) == "on"
            and _has_target(tail, provider_end + 2)
        )
    if head == "start":
        return bool(tail and _word(tail[0]) in {"transaction", "work"})
    if head == "unlisten":
        return bool(tail and (_has_name(tail) or tail[0].value == "*"))
    return False


__all__ = ("has_complete_statement_prefix",)
