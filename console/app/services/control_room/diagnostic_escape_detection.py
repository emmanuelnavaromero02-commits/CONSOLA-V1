from __future__ import annotations

from dataclasses import dataclass

from app.services.control_room.business_copy_unicode import security_detection_forms


_MAX_INPUT_LENGTH = 16_384
_MAX_ESCAPES = 128
_MAX_ROUNDS = 2
_MAX_DETECTION_FORMS = 32
_MAX_STRUCTURED_STARTS = 16
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
class EscapeDetection:
    forms: tuple[str, ...] = ()
    unsafe: bool = False


def _contains_supported_prefix(value: str) -> bool:
    return any(f"\\{prefix}" in value for prefix in _ESCAPE_WIDTHS)


def _plausible_structured_start(value: str, start: int) -> bool:
    tail = value[start + 1 :].lstrip()
    if not tail:
        return True
    if value[start] == "{":
        if tail[0] in {'"', "'", "{", "}", "\\"}:
            return True
        if len(tail) >= 3 and tail[0].isalpha() and tail[1:3] == ":\\":
            return False
        closing = value.find("}", start + 1)
        segment = value[start + 1 : closing if closing >= 0 else len(value)]
        return tail[0].isidentifier() and ":" in segment
    return (
        tail[0] in {'"', "'", "[", "]", "{", "}", "\\", "-", "+", "t", "f", "n"}
        or tail[0].isdigit()
    )


