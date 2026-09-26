from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "console"))

from app.services.security_headers import (  # noqa: E402
    APP_THEME_FONT_DIR,
    APP_THEME_SCRIPT,
    APP_THEME_SHIM,
    inject_published_app_theme,
)

STATIC = REPO / "console/app/static"
FONT_DIR = STATIC / APP_THEME_FONT_DIR.removeprefix("/static/")
# SHA-256 of the files inside Inter-4.1.zip (github.com/rsms/inter, release v4.1);
# identical to the same paths at the v4.1 git tag.
INTER_SHA256 = {
    "InterVariable.woff2": "693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3",
    "InterVariable-Italic.woff2": "e564f652916db6c139570fefb9524a77c4d48f30c92928de9db19b6b5c7a262a",
    "LICENSE.txt": "262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a",
}
REQUESTED_DARK = {
    "--bg": "#090d16",
    "--card": "#111827",
    "--border": "rgba(255, 255, 255, 0.08)",
    "--primary": "#6366f1",
    "--text-primary": "#f8fafc",
    "--text-muted": "#94a3b8",
}
PACKAGED_APPS = sorted(REPO.glob("cartridges/*/apps/*.html"))


def _block(selector: str) -> dict[str, str]:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", APP_THEME_SHIM)
    assert match, f"{selector!r} rule missing from the shim"
    declarations = {}
    for raw in match.group(1).split(";"):
        name, sep, value = raw.partition(":")
        if sep:
            declarations[name.strip()] = value.strip()
    return declarations


def _light() -> dict[str, str]:
    return _block(':root,\n:root[data-theme="light"]')


def _dark() -> dict[str, str]:
    return _block(':root[data-theme="dark"]')


def _layer_body() -> str:
    start = APP_THEME_SHIM.index("@layer omega-base {")
    depth = 0
    for pos in range(APP_THEME_SHIM.index("{", start), len(APP_THEME_SHIM)):
        if APP_THEME_SHIM[pos] == "{":
            depth += 1
        elif APP_THEME_SHIM[pos] == "}":
            depth -= 1
            if depth == 0:
                return APP_THEME_SHIM[APP_THEME_SHIM.index("{", start) + 1 : pos]
    raise AssertionError("@layer omega-base is not closed")


def _selectors_at_depth(css: str) -> list[tuple[int, str]]:
    found, depth, text = [], 0, ""
    for char in css:
        if char == "{":
            found.append((depth, text.strip()))
            depth, text = depth + 1, ""
        elif char == "}":
            depth, text = depth - 1, ""
        elif char == ";":
            text = ""
        else:
            text += char
    return found


def _luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_dark_palette_is_the_requested_one():
    dark = _dark()
    for name, value in REQUESTED_DARK.items():
        assert dark.get(name) == value, name


def test_light_counterpart_covers_every_dark_token():
    light = _light()
    assert set(_dark()) <= set(light)
    assert light["--font-sans"].startswith('"Inter"')


