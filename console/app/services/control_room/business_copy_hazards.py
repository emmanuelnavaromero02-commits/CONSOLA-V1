from __future__ import annotations

import ast
import json
import re
import unicodedata
from collections.abc import Mapping


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
        r"dataset(?: [a-z0-9]+){0,4} (?:no materializado|sin materializar)"
        r"(?: ahora| todavia| pendiente)?",
        r"error(?: http)? [1-5][0-9]{2}",
        r"error source unavailable(?: [1-5][0-9]{2})?",
        r"(?:access|permission) denied",
        r"(?:sin datos(?: disponibles?)?|no hay datos)(?: ahora| todavia)?",
        r"faltan datos(?: (?:ahora|todavia|de(?: [a-z0-9]+){1,3}))?",
        r"source unavailable(?: now| [1-5][0-9]{2})?",
        r"source state (?:missing|blocked|stub|empty|unavailable|error)"
        r"(?: refresh)? pending",
        r"(?:status|data status|readiness status|source status) "
        r"(?:missing|blocked|stub|error|empty|schema only|unavailable|"
        r"invalid schema|insufficient data)",
    )
)
_STATUS_ASSIGNMENT = re.compile(
    r"(?i)(?<![\w])"
    r"(?:status|data[_ ]status|readiness[_ ]status|source[_ ]status|source[_ ]state)"
    r"\s*=\s*[a-z0-9_.-]+(?![\w])"
)
_TECHNICAL_PREFIX = re.compile(
    r"(?i)^\s*"
    r"(?:technical[ _]diagnostic|diagnostic(?:[ _](?:message|code))?|"
    r"error(?:[ _](?:message|code))?|result\.code)"
    r"\s*[:=]\s*\S"
)
_INCOMPLETE_STRUCTURED = re.compile(
    r"""(?x)
    (?:
        \{\s*["'][^"'\n]{1,80}["']\s*:
        |
        \[\s*(?:["']|[\[{])
    )
    """
)
_MAX_STRUCTURED_STARTS = 16
_JSON_DECODER = json.JSONDecoder()


def _without_marks(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.category(character).startswith("M")
    )


def _diagnostic_form(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", _without_marks(value).casefold()))


def contains_unsafe_unicode(value: str) -> bool:
    return any(
        unicodedata.category(character) in {"Cc", "Cf"}
        or unicodedata.bidirectional(character) in _UNSAFE_BIDI
        for character in value
    )


def is_diagnostic_copy(value: str) -> bool:
    comparison = _without_marks(unicodedata.normalize("NFKC", value)).casefold()
    if _STATUS_ASSIGNMENT.search(comparison) or _TECHNICAL_PREFIX.search(comparison):
        return True
    diagnostic = _diagnostic_form(value)
    return diagnostic in _DIAGNOSTIC_ONLY or any(
        signature.fullmatch(diagnostic) for signature in _DIAGNOSTIC_SIGNATURES
    )


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


def contains_structured_copy(value: str) -> bool:
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


__all__ = (
    "contains_structured_copy",
    "contains_unsafe_unicode",
    "is_diagnostic_copy",
)