def _quoted_assignment_ranges(
    value: str,
    occupied: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    if ":" not in value or ('"' not in value and "'" not in value):
        return ()
    assignments: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        quote = value[index]
        if quote not in {'"', "'"} or any(
            start <= index < end for start, end in occupied
        ):
            index += 1
            continue
        escaped = False
        closing = index + 1
        while closing < len(value):
            character = value[closing]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                break
            closing += 1
        if closing >= len(value):
            break
        cursor = closing + 1
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        key = value[index + 1 : closing]
        windows_path = (
            len(key) >= 3 and key[0].isalpha() and key[1:3] == ":\\"
        ) or key.startswith("\\\\")
        if cursor < len(value) and value[cursor] == ":" and not windows_path:
            assignments.append((index, cursor + 1))
        index = closing + 1
    return tuple(assignments)


def _structured_ranges(value: str) -> tuple[tuple[tuple[int, int], ...], bool]:
    ranges: list[tuple[int, int]] = []
    starts = 0
    for start, character in enumerate(value):
        if character not in "{[" or not _plausible_structured_start(value, start):
            continue
        starts += 1
        if starts > _MAX_STRUCTURED_STARTS:
            return (), True
        expected = {"{": "}", "[": "]"}
        stack: list[str] = []
        quote = ""
        escaped = False
        end = len(value)
        for index in range(start, len(value)):
            current = value[index]
            if quote:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    quote = ""
                continue
            if current in {'"', "'"}:
                quote = current
            elif current in expected:
                stack.append(expected[current])
            elif current in "]}":
                if not stack or stack.pop() != current:
                    break
                if not stack:
                    end = index + 1
                    break
        ranges.append((start, end))
    occupied = tuple(ranges)
    ranges.extend(_quoted_assignment_ranges(value, occupied))
    if len(ranges) > _MAX_STRUCTURED_STARTS:
        return (), True
    return tuple(ranges), False


def _strict_escape(index: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= index < end for start, end in ranges)


def _decode_round(value: str) -> tuple[str, int, bool]:
    structured_ranges, excessive_structures = _structured_ranges(value)
    if excessive_structures:
        return "", 0, True
    output: list[str] = []
    escape_count = 0
    supported_count = 0
    unsafe = False
    index = 0
    while index < len(value):
        character = value[index]
        if character != "\\":
            output.append(character)
            index += 1
            continue
        strict = _strict_escape(index, structured_ranges)
        if index + 1 >= len(value):
            if strict:
                unsafe = True
            output.append(character)
            index += 1
            continue

        prefix = value[index + 1]
        if prefix in _JSON_ESCAPES and strict:
            escape_count += 1
            if escape_count > _MAX_ESCAPES:
                return "", escape_count, True
            output.append(_JSON_ESCAPES[prefix])
            index += 2
            continue
        if prefix not in _ESCAPE_WIDTHS:
            if strict:
                unsafe = True
            output.append(character)
            index += 1
            continue

        width = _ESCAPE_WIDTHS[prefix]
        digits_start = index + 2
        digits_end = digits_start + width
        digits = value[digits_start:digits_end]
        if len(digits) != width or any(digit not in _HEX_DIGITS for digit in digits):
            if strict:
                return "", escape_count, True
            output.append(character)
            index += 1
            continue

        codepoint = int(digits, 16)
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            if strict:
                return "", escape_count, True
            output.append(character)
            index += 1
            continue

        escape_count += 1
        supported_count += 1
        if escape_count > _MAX_ESCAPES:
            return "", escape_count, True
        if index > 0 and value[index - 1] == "\\":
            unsafe = True
        if codepoint == 0x5C or (
            codepoint > 0x7F
            and any(
                "\\" in candidate
                for candidate in security_detection_forms(chr(codepoint))
            )
        ):
            unsafe = True
        output.append(chr(codepoint))
        index = digits_end
    decoded = "".join(output)
    if supported_count and any(
        "\\" in candidate for candidate in security_detection_forms(decoded)
    ):
        unsafe = True
    return decoded, escape_count, unsafe


def _escaped_literal_detection(value: str) -> EscapeDetection:
    if len(value) > _MAX_INPUT_LENGTH:
        return EscapeDetection(unsafe=True)
    structured_ranges, excessive_structures = _structured_ranges(value)
    has_structured_escape = any(
        "\\" in value[start:end] for start, end in structured_ranges
    )
    if excessive_structures and "\\" in value:
        return EscapeDetection(unsafe=True)
    if not _contains_supported_prefix(value) and not has_structured_escape:
        return EscapeDetection()

    current = value
    decoded_forms: list[str] = []
    nested = False
    for round_index in range(_MAX_ROUNDS):
        decoded, escape_count, unsafe = _decode_round(current)
        if unsafe:
            return EscapeDetection(unsafe=True)
        if escape_count == 0:
            break
        decoded_forms.append(decoded)
        current = decoded
        if _contains_supported_prefix(current):
            nested = True
            if round_index + 1 == _MAX_ROUNDS:
                return EscapeDetection(unsafe=True)
            continue
        break

    return EscapeDetection(
        forms=tuple(dict.fromkeys(decoded_forms)),
        unsafe=nested,
    )


def escaped_security_detection(value: str) -> EscapeDetection:

    if len(value) > _MAX_INPUT_LENGTH:
        return EscapeDetection(unsafe=True)
    candidates = security_detection_forms(value)
    if len(candidates) > _MAX_DETECTION_FORMS:
        return EscapeDetection(unsafe=True)

    decoded_forms: list[str] = []
    for candidate in candidates:
        detection = _escaped_literal_detection(candidate)
        if detection.unsafe:
            return EscapeDetection(unsafe=True)
        decoded_forms.extend(detection.forms)
    unique_decoded = tuple(dict.fromkeys(decoded_forms))

    normalized_decoded_count = 0
    for decoded in unique_decoded:
        normalized_forms = security_detection_forms(decoded)
        normalized_decoded_count += len(normalized_forms)
        if normalized_decoded_count > _MAX_DETECTION_FORMS:
            return EscapeDetection(unsafe=True)
        if any(len(candidate) > _MAX_INPUT_LENGTH for candidate in normalized_forms):
            return EscapeDetection(unsafe=True)
    return EscapeDetection(forms=unique_decoded)


__all__ = ("EscapeDetection", "escaped_security_detection")
