from __future__ import annotations

import unicodedata
from bisect import bisect_right

from app.services.control_room.business_copy_detection import (
    canonicalize_detection_separators,
)

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
_CONFUSABLE_TRANSLATION = str.maketrans(
    {
        # Common Cyrillic homoglyphs used to hide ASCII identifiers.
        "А": "A",
        "В": "B",
        "С": "C",
        "Е": "E",
        "Н": "H",
        "І": "I",
        "Ј": "J",
        "К": "K",
        "М": "M",
        "О": "O",
        "Р": "P",
        "Ѕ": "S",
        "Т": "T",
        "Х": "X",
        "У": "Y",
        "а": "a",
        "в": "b",
        "с": "c",
        "ԁ": "d",
        "е": "e",
        "г": "r",
        "і": "i",
        "ј": "j",
        "к": "k",
        "ӏ": "l",
        "м": "m",
        "н": "h",
        "о": "o",
        "р": "p",
        "ѕ": "s",
        "т": "t",
        "х": "x",
        "у": "y",
        "ԝ": "w",
        # Common Greek homoglyphs for the same security vocabulary.
        "Α": "A",
        "Β": "B",
        "Ε": "E",
        "Ζ": "Z",
        "Η": "H",
        "Ι": "I",
        "Κ": "K",
        "Μ": "M",
        "Ν": "N",
        "Ο": "O",
        "Ρ": "P",
        "Τ": "T",
        "Υ": "Y",
        "Χ": "X",
        "α": "a",
        "β": "b",
        "ϲ": "c",
        "ε": "e",
        "ι": "i",
        "κ": "k",
        "ν": "v",
        "ο": "o",
        "ρ": "p",
        "τ": "t",
        "υ": "u",
        "χ": "x",
    }
)


def is_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    index = bisect_right(_DEFAULT_IGNORABLE_STARTS, codepoint) - 1
    return index >= 0 and codepoint <= _DEFAULT_IGNORABLE_RANGES[index][1]


def security_skeleton(value: str) -> str:
    skeleton = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.category(character).startswith("M")
        and unicodedata.category(character) != "Cc"
        and not is_default_ignorable(character)
    )
    return skeleton.translate(_CONFUSABLE_TRANSLATION)


def canonical_security_text(value: str) -> str:
    return canonicalize_detection_separators(security_skeleton(value))


def security_detection_forms(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value)
    skeleton = security_skeleton(value)
    forms = (
        value,
        normalized,
        canonicalize_detection_separators(normalized),
        skeleton,
        canonicalize_detection_separators(skeleton),
    )
    return tuple(dict.fromkeys(forms))


__all__ = (
    "canonical_security_text",
    "is_default_ignorable",
    "security_detection_forms",
    "security_skeleton",
)
