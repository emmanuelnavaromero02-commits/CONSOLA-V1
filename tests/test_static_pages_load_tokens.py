"""Sprint v1.42 — every served HTML page resolves the design tokens.

A regression in v1.41 left ``cartridges.html`` referencing tokens that
didn't exist in ``tokens.css`` (``--text1``, ``--font-pixel``), which
collapsed several elements to UA defaults. This test pins two related
invariants:

  1. Each top-level page in ``console/app/static/*.html`` brings in
     ``tokens.css`` — directly via ``<link>`` OR transitively via a
     stylesheet that itself ``@import``s tokens (today: ``main.css``).
  2. No CSS under ``console/app/static/css/`` references a ``var(--X)``
     token that isn't declared somewhere in the same directory. A
     missing token leaves the property at its initial value and the
     designer can't see the breakage in dev.

We intentionally exclude the auth-form pages (login / forgot-password
/ etc) because they're served before theme.js runs and ship minimal
inline styles by design.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC = REPO_ROOT / "console" / "app" / "static"
CSS_DIR = STATIC / "css"

_EXCLUDE_PAGES = {"login.html", "me.html", "forgot-password.html",
                  "forgot_password.html", "reset-password.html",
                  "reset_password.html", "activate.html"}


# Which stylesheets transitively pull tokens.css (via @import)?
def _stylesheets_that_resolve_tokens() -> set[str]:
    resolves: set[str] = {"tokens.css"}
    # One pass is enough today — chain depth is 1.
    changed = True
    while changed:
        changed = False
        for css in CSS_DIR.glob("*.css"):
            if css.name in resolves:
                continue
            text = css.read_text(encoding="utf-8")
            for hit in re.findall(r'@import\s+url\(["\']?([^"\')]+)["\']?\)', text):
                base = Path(hit).name
                if base in resolves:
                    resolves.add(css.name)
                    changed = True
                    break
    return resolves


_OK_STYLESHEETS = _stylesheets_that_resolve_tokens()


def _pages():
    return sorted(p for p in STATIC.glob("*.html") if p.name not in _EXCLUDE_PAGES)


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_page_resolves_design_tokens(page):
    """Page must load at least one stylesheet that resolves tokens.css."""
    text = page.read_text(encoding="utf-8")
    refs = re.findall(r'<link[^>]+href="/static/css/([^"]+)"', text)
    # Strip cache-busting query strings so `tokens.css?v=3` matches.
    refs_basenames = {Path(r.split("?", 1)[0]).name for r in refs}
    matched = refs_basenames & _OK_STYLESHEETS
    assert matched, (
        f"{page.name} does not load any stylesheet that resolves tokens.css. "
        f"It links: {sorted(refs_basenames)}. "
        f"Add `<link rel='stylesheet' href='/static/css/tokens.css'>` "
        f"or load one of {sorted(_OK_STYLESHEETS)}."
    )


def test_no_dangling_css_variable_references():
    """Every var(--X) used inside console/app/static/css/*.css must be
    declared somewhere in the same directory. A typo (--text1 vs --text)
    silently breaks the page; this test surfaces it."""
    declared: set[str] = set()
    for css in CSS_DIR.glob("*.css"):
        text = css.read_text(encoding="utf-8")
        for name in re.findall(r"^\s*(--[a-zA-Z0-9_-]+)\s*:", text, re.MULTILINE):
            declared.add(name)

    # ``var(--X, fallback)`` is fine — the designer provided an explicit
    # default, so a missing declaration won't collapse the property.
    # We only flag ``var(--X)`` references with no fallback.
    var_no_fallback = re.compile(r"var\((--[a-zA-Z0-9_-]+)\s*\)")
    missing: list[str] = []
    for css in CSS_DIR.glob("*.css"):
        text = css.read_text(encoding="utf-8")
        for ref in var_no_fallback.findall(text):
            if ref not in declared:
                missing.append(f"{css.name} → {ref}")
    assert not missing, (
        "CSS references undeclared tokens (silent breakage):\n  "
        + "\n  ".join(sorted(set(missing)))
    )


def test_copilot_page_present_and_uses_tokens():
    """v1.42 — protector for the new copilot page."""
    p = STATIC / "copilot.html"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    refs = re.findall(r'<link[^>]+href="/static/css/([^"]+)"', text)
    refs_basenames = {Path(r).name for r in refs}
    assert refs_basenames & _OK_STYLESHEETS


def test_cartridges_page_uses_real_tokens():
    """v1.42 fix — cartridges.css no longer references undeclared
    --text1 / --font-pixel. Direct pin so a future cleanup can't
    accidentally bring them back."""
    text = (CSS_DIR / "cartridges.css").read_text(encoding="utf-8")
    assert "var(--text1)" not in text
    assert "var(--font-pixel)" not in text
