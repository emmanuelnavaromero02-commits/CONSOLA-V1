"""Bounded SELECT grammar used by the public-copy SQL classifier."""

from __future__ import annotations

import re

from app.services.public_sql_select_lexer import SelectToken, tokenize_select_copy


_MAX_SCAN_LENGTH = 8192
_MAX_NESTING_DEPTH = 64
_SELECT_WORD = re.compile(r"(?i)\bselect\b")
_NATURAL_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", re.UNICODE)
_SQL_NATURAL_DISQUALIFIERS = frozenset(
    "all and any array as asc at between by case collate cross desc distinct "
    "else end escape except false fetch filter first for full glob group having in "
    "ilike inner intersect into is isnull join last lateral left like like_regex limit "
    "match natural not notnull null offset on only or order outer over partition "
    "qualify regexp returning right rlike rows similar some tablesample then time true "
    "union unknown using when where window within with zone".split()
)


def _is_unambiguous_business_instruction(value: str) -> bool:
    candidate = value.strip()
    if candidate.endswith((".", "!", "?")):
        candidate = candidate[:-1].rstrip()
    words = candidate.split()
    if words and words[0].casefold() in {"please", "please,"}:
        words = words[1:]
    if len(words) < 5 or words[0].casefold() != "select":
        return False
    from_indexes = [
        index for index, word in enumerate(words) if word.casefold() == "from"
    ]
    if len(from_indexes) != 1:
        return False
    from_index = from_indexes[0]
    target = words[1:from_index]
    source = words[from_index + 1 :]
    phrases = (*target, *source)
    return bool(
        target
        and source
        and len(target) <= 8
        and len(source) <= 8
        and len(source) >= 3
        and source[0].casefold() in {"a", "an", "the"}
        and all(_NATURAL_WORD.fullmatch(word) for word in phrases)
        and not any(word.casefold() in _SQL_NATURAL_DISQUALIFIERS for word in phrases)
    )


def _select_tokens_are_sql(tokens: tuple[SelectToken, ...]) -> bool:
    for select_index, token in enumerate(tokens):
        if token.kind != "word" or token.value.casefold() != "select":
            continue
        projection = list(tokens[select_index + 1 :])
        if not projection:
            continue
        if any(
            item.kind == "word" and item.value.casefold() == "from"
            for item in projection[:-1]
        ):
            return True
        if projection[-1].kind == "symbol" and projection[-1].value in {".", "!", "?"}:
            projection.pop()
        if not projection:
            continue
        if projection[0].kind == "word" and projection[0].value.casefold() in {
            "all",
            "distinct",
        }:
            projection = projection[1:]
        if not projection:
            continue

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
            if (
                item.kind == "word"
                and item.value.casefold() in _SQL_NATURAL_DISQUALIFIERS
            ):
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
    if _is_unambiguous_business_instruction(value):
        return False
    tokens = tokenize_select_copy(value)
    return tokens is None or _select_tokens_are_sql(tokens)


__all__ = ("contains_public_select_sql",)
