"""Bounded full-stream recognizer for versioned SQL statement productions."""

from __future__ import annotations

import re

from app.services.public_sql_runtime_catalog import (
    HeadPolicy,
    STATEMENT_HEAD_POLICIES,
    STATEMENT_HEADS,
)
from app.services.public_sql_select_lexer import SelectToken, tokenize_select_copy


_MAX_SCAN_LENGTH = 8192
_HEAD_WORD = re.compile(
    rf"(?i)(?<![\w$])(?:{'|'.join(sorted(STATEMENT_HEADS))})(?![\w$])"
)
_TRAILING_COPY_PUNCTUATION = frozenset({".", "!", "?"})
_SHOW_PHRASES = frozenset(
    {
        ("all", "tables"),
        ("all",),
        ("databases",),
        ("search_path",),
        ("tables",),
        ("time", "zone"),
        ("timezone",),
    }
)
_SET_PREFIXES = frozenset(
    {
        "constraints",
        "local",
        "names",
        "role",
        "schema",
        "session",
        "time",
        "transaction",
        "variable",
    }
)
_EMBEDDED_RELATION_HEADS = frozenset(
    {"desc", "describe", "from", "summarize", "table", "truncate", "use"}
)
_EMBEDDED_NO_ARGUMENT_HEADS = frozenset(
    {"abort", "begin", "checkpoint", "commit", "end", "rollback", "vacuum"}
)
_SQL_OBJECT_WORDS = frozenset(
    {
        "database",
        "domain",
        "extension",
        "function",
        "index",
        "macro",
        "materialized",
        "policy",
        "procedure",
        "role",
        "schema",
        "secret",
        "sequence",
        "table",
        "tablespace",
        "trigger",
        "type",
        "user",
        "view",
    }
)


def _word(token: SelectToken) -> str | None:
    return token.value.casefold() if token.kind == "word" else None


def _statement_slice(
    tokens: tuple[SelectToken, ...],
    start: int,
) -> tuple[SelectToken, ...]:
    end = next(
        (
            index
            for index in range(start, len(tokens))
            if tokens[index] == SelectToken("symbol", ";")
        ),
        len(tokens),
    )
    statement = list(tokens[start:end])
    if statement and statement[-1].kind == "symbol":
        if statement[-1].value in _TRAILING_COPY_PUNCTUATION:
            statement.pop()
    return tuple(statement)


def _qualified_name_end(tokens: tuple[SelectToken, ...], start: int) -> int | None:
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


def _is_single_name(tokens: tuple[SelectToken, ...]) -> bool:
    return _qualified_name_end(tokens, 0) == len(tokens)


def _show_production(tail: tuple[SelectToken, ...]) -> bool:
    words = tuple(_word(token) for token in tail)
    return not tail or _is_single_name(tail) or words in _SHOW_PHRASES


def _describe_or_use_production(tail: tuple[SelectToken, ...]) -> bool:
    return _is_single_name(tail)


def _call_production(tail: tuple[SelectToken, ...]) -> bool:
    name_end = _qualified_name_end(tail, 0)
    return bool(
        name_end is not None
        and name_end < len(tail)
        and tail[name_end] == SelectToken("symbol", "(")
    )


def _set_production(tail: tuple[SelectToken, ...]) -> bool:
    if not tail:
        return False
    first_word = _word(tail[0])
    if first_word in _SET_PREFIXES:
        return len(tail) >= 2
    name_end = _qualified_name_end(tail, 0)
    return bool(
        name_end is not None
        and name_end < len(tail)
        and (
            tail[name_end] == SelectToken("symbol", "=")
            or _word(tail[name_end]) == "to"
        )
    )


def _contains_top_level_word(
    tokens: tuple[SelectToken, ...],
    expected: frozenset[str],
) -> bool:
    depth = 0
    for token in tokens:
        if token.kind == "symbol" and token.value in {"(", "["}:
            depth += 1
        elif token.kind == "symbol" and token.value in {
            ")",
            "]",
        }:
            depth = max(0, depth - 1)
        elif depth == 0 and _word(token) in expected:
            return True
    return False


def _copy_production(tail: tuple[SelectToken, ...]) -> bool:
    return bool(tail) and _contains_top_level_word(tail[1:], frozenset({"from", "to"}))


def _grant_production(tail: tuple[SelectToken, ...]) -> bool:
    return bool(tail) and _contains_top_level_word(tail[1:], frozenset({"to"}))


def _structural_production(
    head: str,
    tail: tuple[SelectToken, ...],
) -> bool:
    if head == "show":
        return _show_production(tail)
    if head in {"desc", "describe", "use"}:
        return _describe_or_use_production(tail)
    if head == "call":
        return _call_production(tail)
    if head == "set":
        return _set_production(tail)
    if head == "copy":
        return _copy_production(tail)
    if head == "grant":
        return _grant_production(tail)
    return False


def _embedded_relation_production(
    head: str,
    tail: tuple[SelectToken, ...],
) -> bool:
    if head in {"desc", "describe", "summarize", "table", "truncate", "use"}:
        return _is_single_name(tail)
    if head != "from":
        return False
    relation_end = _qualified_name_end(tail, 0)
    if relation_end is None:
        return bool(tail and tail[0].kind == "word" and len(tail) == 1)
    if relation_end == len(tail):
        return True
    return relation_end + 1 == len(tail) and tail[relation_end].kind == "word"


def _embedded_fail_closed_production(
    head: str,
    tail: tuple[SelectToken, ...],
) -> bool:
    words = tuple(word for token in tail if (word := _word(token)) is not None)
    if head == "select":
        return bool(tail)
    if head == "values":
        return bool(tail and tail[0] == SelectToken("symbol", "("))
    if head == "with":
        return "as" in words and any(
            token == SelectToken("symbol", "(") for token in tail
        )
    if head == "update":
        return "set" in words
    if head == "merge":
        return "into" in words and "using" in words
    if head == "insert":
        return bool(words and words[0] == "into")
    if head == "delete":
        return bool(words and words[0] == "from")
    if head in {"alter", "create", "drop"}:
        return bool(words and words[0] in _SQL_OBJECT_WORDS)
    if head == "explain":
        return bool(words and words[0] in STATEMENT_HEADS)
    return False


def _is_statement_boundary(tokens: tuple[SelectToken, ...], index: int) -> bool:
    return index == 0 or tokens[index - 1] == SelectToken("symbol", ";")


def contains_runtime_sql(value: str) -> bool:
    """Detect statement heads and sound productions without a runtime SQL parser."""

    if not _HEAD_WORD.search(value):
        return False
    if len(value) > _MAX_SCAN_LENGTH:
        return True
    tokens = tokenize_select_copy(value)
    if tokens is None:
        return True

    for index, token in enumerate(tokens):
        head = _word(token)
        policy = STATEMENT_HEAD_POLICIES.get(head or "")
        if policy is None:
            continue
        statement = _statement_slice(tokens, index)
        tail = statement[1:]
        at_boundary = _is_statement_boundary(tokens, index)
        if at_boundary and policy is HeadPolicy.FAIL_CLOSED:
            return True
        if _structural_production(head or "", tail):
            return True
        if at_boundary:
            continue
        if head in _EMBEDDED_RELATION_HEADS and _embedded_relation_production(
            head or "", tail
        ):
            return True
        if head in _EMBEDDED_NO_ARGUMENT_HEADS and not tail:
            return True
        if policy is HeadPolicy.FAIL_CLOSED and _embedded_fail_closed_production(
            head or "", tail
        ):
            return True
    return False


__all__ = ("contains_runtime_sql",)
