"""Positive grammar for candidate-selection business instructions."""

from __future__ import annotations

import re

from app.services.public_sql_select_lexer import SelectToken
from app.services.public_sql_word_statements import (
    contains_embedded_word_statement,
)


_NATURAL_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_RAW_NATURAL_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", re.UNICODE)
_RAW_WORD_COMPONENT = re.compile(r"([-'’])")
_WORD_CONNECTORS = frozenset({"-", "'", "’"})
_TERMINAL_PUNCTUATION = frozenset({".", "!", "?"})

_TARGET_DETERMINERS = frozenset({"all", "the"})
_TARGET_MODIFIER = ("high", "-", "potential")
_SOURCE_DETERMINERS = frozenset({"a", "an", "the", "your"})
_SOURCE_MODIFIERS = frozenset({"available", "first", "internal"})
_SOURCE_HEADS = frozenset(
    {
        ("employees",),
        ("shortlist",),
        ("talent", "pool"),
        ("talent", "pools"),
    }
)
_SOURCE_POSSESSORS = frozenset({"company"})
_COMPLEMENT_PREPOSITIONS = frozenset({"for", "in"})
_PROJECT_PHASES = frozenset({"start", "end"})
_RELEASE_OUTCOMES = frozenset({"readiness"})
_REVIEW_OUTCOMES = frozenset({"review"})
_BUSINESS_LOCATIONS = frozenset({"madrid"})


def _strip_token_framing(tokens: tuple[SelectToken, ...]) -> tuple[SelectToken, ...]:
    framed = list(tokens)
    if (
        framed
        and framed[-1].kind == "symbol"
        and framed[-1].value in _TERMINAL_PUNCTUATION
    ):
        framed.pop()
    if framed and framed[0].kind == "word" and framed[0].value.casefold() == "please":
        framed.pop(0)
        if framed and framed[0] == SelectToken("symbol", ","):
            framed.pop(0)
    return tuple(framed)


def _raw_natural_atoms(value: str) -> tuple[str, ...] | None:
    candidate = value.strip()
    if candidate and candidate[-1] in _TERMINAL_PUNCTUATION:
        candidate = candidate[:-1].rstrip()
    raw_words = candidate.split()
    if raw_words and raw_words[0].casefold() in {"please", "please,"}:
        raw_words = raw_words[1:]
    if not raw_words or not all(
        _RAW_NATURAL_WORD.fullmatch(word) for word in raw_words
    ):
        return None
    return tuple(
        component if component in _WORD_CONNECTORS else component.casefold()
        for word in raw_words
        for component in _RAW_WORD_COMPONENT.split(word)
        if component
    )


def _token_natural_atoms(tokens: tuple[SelectToken, ...]) -> tuple[str, ...] | None:
    atoms: list[str] = []
    for index, token in enumerate(tokens):
        if token.kind == "word" and _NATURAL_WORD.fullmatch(token.value):
            atoms.append(token.value.casefold())
            continue
        if (
            token.kind == "symbol"
            and token.value in _WORD_CONNECTORS
            and index > 0
            and index + 1 < len(tokens)
            and tokens[index - 1].kind == "word"
            and tokens[index + 1].kind == "word"
        ):
            atoms.append(token.value)
            continue
        return None
    return tuple(atoms)


def _framed_atoms(
    value: str,
    tokens: tuple[SelectToken, ...],
) -> tuple[str, ...] | None:
    token_atoms = _token_natural_atoms(_strip_token_framing(tokens))
    raw_atoms = _raw_natural_atoms(value)
    return token_atoms if token_atoms is not None and token_atoms == raw_atoms else None


def _valid_action(atoms: tuple[str, ...]) -> bool:
    return bool(
        atoms
        and atoms[0] == "select"
        and atoms.count("select") == 1
        and atoms.count("from") == 1
    )


def _valid_target(atoms: tuple[str, ...]) -> bool:
    cursor = 0
    if cursor < len(atoms) and atoms[cursor] in _TARGET_DETERMINERS:
        cursor += 1
    if atoms[cursor : cursor + 3] == _TARGET_MODIFIER:
        cursor += 3
    return atoms[cursor:] == ("candidates",)


