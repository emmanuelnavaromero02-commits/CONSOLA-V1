from __future__ import annotations

import re

from app.services.public_sql_prefix_grammar import has_complete_statement_prefix
from app.services.public_sql_runtime_catalog import STATEMENT_HEADS
from app.services.public_sql_select_lexer import SelectToken, tokenize_select_copy


_MAX_SCAN_LENGTH = 8192
_MAX_NESTING_DEPTH = 64
_HEAD_WORD = re.compile(
    rf"(?i)(?<![\w$])(?:{'|'.join(sorted(STATEMENT_HEADS))})(?![\w$])"
)


def _word(token: SelectToken) -> str | None:
    return token.value.casefold() if token.kind == "word" else None


def _statement_tail(
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
    return tokens[start + 1 : end]


def _has_invalid_nesting(tokens: tuple[SelectToken, ...]) -> bool:
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for token in tokens:
        if token.kind != "symbol":
            continue
        if token.value in {"(", "[", "{"}:
            stack.append(token.value)
            if len(stack) > _MAX_NESTING_DEPTH:
                return True
        elif token.value in pairs:
            if not stack or stack.pop() != pairs[token.value]:
                return True
    return bool(stack)


def contains_runtime_sql(value: str) -> bool:

    if not _HEAD_WORD.search(value):
        return False
    if len(value) > _MAX_SCAN_LENGTH:
        return True
    tokens = tokenize_select_copy(value)
    if tokens is None or _has_invalid_nesting(tokens):
        return True

    for index, token in enumerate(tokens):
        head = _word(token)
        if head in STATEMENT_HEADS and has_complete_statement_prefix(
            head,
            _statement_tail(tokens, index),
        ):
            return True
    return False


__all__ = ("contains_runtime_sql",)
