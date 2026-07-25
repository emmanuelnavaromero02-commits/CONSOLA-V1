from __future__ import annotations


MAX_VISIBLE_COPY_SCAN_LENGTH = 8192

_DETECTION_SEPARATOR_TRANSLATION = str.maketrans(
    {
        "∕": "/",
        "⁄": "/",
        "⧸": "/",
        "꞉": ":",
        "﹕": ":",
        "∶": ":",
        "ː": ":",
        "˸": ":",
        "։": ":",
        "׃": ":",
        "⁚": ":",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
        "﹘": "-",
        "﹣": "-",
        "－": "-",
    }
)


def canonicalize_detection_separators(value: str) -> str:
    return value.translate(_DETECTION_SEPARATOR_TRANSLATION)


def raw_copy_within_scan_limit(value: object) -> bool:
    return isinstance(value, str) and len(value) <= MAX_VISIBLE_COPY_SCAN_LENGTH


__all__ = (
    "MAX_VISIBLE_COPY_SCAN_LENGTH",
    "canonicalize_detection_separators",
    "raw_copy_within_scan_limit",
)
