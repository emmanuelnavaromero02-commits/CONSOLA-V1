from __future__ import annotations

import unicodedata
from bisect import bisect_right


# Unicode DerivedCoreProperties.txt: Default_Ignorable_Code_Point.
_DEFAULT_IGNORABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
_DEFAULT_IGNORABLE_STARTS = tuple(start for start, _end in _DEFAULT_IGNORABLE_RANGES)


def is_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    index = bisect_right(_DEFAULT_IGNORABLE_STARTS, codepoint) - 1
    return index >= 0 and codepoint <= _DEFAULT_IGNORABLE_RANGES[index][1]


def security_skeleton(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.category(character).startswith("M")
        and not is_default_ignorable(character)
    )


def security_detection_forms(value: str) -> tuple[str, ...]:
    forms = (
        value,
        unicodedata.normalize("NFKC", value),
        security_skeleton(value),
    )
    return tuple(dict.fromkeys(forms))


__all__ = (
    "is_default_ignorable",
    "security_detection_forms",
    "security_skeleton",
)
