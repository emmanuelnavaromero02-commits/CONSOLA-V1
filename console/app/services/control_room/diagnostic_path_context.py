from __future__ import annotations

from dataclasses import dataclass

from app.services.control_room.diagnostic_path_form_detection import (
    path_form_detection,
)


_MAX_INPUT_LENGTH = 16_384
_MAX_PATH_FIELDS = 16
_MAX_PATH_LENGTH = 4_096
_MAX_PATH_ESCAPES = 128
_MAX_PATH_FORM_ESCAPES = 128
_MAX_PATH_FORMS = 32
_FORBIDDEN_PATH_CHARACTERS = frozenset("\"'{}[];,=")


@dataclass(frozen=True)
class StructuredPathDetection:
    masked_value: str
    forms: tuple[str, ...] = ()
    unsafe: bool = False


def _quoted_end(value: str, opening: int) -> int:
    quote = value[opening]
    slash_run = 0
    for index in range(opening + 1, len(value)):
        character = value[index]
        if character == "\\":
            slash_run += 1
            continue
        if character == quote and slash_run % 2 == 0:
            return index
        slash_run = 0
    return -1


def _path_parts(value: str, start: int) -> tuple[tuple[str, ...], tuple[int, ...]]:
    parts: list[str] = []
    runs: list[int] = []
    part_start = start
    index = start
    while index < len(value):
        if value[index] != "\\":
            index += 1
            continue
        parts.append(value[part_start:index])
        run_end = index + 1
        while run_end < len(value) and value[run_end] == "\\":
            run_end += 1
        runs.append(run_end - index)
        index = run_end
        part_start = index
    parts.append(value[part_start:])
    return tuple(parts), tuple(runs)


def _safe_path_characters(value: str, *, drive: bool) -> bool:
    offset = 2 if drive else 0
    tail = value[offset:]
    return bool(tail) and not any(
        character in _FORBIDDEN_PATH_CHARACTERS
        or ord(character) < 0x20
        or character == ":"
        for character in tail
    )


def _is_quoted_drive_path(value: str) -> bool:
    if len(value) < 5 or not value[0].isascii() or not value[0].isalpha():
        return False
    if value[1] != ":" or not _safe_path_characters(value, drive=True):
        return False
    leading = len(value[2:]) - len(value[2:].lstrip("\\"))
    if leading not in (2, 4):
        return False
    parts, runs = _path_parts(value, 2 + leading)
    return bool(parts) and all(parts) and all(run == leading for run in runs)


def _is_quoted_unc_path(value: str) -> bool:
    if not _safe_path_characters(value, drive=False):
        return False
    leading = len(value) - len(value.lstrip("\\"))
    if leading not in (4, 8):
        return False
    parts, runs = _path_parts(value, leading)
    if len(parts) < 2 or not all(parts) or not runs:
        return False
    return all(run == leading for run in runs) or all(
        run == leading // 2 for run in runs
    )


def _is_safe_quoted_path(value: str) -> bool:
    if not value or len(value) > _MAX_PATH_LENGTH:
        return False
    return _is_quoted_drive_path(value) or _is_quoted_unc_path(value)


def structured_path_detection(value: str) -> StructuredPathDetection:

    if len(value) > _MAX_INPUT_LENGTH or "path" not in value.casefold():
        return StructuredPathDetection(masked_value=value)
    replacements: list[tuple[int, int]] = []
    path_forms: list[str] = []
    path_escapes = 0
    path_form_escapes = 0
    index = 0
    while index < len(value):
        if value[index] not in {'"', "'"}:
            index += 1
            continue
        key_end = _quoted_end(value, index)
        if key_end < 0:
            break
        cursor = key_end + 1
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        if value[index + 1 : key_end].casefold() != "path":
            index = key_end + 1
            continue
        if cursor >= len(value) or value[cursor] != ":":
            index = key_end + 1
            continue
        cursor += 1
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        if cursor >= len(value) or value[cursor] not in {'"', "'"}:
            index = key_end + 1
            continue
        path_end = _quoted_end(value, cursor)
        if path_end < 0:
            break
        raw_path = value[cursor + 1 : path_end]
        if _is_safe_quoted_path(raw_path):
            detection = path_form_detection(raw_path)
            if detection.unsafe:
                return StructuredPathDetection(masked_value=value, unsafe=True)
            path_form_escapes += detection.escape_count
            for form in detection.forms:
                if form not in path_forms:
                    path_forms.append(form)
            replacements.append((cursor + 1, path_end))
            path_escapes += raw_path.count("\\") // 2
            if len(replacements) > _MAX_PATH_FIELDS or path_escapes > _MAX_PATH_ESCAPES:
                return StructuredPathDetection(masked_value=value, unsafe=True)
            if (
                path_form_escapes > _MAX_PATH_FORM_ESCAPES
                or len(path_forms) > _MAX_PATH_FORMS
            ):
                return StructuredPathDetection(masked_value=value, unsafe=True)
        index = path_end + 1
    if not replacements:
        return StructuredPathDetection(masked_value=value)
    masked: list[str] = []
    previous = 0
    for start, end in replacements:
        masked.extend((value[previous:start], "SAFE_PATH"))
        previous = end
    masked.append(value[previous:])
    return StructuredPathDetection(
        masked_value="".join(masked),
        forms=tuple(path_forms),
    )


__all__ = (
    "StructuredPathDetection",
    "structured_path_detection",
)