def test_legacy_names_alias_the_new_tokens_in_every_theme():
    aliases = _block(":root,\n:root[data-theme]")
    assert aliases == {
        "--bg2": "var(--card)",
        "--text": "var(--text-primary)",
        "--text1": "var(--text-primary)",
        "--text2": "var(--text-secondary)",
        "--text3": "var(--text-muted)",
    }
    assert APP_THEME_SHIM.index(":root,\n:root[data-theme] {") > APP_THEME_SHIM.index(
        ':root[data-theme="dark"] {'
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_button_and_text_colours_meet_wcag_aa(theme):
    tokens = _light() if theme == "light" else {**_light(), **_dark()}
    on_primary = tokens["--on-primary"]
    for background in ("--primary-strong", "--primary-strong-hover"):
        assert _contrast(on_primary, tokens[background]) >= 4.5, (theme, background)
    for text in ("--text-primary", "--text-secondary", "--text-muted"):
        for surface in ("--bg", "--card"):
            assert _contrast(tokens[text], tokens[surface]) >= 4.5, (theme, text, surface)


def test_buttons_fill_with_the_strong_primary():
    assert re.search(r":where\(\.omega-btn\) \{[^}]*background: var\(--primary-strong\);", APP_THEME_SHIM)
    assert re.search(
        r":where\(\.omega-btn:hover\) \{[^}]*background: var\(--primary-strong-hover\);", APP_THEME_SHIM
    )


def test_base_styles_are_layered_and_zero_specificity():
    selectors = _selectors_at_depth(_layer_body())
    assert selectors
    for depth, selector in selectors:
        if selector.startswith("@media"):
            continue
        assert selector.startswith(":where("), selector
    assert any(sel.startswith("@media") for _, sel in selectors)
    assert [sel for depth, sel in selectors if depth == 1] == [":where(.omega-btn)"]
    outside = APP_THEME_SHIM[: APP_THEME_SHIM.index("@layer omega-base")]
    assert re.findall(r"^body \{", outside, re.M) == ["body {"]
    assert "!important" not in APP_THEME_SHIM


def test_shim_leaves_color_scheme_and_selection_to_the_app():
    # color-scheme would flip the default text colour of apps that paint fixed
    # light panels without their own colour (replicon skill_gaps_heatmap).
    assert "color-scheme" not in APP_THEME_SHIM
    assert "::selection" not in APP_THEME_SHIM


@pytest.mark.parametrize("app", PACKAGED_APPS, ids=lambda p: f"{p.parent.parent.name}/{p.stem}")
def test_shim_adds_no_color_scheme_to_packaged_apps(app):
    original = app.read_text(encoding="utf-8")
    out = inject_published_app_theme(original)
    assert out.count("color-scheme") == original.count("color-scheme")
    assert out.count("::selection") == original.count("::selection")


def test_skill_gaps_heatmap_keeps_the_browser_default_text_colour():
    app = REPO / "cartridges/replicon/apps/skill_gaps_heatmap.html"
    original = app.read_text(encoding="utf-8")
    assert "color-scheme" not in original
    assert "color-scheme" not in inject_published_app_theme(original)


def test_theme_needs_no_new_csp_hosts():
    assert "@import" not in APP_THEME_SHIM
    urls = re.findall(r"url\(\s*[\"']?([^\"')]+)", APP_THEME_SHIM)
    assert urls
    assert all(url.startswith(f"{APP_THEME_FONT_DIR}/") for url in urls)
    assert re.search(r"https?:", APP_THEME_SHIM) is None


def test_font_files_exist_and_are_licensed():
    urls = re.findall(r"url\(\s*[\"']?([^\"')]+)", APP_THEME_SHIM)
    for url in urls:
        path = STATIC / url.removeprefix("/static/")
        assert path.is_file(), url
        assert path.read_bytes().startswith(b"wOF2"), url
    assert {p.name for p in FONT_DIR.iterdir()} == set(INTER_SHA256)
    for name, digest in INTER_SHA256.items():
        assert hashlib.sha256((FONT_DIR / name).read_bytes()).hexdigest() == digest, name
    assert "SIL Open Font License" in (FONT_DIR / "LICENSE.txt").read_text(encoding="utf-8")


def test_every_packaged_app_is_covered():
    assert len(PACKAGED_APPS) == 21


@pytest.mark.parametrize("app", PACKAGED_APPS, ids=lambda p: f"{p.parent.parent.name}/{p.stem}")
def test_shim_lands_after_each_packaged_app_style(app):
    original = app.read_text(encoding="utf-8")
    out = inject_published_app_theme(original)

    assert out.count('<style id="omega-app-theme-shim">') == 1
    head_end = original.lower().rfind("</head>")
    assert head_end > 0
    last_style = original.lower().rfind("</style>", 0, head_end)
    shim_at = out.index(APP_THEME_SHIM)
    assert shim_at >= last_style + len("</style>")
    assert out[:15] == original[:15]
    assert out.replace(APP_THEME_SHIM, "", 1).replace(APP_THEME_SCRIPT, "", 1) == original


@pytest.fixture()
def studio_assistant_module(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("INTERNAL_API_KEY", "x" * 64)
    console_dir = str(REPO / "console")
    sys.path[:] = [p for p in sys.path if p != console_dir]
    sys.path.insert(0, console_dir)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import studio_assistant

    return studio_assistant


def test_studio_style_rules_only_name_real_tokens(studio_assistant_module):
    rules = studio_assistant_module.STEP_INSTRUCTIONS[5]
    for phrase in ("REGLAS DE ESTILO", "data-theme", "color-scheme", "theme-switch.js"):
        assert phrase in rules
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", APP_THEME_SHIM))
    assert set(re.findall(r"--[a-z0-9-]+", rules)) <= declared
    for component in set(re.findall(r"omega-[a-z-]+", rules)):
        assert f".{component})" in APP_THEME_SHIM, component
    assert set(re.findall(r"#[0-9a-fA-F]{3,6}\b", rules)) == {"#fff", "#000"}
    assert "@layer" in rules and "@import" in rules
