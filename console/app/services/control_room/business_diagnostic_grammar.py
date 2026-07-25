from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import islice

from app.services.control_room.business_copy_detection import (
    MAX_VISIBLE_COPY_SCAN_LENGTH,
    canonicalize_detection_separators,
)


_MAX_GRAMMAR_TOKENS = 64
_WORD = re.compile(r"[^\W_]+")
_SEPARATOR_CHARACTERS = frozenset("-_:=")
_STATE_FIELDS = (
    ("status",),
    ("state",),
    ("data", "status"),
    ("data", "state"),
    ("readiness", "status"),
    ("readiness", "state"),
    ("source", "status"),
    ("source", "state"),
)
_PAID_LEAVE_PREFIX = ("sin", "permiso", "retribuido")
_PAID_LEAVE_METRICS = frozenset({"ausencia", "ausencias"})


@dataclass(frozen=True)
class _Token:
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class _TokenStream:
    normalized: str
    tokens: tuple[_Token, ...]
    overflow: bool


def _tokens(value: str) -> _TokenStream:
    if len(value) > MAX_VISIBLE_COPY_SCAN_LENGTH:
        return _TokenStream("", (), True)
    normalized = canonicalize_detection_separators(value).casefold()
    matches = tuple(islice(_WORD.finditer(normalized), _MAX_GRAMMAR_TOKENS + 1))
    tokens = tuple(
        _Token(match.group(), match.start(), match.end())
        for match in matches[:_MAX_GRAMMAR_TOKENS]
    )
    return _TokenStream(
        normalized=normalized,
        tokens=tokens,
        overflow=len(matches) > _MAX_GRAMMAR_TOKENS,
    )


def _supported_separator(value: str) -> bool:
    return bool(value) and all(
        character.isspace() or character in _SEPARATOR_CHARACTERS for character in value
    )


def _matches(
    normalized: str,
    tokens: tuple[_Token, ...],
    start: int,
    production: tuple[str, ...],
) -> bool:
    selected = tokens[start : start + len(production)]
    if len(selected) != len(production):
        return False
    for index, (token, expected) in enumerate(zip(selected, production, strict=True)):
        if token.value != expected:
            return False
        if index and not _supported_separator(
            normalized[selected[index - 1].end : token.start]
        ):
            return False
    return True


def _has_nonempty_assignment(value: str, start: int) -> bool:
    cursor = start
    while cursor < len(value) and value[cursor].isspace():
        cursor += 1
    has_whitespace_separator = cursor > start
    if cursor < len(value) and value[cursor] in _SEPARATOR_CHARACTERS:
        cursor += 1
    elif not has_whitespace_separator:
        return False
    while cursor < len(value) and value[cursor].isspace():
        cursor += 1
    return cursor < len(value)


def is_diagnostic_state_copy(value: str) -> bool:
    stream = _tokens(value)
    if stream.overflow:
        return True
    normalized, tokens = stream.normalized, stream.tokens
    for start in range(len(tokens)):
        for field in _STATE_FIELDS:
            if not _matches(normalized, tokens, start, field):
                continue
            field_end = tokens[start + len(field) - 1].end
            if _has_nonempty_assignment(normalized, field_end):
                return True
    return False


def _is_paid_leave_metric(
    normalized: str,
    tokens: tuple[_Token, ...],
) -> bool:
    if len(tokens) != 5 or not _matches(
        normalized,
        tokens,
        0,
        _PAID_LEAVE_PREFIX,
    ):
        return False
    return (
        tokens[3].value.isdecimal()
        and tokens[4].value in _PAID_LEAVE_METRICS
        and _supported_separator(normalized[tokens[2].end : tokens[3].start])
        and _supported_separator(normalized[tokens[3].end : tokens[4].start])
    )


def is_spanish_permission_diagnostic(value: str) -> bool:
    stream = _tokens(value)
    if stream.overflow:
        return True
    normalized, tokens = stream.normalized, stream.tokens
    if not _matches(normalized, tokens, 0, ("sin", "permiso")):
        return False
    return not _is_paid_leave_metric(normalized, tokens)


__all__ = (
    "is_diagnostic_state_copy",
    "is_spanish_permission_diagnostic",
)