def _valid_source(atoms: tuple[str, ...]) -> bool:
    cursor = 0
    determiner: str | None = None
    if cursor < len(atoms) and atoms[cursor] in _SOURCE_DETERMINERS:
        determiner = atoms[cursor]
        cursor += 1

    possessive = atoms[cursor:]
    if (
        len(possessive) == 4
        and possessive[0] in _SOURCE_POSSESSORS
        and possessive[1] in {"'", "’"}
        and possessive[2:] == ("s", "pool")
    ):
        return determiner in {None, "the"}

    modifiers: list[str] = []
    while cursor < len(atoms) and atoms[cursor] in _SOURCE_MODIFIERS:
        modifier = atoms[cursor]
        if modifier in modifiers:
            return False
        modifiers.append(modifier)
        cursor += 1
    head = atoms[cursor:]
    if head not in _SOURCE_HEADS:
        return False
    source_modifiers = tuple(modifiers)
    if head == ("employees",):
        return determiner is None and source_modifiers == ("available",)
    if head == ("shortlist",):
        return determiner in {"the", "your"} and not source_modifiers
    if head == ("talent", "pools"):
        return determiner is None and source_modifiers == ("available",)
    if determiner in {"a", "your"}:
        return not source_modifiers
    if determiner == "an":
        return source_modifiers == ("internal",)
    return determiner == "the" and source_modifiers in {(), ("first",)}


def _valid_complement(
    words: tuple[str, ...],
    *,
    absolute_offset: int,
) -> frozenset[int] | None:
    if not words:
        return frozenset()
    preposition, *components = words
    if preposition == "in":
        return (
            frozenset()
            if len(components) == 1 and components[0] in _BUSINESS_LOCATIONS
            else None
        )
    if preposition != "for" or not components:
        return None

    role, *details = components
    if role in _REVIEW_OUTCOMES and not details:
        return frozenset()
    if role == "project" and len(details) == 1 and details[0] in _PROJECT_PHASES:
        return frozenset({absolute_offset + 2})
    if role == "release" and len(details) == 1 and details[0] in _RELEASE_OUTCOMES:
        return frozenset({absolute_offset + 1})
    return None


def _parse_business_roles(atoms: tuple[str, ...]) -> frozenset[int] | None:
    if not _valid_action(atoms):
        return None
    from_index = atoms.index("from")
    if not _valid_target(atoms[1:from_index]):
        return None

    tail = atoms[from_index + 1 :]
    complement_indexes = tuple(
        index for index, word in enumerate(tail) if word in _COMPLEMENT_PREPOSITIONS
    )
    if len(complement_indexes) > 1:
        return None
    complement_index = complement_indexes[0] if complement_indexes else len(tail)
    source = tail[:complement_index]
    complement = tail[complement_index:]
    if not _valid_source(source):
        return None
    return _valid_complement(
        complement,
        absolute_offset=from_index + 1 + complement_index,
    )


def is_unambiguous_business_select(
    value: str,
    tokens: tuple[SelectToken, ...],
) -> bool:
    """Return true only after a full candidate-selection grammar and SQL scan."""

    atoms = _framed_atoms(value, tokens)
    if atoms is None:
        return False
    semantic_statement_starters = _parse_business_roles(atoms)
    ignored_atom_starters = (
        semantic_statement_starters
        if semantic_statement_starters is not None
        else frozenset()
    )
    word_atom_indexes = tuple(
        index for index, atom in enumerate(atoms) if atom not in _WORD_CONNECTORS
    )
    words = tuple(atoms[index] for index in word_atom_indexes)
    ignored_starters = frozenset(
        word_index
        for word_index, atom_index in enumerate(word_atom_indexes)
        if atom_index in ignored_atom_starters
    )
    if contains_embedded_word_statement(
        words,
        ignored_starter_indexes=ignored_starters,
    ):
        return False
    return semantic_statement_starters is not None


__all__ = ("is_unambiguous_business_select",)
