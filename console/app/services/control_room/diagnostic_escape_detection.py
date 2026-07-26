from __future__ import annotations

from dataclasses import dataclass

from app.services.control_room.business_copy_unicode import security_detection_forms


_MAX_INPUT_LENGTH = 16_384
_MAX_ESCAPES = 128
_MAX_ROUNDS = 2
_MAX_DETECTION_FORMS = 32
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_ESCAPE_WIDTHS = {"u": 4, "U": 8, "x": 2}


@dataclass(frozen=True)
class EscapeDetection:
    forms: tuple[str, ...] = ()
    unsafe: bool = False


def _contains_supported_prefix(value: str) -> bool:
    return any(f"\\{prefix}" in value for prefix in _ESCAPE_WIDTHS)


def _decode_round(value: str) -> tuple[str, int, bool]:
    output: list[str] = []
    escape_count = 0
    unsafe = False
    index = 0
    while index < len(value):
        character = value[index]
        if character != "\\":
            output.append(character)
            index += 1
            continue
        if index + 1 >= len(value) or value[index + 1] not in _ESCAPE_WIDTHS:
            unsafe = True
            output.append(character)
            index += 1
            continue

        prefix = value[index + 1]
        width = _ESCAPE_WIDTHS[prefix]
        digits_start = index + 2
        digits_end = digits_start + width
        digits = value[digits_start:digits_end]
        if len(digits) != width or any(digit not in _HEX_DIGITS for digit in digits):
            return "", escape_count, True

        codepoint = int(digits, 16)
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            return "", escape_count, True

        escape_count += 1
        if escape_count > _MAX_ESCAPES:
            return "", escape_count, True
        if index > 0 and value[index - 1] == "\\":
            unsafe = True
        if codepoint == 0x5C:
            unsafe = True
        output.append(chr(codepoint))
        index = digits_end
    return "".join(output), escape_count, unsafe


def _escaped_literal_detection(value: str) -> EscapeDetection:
    if len(value) > _MAX_INPUT_LENGTH:
        return EscapeDetection(unsafe=True)
    if not _contains_supported_prefix(value):
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
    """Return bounded forms after Unicode-aware, detection-only escape handling."""

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
        if any(
            len(candidate) > _MAX_INPUT_LENGTH or "\\" in candidate
            for candidate in normalized_forms
        ):
            return EscapeDetection(unsafe=True)
    return EscapeDetection(forms=unique_decoded)


__all__ = ("EscapeDetection", "escaped_security_detection")
