from __future__ import annotations

from itertools import product

from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from app.services.control_room.diagnostic_redaction_keys import (
    sensitive_diagnostic_field,
)


SECRET = "TOPSECRET"
KEY_COMBINATIONS = 82_944
ADVERSARIAL_CASES = 181
EXPECTED_COMBINATIONS = 83_125


def _encodings(character: str) -> tuple[str, ...]:
    codepoint = ord(character)
    return (
        character,
        f"\\u{codepoint:04x}",
        f"\\U{codepoint:08X}",
        f"\\x{codepoint:02x}",
    )


def _encoded_sensitive_keys() -> int:
    checked = 0
    for word in ("token", "api_key", "password"):
        for parts in product(*(_encodings(character) for character in word)):
            key = "".join(parts)
            assert sensitive_diagnostic_field(key), key
            checked += 1
    return checked


def _syntax_variants() -> int:
    checked = 0
    slashes = ("\\", "＼", "﹨")
    prefixes = {
        "u": ("u", "ｕ", "υ", "𝘶"),
        "U": ("U", "Ｕ", "𝘜"),
        "x": ("x", "ｘ", "х", "𝘹"),
    }
    sensitive_digits = {"u": "0073", "U": "00000073", "x": "73"}
    benign_digits = {"u": "0074", "U": "00000074", "x": "74"}
    for slash in slashes:
        for prefix, variants in prefixes.items():
            for variant in variants:
                key = f"pa{slash}{variant}{sensitive_digits[prefix]}sword"
                assert redact_diagnostic_value({key: SECRET}) == {key: "[REDACTED]"}
                checked += 1
                text = f'{{"{key}":"{SECRET}"}}'
                assert redact_diagnostic_value(text) == "[REDACTED]"
                checked += 1
                benign = f"Metric {slash}{variant}{benign_digits[prefix]}otal"
                assert redact_diagnostic_value(benign) == benign
                checked += 1
    return checked


def _created_or_retained_escapes() -> int:
    keys = [
        f"p{slash_escape}{suffix}"
        for slash_escape in (
            r"\uFF3C",
            r"\uFE68",
            r"\U0000FF3C",
            r"\U0000FE68",
        )
        for suffix in (
            "141ssword",
            "qassword",
            "nassword",
            "\\141ssword",
            "/assword",
            '"assword',
        )
    ]
    keys.extend(
        (
            r"p\x61ssw\157rd",
            r"p\141ssw\u006frd",
            r"p\u0061ssw\157rd",
            r"p\157ssw\U0000006Frd",
            "p＼141ssw\\u006frd",
            "p﹨qassw\\x6frd",
            r"p\u005cu0073sword",
            r"p\uFF3Cu0073sword",
            r"p\uFF3C\uFF550073sword",
            r"p\uFE68\uFF580073sword",
        )
    )
    checked = 0
    for key in keys:
        assert redact_diagnostic_value({key: SECRET}) == {key: "[REDACTED]"}
        checked += 1
        assert redact_diagnostic_value(f'{{"{key}":"{SECRET}"}}') == "[REDACTED]"
        checked += 1
    return checked


def _bounded_inputs() -> int:
    cases = (
        (r"\x41" * 128, r"\x41" * 128),
        (r"\x41" * 129, "[REDACTED]"),
        ("A" * 16_384, "A" * 16_384),
        ("A" * 16_385, "[REDACTED]"),
        ("A" * 16_380 + r"\x42", "A" * 16_380 + r"\x42"),
        ("A" * 16_381 + r"\x42", "[REDACTED]"),
    )
    for value, expected in cases:
        assert redact_diagnostic_value(value) == expected
    return len(cases)


def _malformed_and_benign_inputs() -> int:
    unsafe = (
        r'{"note":"\u","secret":"TOPSECRET"}',
        r'{"note":"\u1","secret":"TOPSECRET"}',
        r'{"note":"\u12GG","secret":"TOPSECRET"}',
        r'{"note":"\U00110000","secret":"TOPSECRET"}',
        r'{"note":"\U0000D800","secret":"TOPSECRET"}',
        r'{"note":"\xG1","secret":"TOPSECRET"}',
        r'{"pa\qssword":"TOPSECRET"}',
    )
    for value in unsafe:
        assert redact_diagnostic_value(value) == "[REDACTED]"
    path = r"C:\Program Files\OMEGA"
    assert redact_diagnostic_value(path) == path
    return len(unsafe) + 1


def test_deterministic_diagnostic_redaction_fuzz_covers_83125_cases() -> None:
    key_checked = _encoded_sensitive_keys()
    adversarial_checked = sum(
        (
            _syntax_variants(),
            _created_or_retained_escapes(),
            _bounded_inputs(),
            _malformed_and_benign_inputs(),
        )
    )
    checked = key_checked + adversarial_checked

    assert key_checked == KEY_COMBINATIONS
    assert adversarial_checked == ADVERSARIAL_CASES
    assert checked == EXPECTED_COMBINATIONS
    print(f"deterministic diagnostic redaction fuzz checked={checked}")
