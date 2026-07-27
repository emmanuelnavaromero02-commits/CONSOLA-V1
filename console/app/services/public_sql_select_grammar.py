"""Bounded SELECT grammar used by the public-copy SQL classifier."""

from __future__ import annotations

import re

from app.services.public_sql_business_instruction import (
    is_unambiguous_business_select,
)
from app.services.public_sql_select_lexer import SelectToken, tokenize_select_copy


_MAX_SCAN_LENGTH = 8192
_MAX_NESTING_DEPTH = 64
_SELECT_WORD = re.compile(r"(?i)\bselect\b")
_SQL_PROJECTION_WORDS = frozenset(
    "all and any array as asc at between by case collate cross desc distinct "
    "else end escape except false fetch filter first for full glob group having in "
    "ilike inner intersect into is isnull join last lateral left like like_regex limit "
    "match natural not notnull null offset on only or order outer over partition "
    "qualify regexp returning right rlike rows similar some tablesample then time true "
    "union unknown using when where window within with zone".split()
)


def _select_tokens_are_sql(tokens: tuple[SelectToken, ...]) -> bool:
    for select_index, token in enumerate(tokens):
        if token.kind != "word" or token.value.casefold() != "select":
            continue
        projection = list(tokens[select_index + 1 :])
        if not projection:
            return True
        if any(
            item.kind == "word" and item.value.casefold() == "from"
            for item in projection[:-1]
        ):
            return True
        if projection[-1].kind == "symbol" and projection[-1].value in {".", "!", "?"}:
            projection.pop()
        if not projection:
            return True
        if projection[0].kind == "word" and projection[0].value.casefold() in {
            "all",
            "distinct",
        }:
            projection = projection[1:]
        if not projection:
            return True

        depth = 0
        for item in projection:
            if item.kind == "symbol" and item.value in {"(", "["}:
                depth += 1
                if depth > _MAX_NESTING_DEPTH:
                    return True
            elif item.kind == "symbol" and item.value in {
                ")",
                "]",
            }:
                depth -= 1
                if depth < 0:
                    return True
            if item.kind in {"string", "number", "parameter"}:
                return True
            if item.kind == "symbol":
                return True
            if item.kind == "word" and item.value.casefold() in _SQL_PROJECTION_WORDS:
                return True
        if depth != 0:
            return True

        identifiers = [
            item for item in projection if item.kind in {"word", "identifier"}
        ]
        if len(identifiers) == len(projection) and len(identifiers) <= 2:
            return True
    return False


def contains_public_select_sql(value: str) -> bool:
    """Detect bounded SELECT/FROM and no-FROM projection grammar."""

    if len(value) > _MAX_SCAN_LENGTH or not _SELECT_WORD.search(value):
        return False
    tokens = tokenize_select_copy(value)
    if tokens is None:
        return True
    if is_unambiguous_business_select(value, tokens):
        return False
    return _select_tokens_are_sql(tokens)


__all__ = ("contains_public_select_sql",)
