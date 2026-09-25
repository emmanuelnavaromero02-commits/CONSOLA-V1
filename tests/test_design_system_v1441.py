from __future__ import annotations

import re
from pathlib import Path


REPO     = Path(__file__).resolve().parents[1]
STATIC   = REPO / "console/app/static"
TOKENS   = STATIC / "css/tokens.css"
COMPS    = STATIC / "css/components.css"
UI_JS    = STATIC / "js/ui_components.js"
FAB_JS   = STATIC / "js/copilot_fab.js"


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


def _comps() -> str:
    return COMPS.read_text(encoding="utf-8")


def test_components_css_exists():
    assert COMPS.exists()


def test_components_css_declares_button_classes():
    src = _comps()
    for cls in (".btn", ".btn-primary", ".btn-secondary",
                ".btn-ghost", ".btn-danger"):
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
    src = _comps()
    inline_hex = re.findall(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b", src)
    assert not inline_hex, (
        f"components.css must reference var(--…); inline hex found: {inline_hex}"
    )
    inline_func = re.findall(r"(?:rgba?|hsla?)\([^)]+\)", src)
    assert not inline_func, (
        f"components.css must reference var(--…); inline rgb/rgba/hsl found: "
        f"{inline_func}. Add a --foo-soft token to tokens.css instead."
    )
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    forbidden_names = (
        "white", "black", "red", "green", "blue", "yellow",
        "gray", "grey", "silver", "navy", "teal", "lime",
        "maroon", "olive", "purple", "fuchsia", "aqua",
        "orange", "pink", "brown",
    )
    for name in forbidden_names:
        hits = re.findall(
            rf":\s*{name}\s*[;\}}]",
            code,
            flags=re.IGNORECASE,
        )
        assert not hits, (
            f"components.css uses named colour {name!r} ({len(hits)}× usage). "
            f"Add a --foo token instead."
        )


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
    assert "__omegaUiComponentsLoaded" in src


def test_ui_components_js_supports_keyboard_dismiss():
    src = _ui_js()
    assert "'Escape'" in src or '"Escape"' in src, (
        "ui_components.js must close the modal on ESC for accessibility"
    )


def test_ui_components_js_focus_trap_on_close():
    src = _ui_js()
    assert "lastFocused" in src


def test_ui_components_js_toast_uses_textContent_not_innerHTML():
    src = _ui_js()
    assert "node.textContent = message" in src, (
        "showToast must set textContent, never innerHTML"
    )
    code_only = re.sub(r"//.*?$|/\*.*?\*/", "", src, flags=re.MULTILINE | re.DOTALL)
    assert ".innerHTML" not in code_only, (
        "ui_components.js must never assign to .innerHTML"
    )


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
