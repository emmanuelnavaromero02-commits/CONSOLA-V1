from __future__ import annotations

from dataclasses import dataclass


_MAX_INPUT_LENGTH = 16_384
_MAX_ESCAPES = 128
_MAX_FORMS = 32
_MAX_ROUNDS = 2
_MAX_ASSIGNMENTS = 128
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_ESCAPE_WIDTHS = {"u": 4, "U": 8, "x": 2}
_JSON_ESCAPES = {
    '"': '"',
    "/": "/",
    "\\": "\\",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


@dataclass(frozen=True)
class FieldEscapeDetection:
    forms: tuple[str, ...] = ()
    unsafe: bool = False


@dataclass(frozen=True)
class AssignmentFieldDetection:
    fields: tuple[str, ...] = ()
    unsafe: bool = False


def _scalar_is_valid(digits: str, width: int) -> bool:
    if len(digits) != width or any(digit not in _HEX_DIGITS for digit in digits):
        return False
    codepoint = int(digits, 16)
    return codepoint <= 0x10FFFF and not 0xD800 <= codepoint <= 0xDFFF


def _append_form(forms: list[str], candidate: str, original: str) -> None:
    if candidate != original and candidate not in forms and len(forms) < _MAX_FORMS:
        forms.append(candidate)


def _round_forms(value: str) -> FieldEscapeDetection:
    output: list[str] = []
    escape_count = 0
    changed = False
    index = 0
    while index < len(value):
        if value[index] != "\\":
            output.append(value[index])
            index += 1
            continue
        escape_count += 1
        if escape_count > _MAX_ESCAPES:
            return FieldEscapeDetection(unsafe=True)
        if index + 1 >= len(value):
            return FieldEscapeDetection(unsafe=True)

        prefix = value[index + 1]
        if prefix in _JSON_ESCAPES:
            output.append(_JSON_ESCAPES[prefix])
            changed = True
            index += 2
            continue

        if prefix not in _ESCAPE_WIDTHS:
            return FieldEscapeDetection(unsafe=True)
        width = _ESCAPE_WIDTHS[prefix]
        digits_start = index + 2
        digits_end = digits_start + width
        digits = value[digits_start:digits_end]
        if not _scalar_is_valid(digits, width):
            return FieldEscapeDetection(unsafe=True)
        output.append(value[index:digits_end])
        index = digits_end
    decoded = "".join(output)
    return FieldEscapeDetection(
        forms=(decoded,) if changed and decoded != value else ()
    )


def malformed_field_escape_detection(value: str) -> FieldEscapeDetection:

    if len(value) > _MAX_INPUT_LENGTH:
        return FieldEscapeDetection(unsafe=True)
    if "\\" not in value:
        return FieldEscapeDetection()

    discovered: list[str] = []
    frontier = (value,)
    for _round in range(_MAX_ROUNDS):
        next_frontier: list[str] = []
        for candidate in frontier:
            detection = _round_forms(candidate)
            if detection.unsafe:
                return FieldEscapeDetection(unsafe=True)
            for form in detection.forms:
                _append_form(discovered, form, value)
                _append_form(next_frontier, form, candidate)
        frontier = tuple(next_frontier)
        if not frontier:
            break
    if frontier and any("\\" in candidate for candidate in frontier):
        return FieldEscapeDetection(unsafe=True)
    return FieldEscapeDetection(forms=tuple(discovered))


def _field_before_separator(value: str, separator: int) -> str:
    end = separator
    while end > 0 and value[end - 1].isspace():
        end -= 1
    if end == 0:
        return ""
    if value[end - 1] in {'"', "'"}:
        quote = value[end - 1]
        opening = value.rfind(quote, 0, end - 1)
        return value[opening + 1 : end - 1] if opening >= 0 else ""
    start = end
    while start > 0:
        character = value[start - 1]
        if character.isspace() or character in "{}[](),;'\"=:/":
            break
        start -= 1
    return value[start:end]


def assignment_field_detection(value: str) -> AssignmentFieldDetection:

    if len(value) > _MAX_INPUT_LENGTH:
        return AssignmentFieldDetection(unsafe=True)
    if "\\" not in value:
        return AssignmentFieldDetection()
    fields: list[str] = []
    assignments = 0
    separators = [index for index, character in enumerate(value) if character in "=:"]
    separators.extend(
        index
        for index in range(len(value) - 3)
        if value[index : index + 4].casefold() == " is "
    )
    for index in sorted(set(separators)):
        assignments += 1
        if assignments > _MAX_ASSIGNMENTS:
            return AssignmentFieldDetection(unsafe=True)
        field = _field_before_separator(value, index)
        if "\\" in field and field not in fields:
            fields.append(field)
    return AssignmentFieldDetection(fields=tuple(fields))


__all__ = (
    "FieldEscapeDetection",
    "AssignmentFieldDetection",
    "assignment_field_detection",
    "malformed_field_escape_detection",
)
