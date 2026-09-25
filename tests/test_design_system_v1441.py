from __future__ import annotations

from pathlib import Path


REPO     = Path(__file__).resolve().parents[1]
STATIC   = REPO / "console/app/static"
TOKENS   = STATIC / "css/tokens.css"


def _tokens() -> str:
    return TOKENS.read_text(encoding="utf-8")


def test_tokens_css_exists():
    assert TOKENS.exists()


def test_tokens_css_has_spacing_scale():
    src = _tokens()
    for var in ("--space-xs", "--space-sm", "--space-md",
                "--space-lg", "--space-xl", "--space-2xl"):
        assert var in src, f"tokens.css missing {var}"


def test_tokens_css_has_extended_radius_scale():
    src = _tokens()
    for var in ("--radius-sm", "--radius-md", "--radius-lg",
                "--radius-xl", "--radius-pill"):
        assert var in src, f"tokens.css missing {var}"


def test_tokens_css_has_extended_shadow_scale():
    src = _tokens()
    for var in ("--shadow-sm", "--shadow-md", "--shadow-lg", "--shadow-xl"):
        assert var in src, f"tokens.css missing {var}"


def test_tokens_css_has_font_size_scale():
    src = _tokens()
    for var in ("--font-size-xs", "--font-size-sm", "--font-size-md",
                "--font-size-lg", "--font-size-xl",
                "--font-size-2xl", "--font-size-3xl"):
        assert var in src, f"tokens.css missing {var}"


def test_tokens_css_has_z_index_scale():
    src = _tokens()
    for var in ("--z-base", "--z-dropdown", "--z-fab",
                "--z-modal", "--z-toast", "--z-tooltip"):
        assert var in src, f"tokens.css missing {var}"


def test_tokens_css_has_transition_scale():
    src = _tokens()
    for var in ("--transition-fast", "--transition-base", "--transition-slow"):
        assert var in src, f"tokens.css missing {var}"


def test_tokens_css_has_info_colour():
    src = _tokens()
    assert "--info:" in src
    assert "--info-soft:" in src


def test_tokens_css_has_semantic_aliases():
    src = _tokens()
    for var in ("--text-primary", "--text-secondary", "--text-inverse",
                "--bg-primary", "--bg-secondary", "--bg-card",
                "--border-default", "--border-subtle"):
        assert var in src, f"tokens.css missing semantic alias {var}"


def test_tokens_css_dark_mode_block_present():
    src = _tokens()
    assert ':root[data-theme="dark"]' in src
    dark_block = src.split(':root[data-theme="dark"]')[1]
    assert "--shadow-xl" in dark_block, (
        "dark mode missing --shadow-xl override"
    )
    assert "--text-inverse" in dark_block, (
        "dark mode missing --text-inverse override"
    )


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(fg: str, bg: str) -> float:
    lf, lb = _luminance(fg), _luminance(bg)
    light, dark = (lf, lb) if lf > lb else (lb, lf)
    return (light + 0.05) / (dark + 0.05)


WCAG_PAIRS_LIGHT = [
    ("#0f172a", "#f5f7fa", "text on bg (light)"),
    ("#0f172a", "#ffffff", "text on surface (light)"),
    ("#334155", "#ffffff", "text2 on surface (light)"),
    ("#ffffff", "#0a6ed1", "on-primary on primary (light)"),
    ("#ffffff", "#b3261e", "white on danger (light)"),
]
WCAG_PAIRS_DARK = [
    ("#e6edf6", "#0f1822", "text on bg (dark)"),
    ("#e6edf6", "#182331", "text on surface (dark)"),
    ("#c5cfdc", "#182331", "text2 on surface (dark)"),
    ("#0f172a", "#4ea3e0", "on-primary on primary (dark)"),
]


def test_wcag_aa_contrast_light_mode():
    failures = []
    for fg, bg, label in WCAG_PAIRS_LIGHT:
        ratio = _contrast(fg, bg)
        if ratio < 4.5:
            failures.append(f"{label}: {ratio:.2f} (need 4.5)")
    assert not failures, (
        "WCAG AA contrast failures in light mode:\n  " + "\n  ".join(failures)
    )


def test_wcag_aa_contrast_dark_mode():
    failures = []
    for fg, bg, label in WCAG_PAIRS_DARK:
        ratio = _contrast(fg, bg)
        if ratio < 4.5:
            failures.append(f"{label}: {ratio:.2f} (need 4.5)")
    assert not failures, (
        "WCAG AA contrast failures in dark mode:\n  " + "\n  ".join(failures)
    )


def test_wcag_helpers_compute_known_pair_correctly():
    assert abs(_contrast("#000000", "#ffffff") - 21.0) < 0.01
