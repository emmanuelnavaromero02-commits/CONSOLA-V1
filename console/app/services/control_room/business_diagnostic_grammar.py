from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.control_room.business_copy_unicode import security_skeleton


_MAX_GRAMMAR_LENGTH = 8192
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
_STATE_VALUES = (
    ("ready",),
    ("degraded",),
    ("ok",),
    ("missing",),
    ("blocked",),
    ("stub",),
    ("error",),
    ("empty",),
    ("schema", "only"),
    ("unavailable",),
    ("invalid", "schema"),
    ("insufficient", "data"),
    ("no", "permission"),
)
_PAID_LEAVE_PREFIX = ("sin", "permiso", "retribuido")
_PAID_LEAVE_METRICS = frozenset({"ausencia", "ausencias"})


@dataclass(frozen=True)
class _Token:
    value: str
    start: int
    end: int


def _tokens(value: str) -> tuple[str, tuple[_Token, ...]]:
    normalized = security_skeleton(value[:_MAX_GRAMMAR_LENGTH]).casefold()
    matches = tuple(_WORD.finditer(normalized))
    tokens = tuple(
        _Token(match.group(), match.start(), match.end())
        for match in matches[:_MAX_GRAMMAR_TOKENS]
    )
    return normalized, tokens


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


def is_diagnostic_state_copy(value: str) -> bool:
    normalized, tokens = _tokens(value)
    for start in range(len(tokens)):
        for field in _STATE_FIELDS:
            if not _matches(normalized, tokens, start, field):
                continue
            value_index = start + len(field)
            if value_index >= len(tokens):
                continue
            assignment = normalized[
                tokens[value_index - 1].end : tokens[value_index].start
            ]
            if not _supported_separator(assignment):
                continue
            if ":" in assignment or "=" in assignment:
                return True
            for state in _STATE_VALUES:
                if _matches(normalized, tokens, start, field + state):
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
    normalized, tokens = _tokens(value)
    if not _matches(normalized, tokens, 0, ("sin", "permiso")):
        return False
    return not _is_paid_leave_metric(normalized, tokens)


__all__ = (
    "is_diagnostic_state_copy",
    "is_spanish_permission_diagnostic",
)
