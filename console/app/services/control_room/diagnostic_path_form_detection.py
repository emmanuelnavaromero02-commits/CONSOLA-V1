from __future__ import annotations

from dataclasses import dataclass

from app.services.control_room.business_copy_unicode import security_detection_forms


_MAX_ESCAPES = 128
_MAX_FORMS = 32
_MAX_ROUNDS = 2
_ESCAPE_WIDTHS = {"u": 4, "U": 8, "x": 2}
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


@dataclass(frozen=True)
class PathFormDetection:
    forms: tuple[str, ...] = ()
    escape_count: int = 0
    unsafe: bool = False


def _path_parts(value: str, start: int) -> tuple[str, ...]:
    parts: list[str] = []
    part_start = start
    index = start
    while index < len(value):
        if value[index] != "\\":
            index += 1
            continue
        parts.append(value[part_start:index])
        while index < len(value) and value[index] == "\\":
            index += 1
        part_start = index
    parts.append(value[part_start:])
    return tuple(parts)


def _supported_prefix(value: str) -> bool:
    for candidate in security_detection_forms(value):
        if not candidate or candidate[0] not in _ESCAPE_WIDTHS:
            continue
        width = _ESCAPE_WIDTHS[candidate[0]]
        digits = candidate[1 : width + 1]
        if len(digits) == width and all(digit in _HEX_DIGITS for digit in digits):
            return True
    return False


def _semantic_candidates(value: str) -> tuple[str, ...]:
    if len(value) >= 3 and value[0].isascii() and value[0].isalpha():
        leading = len(value[2:]) - len(value[2:].lstrip("\\"))
        parts = _path_parts(value, 2 + leading)
        prefix = value[:2]
    else:
        leading = len(value) - len(value.lstrip("\\"))
        parts = _path_parts(value, leading)
        prefix = "\\\\"
    candidates = [prefix + "\\" + "\\".join(parts)]
    supported = tuple(_supported_prefix(part) for part in parts)
    for index in range(1, len(parts)):
        if supported[index] and not supported[index - 1]:
            candidate = "\\" + "\\".join(parts[index:])
            if candidate not in candidates:
                candidates.append(candidate)
    return tuple(candidates)


def _decode_round(value: str) -> tuple[str, int, bool]:
    output: list[str] = []
    escape_count = 0
    index = 0
    while index < len(value):
        if value[index] != "\\" or index + 1 >= len(value):
            output.append(value[index])
            index += 1
            continue
        prefix = value[index + 1]
        if prefix not in _ESCAPE_WIDTHS:
            output.append(value[index])
            index += 1
            continue
        width = _ESCAPE_WIDTHS[prefix]
        digits_start = index + 2
        digits_end = digits_start + width
        digits = value[digits_start:digits_end]
        if len(digits) != width or any(digit not in _HEX_DIGITS for digit in digits):
            output.append(value[index])
            index += 1
            continue
        codepoint = int(digits, 16)
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            return "", escape_count, True
        escape_count += 1
        output.append(chr(codepoint))
        index = digits_end
    return "".join(output), escape_count, False


def path_form_detection(value: str) -> PathFormDetection:
    frontier = tuple(
        dict.fromkeys(
            form
            for candidate in _semantic_candidates(value)
            for form in security_detection_forms(candidate)
        )
    )
    if len(frontier) > _MAX_FORMS:
        return PathFormDetection(unsafe=True)
    discovered: list[str] = []
    escape_count = 0
    for _round in range(_MAX_ROUNDS):
        next_frontier: list[str] = []
        for candidate in frontier:
            decoded, decoded_count, unsafe = _decode_round(candidate)
            if unsafe:
                return PathFormDetection(unsafe=True)
            if not decoded_count:
                continue
            escape_count += decoded_count
            for form in security_detection_forms(decoded):
                if form not in discovered:
                    discovered.append(form)
                if form not in next_frontier:
                    next_frontier.append(form)
        if not next_frontier:
            break
        if len(discovered) > _MAX_FORMS or escape_count > _MAX_ESCAPES:
            return PathFormDetection(unsafe=True)
        frontier = tuple(next_frontier)
    for candidate in frontier:
        _, residual_count, unsafe = _decode_round(candidate)
        if unsafe or residual_count:
            return PathFormDetection(unsafe=True)
    return PathFormDetection(forms=tuple(discovered), escape_count=escape_count)


__all__ = ("PathFormDetection", "path_form_detection")
