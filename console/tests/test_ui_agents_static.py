from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_HTML = REPO_ROOT / "console/app/static/agents.html"
AGENTS_JS = REPO_ROOT / "console/app/static/js/agents.js"
THEME_SWITCH_JS = REPO_ROOT / "console/app/static/js/theme-switch.js"

INLINE_HANDLER_RE = re.compile(
    r"\s+on(click|change|input|submit|load|keydown|keyup|mouseover|mouseout|focus|blur)\s*=",
    re.IGNORECASE,
)


def test_agents_html_is_strict_csp_safe():
    html = AGENTS_HTML.read_text(encoding="utf-8")
    assert not INLINE_HANDLER_RE.findall(html)
    assert "<script>\n" not in html
    assert "/static/js/agents.js" in html
    assert "/static/js/theme-switch.js" in html


def test_agents_page_does_not_depend_on_external_assets():
    html = AGENTS_HTML.read_text(encoding="utf-8")
    assert "https://fonts.googleapis.com" not in html
    assert "https://fonts.gstatic.com" not in html


def test_agents_js_contains_complete_agent_surface_wiring():
    js = AGENTS_JS.read_text(encoding="utf-8")
    for endpoint in (
        "/api/agents?include_inactive=true",
        "/api/agents/_tool-catalog",
        "/api/agents/${encodeURIComponent(state.selectedId)}/runs?limit=30",
        "/api/agent-runs/${encodeURIComponent(runId)}",
        "/invoke/stream",
    ):
        assert endpoint in js
    for hook in (
        "btn-new-agent",
        "btn-save",
        "btn-del",
        "btn-refresh-runs",
        "data-agent-id",
        "data-tool",
        "data-remove-var",
    ):
        assert hook in js
    assert "addEventListener" in js
    assert "window." not in js


def test_theme_switch_file_exists_for_agents_page():
    assert THEME_SWITCH_JS.read_text(encoding="utf-8").strip()
