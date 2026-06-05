from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_lessons_service_documents_zero_width_space_with_escape_not_literal():
    src = (REPO / "console" / "app" / "services" / "lessons_service.py").read_text(encoding="utf-8")

    assert "\u200b" not in src
    assert "\\u200b" in src
