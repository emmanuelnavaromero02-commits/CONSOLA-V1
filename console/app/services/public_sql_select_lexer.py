"""Linear bounded lexer for SELECT-shaped public copy."""

from __future__ import annotations

import re
from typing import NamedTuple


_MAX_TOKENS = 512
_MAX_COMMENT_DEPTH = 64
_DOLLAR_DELIMITER = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
_MULTI_CHARACTER_OPERATORS = (
    "#>>",
    "->>",
    "::",
    "||",
    "<=",
    ">=",
    "<>",
    "!=",
    "->",
    "#>",
    "=>",
    ":=",
)


class SelectToken(NamedTuple):
    kind: str
    value: str


def _consume_quote(
    value: str,
    quote_index: int,
    *,
    quote: str,
    backslash_escapes: bool,
) -> int | None:
    index = quote_index + 1
    while index < len(value):
        if backslash_escapes and value[index] == "\\":
            index += 2
            continue
        if value[index] == quote:
            if index + 1 < len(value) and value[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return None


def _consume_block_comment(value: str, start: int) -> int | None:
    depth = 1
    index = start + 2
    while index < len(value):
        if value.startswith("/*", index):
            depth += 1
            if depth > _MAX_COMMENT_DEPTH:
                return None
            index += 2
            continue
        if value.startswith("*/", index):
            depth -= 1
            index += 2
            if depth == 0:
                return index
            continue
        index += 1
    return None


def _prefixed_string_quote(value: str, index: int) -> tuple[int, bool] | None:
    if index + 1 < len(value) and value[index] in "eEnNbBxX":
        if value[index + 1] == "'":
            return index + 1, value[index] in "eE"
    if index + 2 < len(value) and value[index] in "uU":
        if value[index + 1 : index + 3] == "&'":
            return index + 2, False
    if value[index] == "_":
        end = index + 1
        while end < len(value) and (value[end].isalnum() or value[end] == "_"):
            end += 1
        if end < len(value) and end > index + 1 and value[end] == "'":
            return end, False
    return None


def tokenize_select_copy(value: str) -> tuple[SelectToken, ...] | None:
    """Tokenize copy, returning None on malformed input or bounded overflow."""

    tokens: list[SelectToken] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char.isspace():
            index += 1
            continue
        if value.startswith("--", index):
            newline = value.find("\n", index + 2)
            index = len(value) if newline < 0 else newline + 1
            continue
        if value.startswith("/*", index):
            end = _consume_block_comment(value, index)
            if end is None:
                return None
            index = end
            continue

        prefixed = _prefixed_string_quote(value, index)
        if prefixed is not None:
            quote_index, backslash_escapes = prefixed
            end = _consume_quote(
                value,
                quote_index,
                quote="'",
                backslash_escapes=backslash_escapes,
            )
            if end is None:
                return None
            tokens.append(SelectToken("string", value[index:end]))
            index = end
        elif (
            char == "'"
            and index > 0
            and index + 1 < len(value)
            and value[index - 1].isalpha()
            and value[index + 1].isalpha()
        ):
            tokens.append(SelectToken("symbol", char))
            index += 1
        elif char == "'":
            end = _consume_quote(
                value,
                index,
                quote="'",
                backslash_escapes=False,
            )
            if end is None:
                return None
            tokens.append(SelectToken("string", value[index:end]))
            index = end
        elif char == '"':
            end = _consume_quote(
                value,
                index,
                quote='"',
                backslash_escapes=False,
            )
            if end is None:
                return None
            tokens.append(SelectToken("identifier", value[index:end]))
            index = end
        elif char == "$":
            delimiter = _DOLLAR_DELIMITER.match(value, index)
            if delimiter is not None:
                marker = delimiter.group(0)
                closing = value.find(marker, delimiter.end())
                if closing < 0:
                    return None
                end = closing + len(marker)
                tokens.append(SelectToken("string", value[index:end]))
                index = end
            else:
                end = index + 1
                while end < len(value) and value[end].isdigit():
                    end += 1
                tokens.append(
                    SelectToken(
                        "parameter" if end > index + 1 else "symbol", value[index:end]
                    )
                )
                index = end
        elif char.isalpha() or char == "_":
            end = index + 1
            while end < len(value) and (
                value[end].isalnum() or value[end] in {"_", "$"}
            ):
                end += 1
            tokens.append(SelectToken("word", value[index:end]))
            index = end
        elif char.isdigit():
            end = index + 1
            while end < len(value) and (
                value[end].isalnum() or value[end] in {"_", "."}
            ):
                end += 1
            tokens.append(SelectToken("number", value[index:end]))
            index = end
        else:
            operator = next(
                (
                    item
                    for item in _MULTI_CHARACTER_OPERATORS
                    if value.startswith(item, index)
                ),
                char,
            )
            tokens.append(SelectToken("symbol", operator))
            index += len(operator)

        if len(tokens) > _MAX_TOKENS:
            return None
    return tuple(tokens)


__all__ = ("SelectToken", "tokenize_select_copy")
