from __future__ import annotations

import unicodedata


_MISSING_LABELS = frozenset({"(sin nombre)"})
_DEFAULT_IGNORABLE_RANGES = (
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
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def _is_forbidden_character(character: str) -> bool:
    codepoint = ord(character)
    unicode_name = unicodedata.name(character, "")
    return (
        unicodedata.category(character).startswith("C")
        or "FILLER" in unicode_name
        or unicode_name.endswith(" BLANK")
        or any(start <= codepoint <= end for start, end in _DEFAULT_IGNORABLE_RANGES)
    )


def business_label(value: object) -> str | None:

    if not isinstance(value, str) or any(map(_is_forbidden_character, value)):
        return None
    normalized = unicodedata.normalize("NFKC", value)
    if any(map(_is_forbidden_character, normalized)):
        return None
    display = normalized.strip()
    if not display or display.casefold() in _MISSING_LABELS:
        return None
    return display


__all__ = ("business_label",)
