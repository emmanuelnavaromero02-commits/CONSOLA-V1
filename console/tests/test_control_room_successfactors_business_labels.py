from __future__ import annotations

import sys
import unicodedata

import pytest

from app.services.control_room.successfactors_gold_observations import (
    _sf_gold_public_widget,
)
from app.services.intelligence.business_labels import business_label


BLANK_FILLERS = tuple(
    chr(codepoint)
    for codepoint in (
        0x115F,
        0x1160,
        0x2800,
        0x3164,
        0xA8F9,
        0xFFA0,
        0x10AF6,
        0x1144E,
        0x11945,
        0x11C44,
        0x11C45,
        0x11F48,
        0x13441,
        0x13442,
        0x16FE4,
    )
)
WHITESPACE_CHARACTERS = tuple(
    chr(codepoint)
    for codepoint in range(sys.maxunicode + 1)
    if chr(codepoint).isspace()
)
FORBIDDEN_LABELS = (
    "\u200b",
    "\u2060",
    "\ufeff",
    "Nombre\u200b",
    "Nombre\u2060",
    "Nombre\ufeff",
    *(f"Nombre{chr(codepoint)}" for codepoint in range(0x202A, 0x202F)),
    *(f"Nombre{chr(codepoint)}" for codepoint in range(0x2066, 0x206A)),
    "Nombre\ufe0f",
    "Nombre\U000e0100",
    "\u034f",
    *BLANK_FILLERS,
    *(f"Nombre{filler}" for filler in BLANK_FILLERS),
    *WHITESPACE_CHARACTERS,
    "\u200b\u2060\ufeff",
)


def test_blank_filler_corpus_matches_the_runtime_unicode_name_class():
    discovered = tuple(
        chr(codepoint)
        for codepoint in range(sys.maxunicode + 1)
        if "FILLER" in unicodedata.name(chr(codepoint), "")
        or unicodedata.name(chr(codepoint), "").endswith(" BLANK")
    )
    assert discovered == BLANK_FILLERS


def test_whitespace_corpus_includes_line_and_paragraph_separators():
    assert "\u2028" in WHITESPACE_CHARACTERS
    assert "\u2029" in WHITESPACE_CHARACTERS


@pytest.mark.parametrize("value", FORBIDDEN_LABELS)
def test_business_label_rejects_controls_and_default_ignorables(value: str):
    assert business_label(value) is None


@pytest.mark.parametrize(
    "value", [None, 7, "", "  ", "(sin nombre)", "（ｓｉｎ　ｎｏｍｂｒｅ）"]
)
def test_business_label_rejects_missing_or_fabricated_labels(value: object):
    assert business_label(value) is None


def test_business_label_preserves_real_accented_business_text():
    assert business_label("  Dirección de México  ") == "Dirección de México"
    assert business_label("Área ␢ visible") == "Área ␢ visible"


def test_public_projection_is_not_ready_when_every_label_is_invalid():
    projected = _sf_gold_public_widget(
        {
            "id": "sf_headcount_by_company",
            "title": "Headcount por compañía",
            "value": len(FORBIDDEN_LABELS),
            "status": "ready",
            "rows": [
                {"company_name": value, "headcount": 1} for value in FORBIDDEN_LABELS
            ],
        }
    )

    assert projected["value"] is None
    assert projected["rows"] == []
    assert projected["status"] == "invalid_schema"
