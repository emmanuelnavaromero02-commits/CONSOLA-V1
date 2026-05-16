"""Sprint v1.44.1 — Tareas A + C + D + H foundation tests.

Static verification only. Visual / browser-level verification is
out of scope for the CI sandbox; see the PR body's "Manual
checklist" for the items the developer must run on a Mac with a
booted stack.

Covered:
  * tokens.css ships the spacing / radius / shadow / font-size /
    z-index / transition / info-colour / semantic-alias scales
    documented in the v1.44.1 brief.
  * components.css declares the .btn / .card / .input / .badge /
    .alert / .table / .modal / .toast / .skeleton / .nav / .fab
    classes that the upcoming frontend wiring (Tareas B / E / F /
    G) will consume.
  * ui_components.js exposes the showToast / showModal /
    showConfirm / showLoading / hideLoading globals + the
    double-load guard.
  * copilot_fab.js skips /copilot, /login, and other public pages
    so the FAB isn't injected where it'd be wrong.
  * WCAG-AA contrast holds on the documented foreground/background
    token pairs in both light and dark mode.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO     = Path(__file__).resolve().parents[1]
STATIC   = REPO / "console/app/static"
TOKENS   = STATIC / "css/tokens.css"
COMPS    = STATIC / "css/components.css"
UI_JS    = STATIC / "js/ui_components.js"
FAB_JS   = STATIC / "js/copilot_fab.js"


# ── tokens.css ────────────────────────────────────────────────────────────


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
    """The brief documents success/warning/danger/info as siblings.
    tokens.css already had the first three; v1.44.1 adds info."""
    src = _tokens()
    assert "--info:" in src
    assert "--info-soft:" in src


def test_tokens_css_has_semantic_aliases():
    """Aliases components.css consumes: text-primary, text-inverse,
    bg-card, border-subtle. These don't replace the legacy --text /
    --bg names; they coexist for naming clarity."""
    src = _tokens()
    for var in ("--text-primary", "--text-secondary", "--text-inverse",
                "--bg-primary", "--bg-secondary", "--bg-card",
                "--border-default", "--border-subtle"):
        assert var in src, f"tokens.css missing semantic alias {var}"


def test_tokens_css_dark_mode_block_present():
    """Dark mode triggered by [data-theme="dark"]. The v1.44.1 additions
    must extend dark mode too, not just :root light."""
    src = _tokens()
    assert ':root[data-theme="dark"]' in src
    # Specifically: shadow-xl and the new semantic aliases need dark
    # overrides where they differ.
    dark_block = src.split(':root[data-theme="dark"]')[1]
    assert "--shadow-xl" in dark_block, (
        "dark mode missing --shadow-xl override"
    )
    assert "--text-inverse" in dark_block, (
        "dark mode missing --text-inverse override"
    )


# ── components.css ────────────────────────────────────────────────────────


def _comps() -> str:
    return COMPS.read_text(encoding="utf-8")


def test_components_css_exists():
    assert COMPS.exists()


def test_components_css_declares_button_classes():
    src = _comps()
    for cls in (".btn", ".btn-primary", ".btn-secondary",
                ".btn-ghost", ".btn-danger"):
        # selector must appear at start-of-rule (preceded by newline or {)
        assert re.search(r"(^|[\s,])" + re.escape(cls) + r"[\s,{:]", src), (
            f"components.css missing class {cls!r}"
        )


def test_components_css_declares_card_classes():
    src = _comps()
    for cls in (".card", ".card-header", ".card-body", ".card-footer"):
        assert cls in src, f"components.css missing {cls}"


def test_components_css_declares_form_classes():
    src = _comps()
    for cls in (".input", ".input-group", ".label"):
        assert cls in src


def test_components_css_declares_badge_classes():
    src = _comps()
    for cls in (".badge", ".badge-success", ".badge-warning",
                ".badge-danger", ".badge-info", ".badge-new"):
        assert cls in src


def test_components_css_declares_alert_classes():
    src = _comps()
    for cls in (".alert-success", ".alert-warning",
                ".alert-danger", ".alert-info"):
        assert cls in src


def test_components_css_declares_table_classes():
    src = _comps()
    for cls in (".table", ".table-striped", ".table-hover"):
        assert cls in src


def test_components_css_declares_modal_classes():
    src = _comps()
    for cls in (".modal", ".modal-overlay", ".modal-header",
                ".modal-body", ".modal-footer"):
        assert cls in src


def test_components_css_declares_toast_classes():
    src = _comps()
    # tokens.css ships .toast; components.css extends with the stack
    # and the type variants.
    for cls in (".toast-stack", ".toast-info", ".toast-success",
                ".toast-warning", ".toast-error"):
        assert cls in src


def test_components_css_declares_loading_classes():
    src = _comps()
    for cls in (".skeleton", ".spinner"):
        assert cls in src


def test_components_css_declares_nav_and_fab():
    src = _comps()
    for cls in (".nav", ".nav-item", ".nav-link", ".nav-copilot", ".fab"):
        assert cls in src


def test_components_css_uses_only_tokens_no_inline_color_literals():
    """components.css must consume tokens — no hex AND no rgba()
    literals. The R1 frontend review caught five rgba() values
    pinned to light-mode hues that drifted in dark mode; the fix
    introduced --success-soft / --warning-soft / --on-danger so
    every colour reference goes through var(--…) again."""
    src = _comps()
    inline_hex = re.findall(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b", src)
    assert not inline_hex, (
        f"components.css must reference var(--…); inline hex found: {inline_hex}"
    )
    inline_rgba = re.findall(r"rgba?\([^)]+\)", src)
    assert not inline_rgba, (
        f"components.css must reference var(--…); inline rgba/rgb found: "
        f"{inline_rgba}. Add a --foo-soft token to tokens.css instead."
    )


# ── ui_components.js ──────────────────────────────────────────────────────


def _ui_js() -> str:
    return UI_JS.read_text(encoding="utf-8")


def test_ui_components_js_exists():
    assert UI_JS.exists()


def test_ui_components_js_exposes_full_api():
    src = _ui_js()
    for fn in ("window.showToast", "window.showModal",
               "window.showConfirm", "window.showLoading",
               "window.hideLoading"):
        assert fn in src, f"ui_components.js missing {fn} export"


def test_ui_components_js_double_load_guard():
    src = _ui_js()
    # Double-include guard prevents the IIFE from re-binding handlers
    # when a page (or its parent template) pulls the script twice.
    assert "__omegaUiComponentsLoaded" in src


def test_ui_components_js_supports_keyboard_dismiss():
    src = _ui_js()
    assert "'Escape'" in src or '"Escape"' in src, (
        "ui_components.js must close the modal on ESC for accessibility"
    )


def test_ui_components_js_focus_trap_on_close():
    """When a modal closes, focus must return to the element that
    opened it. lastFocused captures + restores."""
    src = _ui_js()
    assert "lastFocused" in src


def test_ui_components_js_toast_uses_textContent_not_innerHTML():
    """User-supplied message strings must not be parsed as HTML —
    otherwise toast('<img src=x onerror=…>') is XSS."""
    src = _ui_js()
    assert "node.textContent = message" in src, (
        "showToast must set textContent, never innerHTML"
    )
    # Strip JS comments so the prose "never inject HTML" doesn't trip the
    # check; only real ``.innerHTML`` assignments count.
    code_only = re.sub(r"//.*?$|/\*.*?\*/", "", src, flags=re.MULTILINE | re.DOTALL)
    assert ".innerHTML" not in code_only, (
        "ui_components.js must never assign to .innerHTML"
    )


# ── copilot_fab.js ───────────────────────────────────────────────────────


def _fab_js() -> str:
    return FAB_JS.read_text(encoding="utf-8")


def test_copilot_fab_js_exists():
    assert FAB_JS.exists()


def test_copilot_fab_skips_copilot_and_login_pages():
    src = _fab_js()
    for prefix in ("/copilot", "/login", "/forgot-password",
                   "/reset-password", "/activate"):
        assert f"'{prefix}'" in src, (
            f"copilot_fab.js must skip {prefix} (don't FAB the FAB page "
            f"or pre-auth pages)"
        )


def test_copilot_fab_double_load_guard():
    src = _fab_js()
    assert "__omegaCopilotFabLoaded" in src


def test_copilot_fab_links_to_copilot_page():
    src = _fab_js()
    assert "window.location.href = '/copilot'" in src


def test_copilot_fab_has_accessible_label():
    src = _fab_js()
    assert "aria-label" in src
    assert "title" in src


# ── WCAG AA contrast (Tarea H) ────────────────────────────────────────────


def _luminance(hex_color: str) -> float:
    """Relative luminance per WCAG 2.1, accepting #rgb or #rrggbb."""
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


