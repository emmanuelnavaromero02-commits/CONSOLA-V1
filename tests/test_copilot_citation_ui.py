"""Sprint v1.43 — copilot citation UI.

These are static checks against the JS + CSS source. We don't spin up
a headless browser — the existing token-loading + CSP regression tests
already pin the broader invariants. Here we just confirm:

  * The renderer hooks exist and use textContent (no innerHTML on
    user-controlled fields).
  * The CSS uses only declared tokens; freshness levels map to
    --success / --warning / --danger.
  * The HTML page bootstraps the new behaviour (loads copilot.js).
"""
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
    # Source / entity end up under textContent assignments, not innerHTML.
    assert "sourceStrong.textContent = c.source" in src
    assert "ent.textContent = c.entity" in src


def test_citation_card_shows_freshness_icon():
    src = _js()
    # Each freshness bucket has an icon mapping.
    for bucket in ("fresh", "recent", "stale", "very_stale", "unknown"):
        assert f'{bucket}:' in src, f"missing freshness icon mapping for {bucket}"
    # The icon goes into a span with role="img" + aria-label so screen
    # readers announce the bucket name (accessibility, not just color).
    assert 'setAttribute("aria-label", "freshness: " + level)' in src
    assert 'setAttribute("role", "img")' in src


def test_no_citations_no_card_rendered():
    src = _js()
    # The renderer must early-out when the array is empty or missing.
    assert "if (!Array.isArray(citations) || citations.length === 0) return" in src


def test_xss_in_citation_fields_is_escaped():
    """Every dynamic citation field reaches the DOM through textContent
    or as a data-* attribute — never innerHTML. We scan the function
    body and assert no innerHTML assignment touches a `c.*` field."""
    src = _js()
    # Locate the citation render block.
    block_match = re.search(
        r"function _appendCitationCard\(c\) \{.*?\n\s*return card;\s*\}",
        src, re.DOTALL,
    )
    assert block_match, "citation card function not found"
    block = block_match.group(0)
    # Forbid any innerHTML usage inside the citation renderer.
    assert "innerHTML" not in block, (
        "citation card must not use innerHTML — XSS surface"
    )
    # Forbid template-string interpolation of c.* into HTML.
    assert "${c." not in block, (
        "citation card must not interpolate citation fields into "
        "template strings — use textContent instead"
    )


def test_citation_rendered_in_turn_response_and_history_reload():
    src = _js()
    # New turn: renderTurnResponse must call appendCitations.
    assert "if (Array.isArray(out.citations) && out.citations.length > 0)" in src
    assert "appendCitations(out.citations)" in src
    # History reload: selectConversation must re-render persisted citations.
    assert "if (Array.isArray(m.citations) && m.citations.length > 0)" in src
    assert "appendCitations(m.citations)" in src


def test_citation_css_uses_only_declared_tokens():
    css = _css()
    # All freshness levels mapped to semantic tokens — no hex colors.
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
    # Scan the citation block for hardcoded hex (a runaway #rgb).
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
    # The container uses a responsive auto-fit grid so cards collapse
    # to one column on narrow screens without needing a media query.
    assert ".copilot-citations" in css
    assert "auto-fit" in css
    assert "minmax(" in css


def test_citation_card_layout_does_not_break_mobile():
    """The existing copilot media query (< 720px) collapses the sidebar.
    Citations should still work since auto-fit handles narrow widths.
    Cross-check that no rule forces a fixed width on the card."""
    css = _css()
    block_match = re.search(
        r"/\* ── v1\.43: citation cards.*?(?=\n/\*|\Z)", css, re.DOTALL,
    )
    assert block_match
    block = block_match.group(0)
    # No fixed width — only min-width via minmax().
    assert not re.search(r"\bwidth:\s*\d+px", block)
