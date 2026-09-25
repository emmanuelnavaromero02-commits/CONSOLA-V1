from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
JS  = REPO / "console" / "app" / "static" / "js"  / "copilot.js"
CSS = REPO / "console" / "app" / "static" / "css" / "copilot.css"


def _js() -> str:  return JS.read_text(encoding="utf-8")
def _css() -> str: return CSS.read_text(encoding="utf-8")


def test_citation_card_renders_with_source_and_entity():
    src = _js()
    assert "function appendCitations(" in src
    assert "function _appendCitationCard(" in src
    assert "sourceStrong.textContent = c.source" in src
    assert "ent.textContent = c.entity" in src


def test_citation_card_shows_freshness_icon():
    src = _js()
    for bucket in ("fresh", "recent", "stale", "very_stale", "unknown"):
        assert f'{bucket}:' in src, f"missing freshness icon mapping for {bucket}"
    assert 'setAttribute("aria-label", "freshness: " + level)' in src
    assert 'setAttribute("role", "img")' in src


def test_no_citations_no_card_rendered():
    src = _js()
    assert "if (!Array.isArray(citations) || citations.length === 0) return" in src


def test_xss_in_citation_fields_is_escaped():
    src = _js()
    block_match = re.search(
        r"function _appendCitationCard\(c\) \{.*?\n\s*return card;\s*\}",
        src, re.DOTALL,
    )
    assert block_match, "citation card function not found"
    block = block_match.group(0)
    assert "innerHTML" not in block, (
        "citation card must not use innerHTML — XSS surface"
    )
    assert "${c." not in block, (
        "citation card must not interpolate citation fields into "
        "template strings — use textContent instead"
    )


def test_citation_rendered_in_turn_response_and_history_reload():
    src = _js()
    assert "if (Array.isArray(out.citations) && out.citations.length > 0)" in src
    assert "appendCitations(out.citations)" in src
    assert "if (Array.isArray(m.citations) && m.citations.length > 0)" in src
    assert "appendCitations(m.citations)" in src


def test_citation_css_uses_only_declared_tokens():
    css = _css()
    for bucket, token in [
        ("fresh",      "--success"),
        ("recent",     "--success"),
        ("stale",      "--warning"),
        ("very_stale", "--danger"),
        ("unknown",    "--text3"),
    ]:
        pattern = (
            rf'\.copilot-citation-card\[data-freshness="{bucket}"\][^\{{]*\{{\s*'
            rf'border-left-color:\s*var\({token}\)'
        )
        assert re.search(pattern, css), (
            f"citation freshness '{bucket}' must map to {token}"
        )
    block_match = re.search(
        r"/\* ── v1\.43: citation cards.*?(?=\n/\*|\Z)", css, re.DOTALL,
    )
    assert block_match
    block = block_match.group(0)
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block), (
        "citation CSS block contains a hardcoded color literal"
    )


def test_citation_card_responsive_grid():
    css = _css()
    assert ".copilot-citations" in css
    assert "auto-fit" in css
    assert "minmax(" in css


def test_citation_card_layout_does_not_break_mobile():
    css = _css()
    block_match = re.search(
        r"/\* ── v1\.43: citation cards.*?(?=\n/\*|\Z)", css, re.DOTALL,
    )
    assert block_match
    block = block_match.group(0)
    assert not re.search(r"\bwidth:\s*\d+px", block)
