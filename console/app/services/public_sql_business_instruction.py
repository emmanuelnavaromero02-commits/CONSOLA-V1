"""Positional grammar for SELECT-shaped natural-language instructions."""

from __future__ import annotations

import re

from app.services.public_sql_select_lexer import SelectToken
from app.services.public_sql_word_statements import (
    contains_embedded_word_statement,
)


_MAX_NATURAL_WORDS = 16
_NATURAL_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_RAW_NATURAL_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", re.UNICODE)
_RAW_WORD_COMPONENT = re.compile(r"[-'’]")
_WORD_CONNECTORS = frozenset({"-", "'", "’"})
_TERMINAL_PUNCTUATION = frozenset({".", "!", "?"})
_SOURCE_DETERMINERS = frozenset(
    {
        "a",
        "an",
        "her",
        "his",
        "its",
        "my",
        "our",
        "that",
        "the",
        "their",
        "these",
        "this",
        "those",
        "your",
    }
)
_SOURCE_COMPLEMENTS = frozenset({"for", "in"})
_FOR_LOCK_WORDS = frozenset({"key", "no", "share", "update"})
_SQL_NATURAL_DISQUALIFIERS = frozenset(
    "all and any array as asc at between by case collate cross desc distinct "
    "else end escape except false fetch filter first for full glob group having in "
    "ilike inner intersect into is isnull join last lateral left like like_regex limit "
    "match natural not notnull null offset on only or order outer over partition "
    "qualify regexp returning right rlike rows similar some tablesample then time true "
    "union unknown using when where window within with zone".split()
)


def _strip_sentence_framing(
    tokens: tuple[SelectToken, ...],
) -> tuple[tuple[SelectToken, ...], bool, bool] | None:
    framed = list(tokens)
    has_terminal = bool(
        framed
        and framed[-1].kind == "symbol"
        and framed[-1].value in _TERMINAL_PUNCTUATION
    )
    if has_terminal:
        framed.pop()

    has_please = bool(
        framed and framed[0].kind == "word" and framed[0].value.casefold() == "please"
    )
    if has_please:
        framed.pop(0)
        if framed and framed[0] == SelectToken("symbol", ","):
            framed.pop(0)
    return tuple(framed), has_please, has_terminal


def _raw_natural_words(
    value: str,
) -> tuple[tuple[str, ...], bool, bool, str] | None:
    candidate = value.strip()
    has_terminal = candidate.endswith(tuple(_TERMINAL_PUNCTUATION))
    if has_terminal:
        candidate = candidate[:-1].rstrip()
    raw_words = candidate.split()
    has_please = bool(raw_words and raw_words[0].casefold() in {"please", "please,"})
    if has_please:
        raw_words = raw_words[1:]
    if not raw_words or not all(
        _RAW_NATURAL_WORD.fullmatch(word) for word in raw_words
    ):
        return None
    select_spelling = raw_words[0]
    words = tuple(
        component.casefold()
        for word in raw_words
        for component in _RAW_WORD_COMPONENT.split(word)
    )
    if len(words) > _MAX_NATURAL_WORDS:
        return None
    return words, has_please, has_terminal, select_spelling


def _natural_words(tokens: tuple[SelectToken, ...]) -> tuple[str, ...] | None:
    words: list[str] = []
    for index, token in enumerate(tokens):
        if token.kind == "word" and _NATURAL_WORD.fullmatch(token.value):
            words.append(token.value.casefold())
            continue
        if (
            token.kind == "symbol"
            and token.value in _WORD_CONNECTORS
            and index > 0
            and index + 1 < len(tokens)
            and tokens[index - 1].kind == "word"
            and tokens[index + 1].kind == "word"
        ):
            continue
        return None
    return tuple(words) if len(words) <= _MAX_NATURAL_WORDS else None


def _valid_target(words: tuple[str, ...]) -> bool:
    target = words[1:] if words and words[0] == "all" else words
    return bool(
        1 <= len(target) <= 4
        and all(word not in _SQL_NATURAL_DISQUALIFIERS for word in target)
    )


def _valid_source_core(
    words: tuple[str, ...],
    *,
    has_please: bool,
    has_terminal: bool,
    select_spelling: str,
) -> bool:
    if len(words) < 2:
        return False
    determined = words[0] in _SOURCE_DETERMINERS
    if len(words) > (4 if determined else 3):
        return False
    for index, word in enumerate(words):
        if word == "first" and determined and index == 1:
            continue
        if word in _SQL_NATURAL_DISQUALIFIERS:
            return False
    if len(words) >= 3:
        return True
    if determined:
        return bool(
            words[0] == "the"
            and not has_please
            and has_terminal
            and select_spelling == "Select"
        )
    return has_please and has_terminal and select_spelling == "select"


def _valid_source(
    words: tuple[str, ...],
    *,
    has_please: bool,
    has_terminal: bool,
    select_spelling: str,
) -> bool:
    complement_indexes = tuple(
        index for index, word in enumerate(words) if word in _SOURCE_COMPLEMENTS
    )
    if len(complement_indexes) > 1:
        return False
    if not complement_indexes:
        return _valid_source_core(
            words,
            has_please=has_please,
            has_terminal=has_terminal,
            select_spelling=select_spelling,
        )

    complement_index = complement_indexes[0]
    core = words[:complement_index]
    complement = words[complement_index + 1 :]
    if len(core) < 3 or not 1 <= len(complement) <= 3:
        return False
    if words[complement_index] == "for" and complement[0] in _FOR_LOCK_WORDS:
        return False
    if any(word in _SQL_NATURAL_DISQUALIFIERS for word in complement):
        return False
    return _valid_source_core(
        core,
        has_please=has_please,
        has_terminal=has_terminal,
        select_spelling=select_spelling,
    )


def is_unambiguous_business_select(
    value: str,
    tokens: tuple[SelectToken, ...],
) -> bool:
    """Return true only for a complete, bounded natural SELECT instruction."""

    framed = _strip_sentence_framing(tokens)
    raw = _raw_natural_words(value)
    if framed is None or raw is None:
        return False
    body, has_please, has_terminal = framed
    select_spelling = body[0].value if body and body[0].kind == "word" else ""
    words = _natural_words(body)
    raw_words, raw_please, raw_terminal, raw_select_spelling = raw
    if (
        words is None
        or words != raw_words
        or has_please != raw_please
        or has_terminal != raw_terminal
        or select_spelling != raw_select_spelling
        or not words
        or words[0] != "select"
        or words.count("select") != 1
        or words.count("from") != 1
        or contains_embedded_word_statement(words)
    ):
        return False
    from_index = words.index("from")
    target = words[1:from_index]
    source = words[from_index + 1 :]
    return _valid_target(target) and _valid_source(
        source,
        has_please=has_please,
        has_terminal=has_terminal,
        select_spelling=select_spelling,
    )


__all__ = ("is_unambiguous_business_select",)
