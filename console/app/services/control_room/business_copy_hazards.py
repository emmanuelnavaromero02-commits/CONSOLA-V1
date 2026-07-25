from __future__ import annotations

import ast
import json
import re
import unicodedata
from collections.abc import Mapping

from app.services.control_room.business_copy_unicode import (
    security_detection_forms,
    security_skeleton,
)
from app.services.control_room.business_diagnostic_grammar import (
    is_diagnostic_state_copy,
    is_spanish_permission_diagnostic,
)

_UNSAFE_BIDI = frozenset(
    {"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"}
)
_DIAGNOSTIC_ONLY = frozenset(
    {
        "blocked",
        "cartucho inactivo o bloqueado",
        "cartucho sin permiso para este usuario",
        "dataset con contrato invalido",
        "dataset no materializado",
        "dataset requerido no registrado",
        "dataset sin materializar",
        "datos no disponibles",
        "empty",
        "error",
        "faltan datos",
        "fuente operativa no disponible",
        "fuente parcial no apta para operacion completa",
        "fuente requiere atencion",
        "fuente sin datos materializados",
        "fuente stub no apta para decisiones operativas",
        "insufficient data",
        "invalid schema",
        "materialization pending",
        "missing",
        "n d",
        "nd",
        "no disponible",
        "no permission",
        "schema only",
        "sin datos",
        "sin permiso",
        "source state",
        "source unavailable",
        "stub",
        "technical diagnostic",
        "unavailable",
    }
)
_DIAGNOSTIC_SIGNATURES = tuple(
    re.compile(pattern)
    for pattern in (
        r"^dataset(?: [a-z0-9]+){0,4} "
        r"(?:missing|blocked|stub|error|empty|unavailable|no permission|"
        r"schema only|invalid schema|insufficient data|no materializado|"
        r"sin materializar)(?: |$)",
        r"^error(?: http)? [1-5][0-9]{2}(?: |$)",
        r"^blocked(?: |$)",
        r"^error source unavailable(?: [1-5][0-9]{2})?(?: |$)",
        r"^(?:access|permission) denied(?: |$)",
        r"^(?:sin datos(?: disponibles?)?|no hay datos|faltan datos)(?: |$)",
        r"^source unavailable(?: |$)",
    )
)
_TECHNICAL_PREFIX = re.compile(
    r"(?i)^\s*"
    r"(?:technical[ _]diagnostic|diagnostic(?:[ _](?:message|code))?|"
    r"error(?:[ _](?:message|code))?|result\.code)"
    r"\s*[:=]\s*\S"
)
_INCOMPLETE_STRUCTURED = re.compile(
    r"""(?ix)
    (?:
        \{\s*(?:["'][^"'\n]{0,80}["']|[a-z_][a-z0-9_.-]{0,79})\s*:
        |
        \[\s*(?:["']|[\[{]|[-+]?(?:\d|\.\d)|true\b|false\b|null\b|none\b)
    )
    """
)
_MAX_STRUCTURED_STARTS = 16
_JSON_DECODER = json.JSONDecoder()


def _diagnostic_form(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", security_skeleton(value).casefold()))


def contains_unsafe_unicode(value: str) -> bool:
    return any(
        unicodedata.category(character) in {"Cc", "Cf"}
        or unicodedata.bidirectional(character) in _UNSAFE_BIDI
        for character in value
    )


def is_diagnostic_copy(value: str) -> bool:
    for candidate in security_detection_forms(value):
        comparison = candidate.casefold()
        if (
            is_diagnostic_state_copy(candidate)
            or is_spanish_permission_diagnostic(candidate)
            or _TECHNICAL_PREFIX.search(comparison)
        ):
            return True
        diagnostic = _diagnostic_form(candidate)
        if diagnostic in _DIAGNOSTIC_ONLY or any(
            signature.match(diagnostic) for signature in _DIAGNOSTIC_SIGNATURES
        ):
            return True
    return False


def _balanced_candidate(value: str, start: int) -> str | None:
    expected = {"{": "}", "[": "]"}
    stack: list[str] = []
    quote = ""
    escaped = False
    for index, character in enumerate(value[start:], start=start):
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in expected:
            stack.append(expected[character])
        elif character in {"]", "}"}:
            if not stack or stack.pop() != character:
                return None
            if not stack:
                return value[start : index + 1]
    return None


def _structured_value(candidate: str) -> bool:
    try:
        parsed, _end = _JSON_DECODER.raw_decode(candidate)
    except (RecursionError, ValueError):
        try:
            parsed = ast.literal_eval(candidate)
        except (MemoryError, RecursionError, SyntaxError, ValueError):
            return False
    return isinstance(parsed, (Mapping, list))


def _contains_structured_form(value: str) -> bool:
    if _INCOMPLETE_STRUCTURED.search(value):
        return True
    starts = [index for index, character in enumerate(value) if character in {"{", "["}]
    if len(starts) > _MAX_STRUCTURED_STARTS:
        return True
    for start in starts:
        suffix = value[start:]
        try:
            parsed, _end = _JSON_DECODER.raw_decode(suffix)
        except (RecursionError, ValueError):
            parsed = None
        if isinstance(parsed, (Mapping, list)):
            return True
        candidate = _balanced_candidate(value, start)
        if candidate and _structured_value(candidate):
            return True
    return False


def contains_structured_copy(value: str) -> bool:
    return any(
        _contains_structured_form(candidate)
        for candidate in security_detection_forms(value)
    )


__all__ = (
    "contains_structured_copy",
    "contains_unsafe_unicode",
    "is_diagnostic_copy",
)