# Pairs the v1.44.1 brief explicitly calls out as MUST-meet WCAG AA
# (4.5:1 for body text). Tuple is (fg_hex, bg_hex, label).
# Values mirror tokens.css :root and :root[data-theme="dark"].
WCAG_PAIRS_LIGHT = [
    ("#0f172a", "#f5f7fa", "text on bg (light)"),                # text / bg
    ("#0f172a", "#ffffff", "text on surface (light)"),           # text / bg2
    ("#334155", "#ffffff", "text2 on surface (light)"),          # text2 / bg2
    ("#ffffff", "#0a6ed1", "on-primary on primary (light)"),     # btn primary
    ("#ffffff", "#b3261e", "white on danger (light)"),           # btn danger
]
WCAG_PAIRS_DARK = [
    ("#e6edf6", "#0f1822", "text on bg (dark)"),                 # text / bg
    ("#e6edf6", "#182331", "text on surface (dark)"),            # text / surface
    ("#c5cfdc", "#182331", "text2 on surface (dark)"),           # text2 / surface
    ("#0f172a", "#4ea3e0", "on-primary on primary (dark)"),      # btn primary dark
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
    """Self-test on a known reference: black on white = 21:1."""
    assert abs(_contrast("#000000", "#ffffff") - 21.0) < 0.01
