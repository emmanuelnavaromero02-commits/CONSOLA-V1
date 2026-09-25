from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_STATIC = REPO_ROOT / "workspace" / "app" / "static"
WORKSPACE_JS = WORKSPACE_STATIC / "js" / "workspace.js"


def _extract_function(src: str, name: str) -> str:
    match = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*{{", src)
    assert match, f"missing function {name}"
    start = match.start()
    depth = 0
    for idx in range(match.end() - 1, len(src)):
        char = src[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return src[start : idx + 1]
    raise AssertionError(f"could not parse function {name}")


def test_workspace_html_serves_render_markdown_script():
    html = (WORKSPACE_STATIC / "workspace.html").read_text(encoding="utf-8")
    src = WORKSPACE_JS.read_text(encoding="utf-8")

    assert '/static/js/workspace.js' in html
    assert "function renderMarkdown" in src


def test_render_markdown_escapes_captured_markdown_content():
    src = WORKSPACE_JS.read_text(encoding="utf-8")
    render_markdown = _extract_function(src, "renderMarkdown")

    wrapped_replacements = re.findall(
        r"\.replace\([^;]+?=>\s*(?:protect\()?\s*`<(?P<tag>[a-z][^>]*)>[^`]+`",
        render_markdown,
        flags=re.DOTALL,
    )
    assert wrapped_replacements, "renderMarkdown should contain markdown substitutions"

    assert "`<strong>${escHtml(t)}</strong>`" in render_markdown
    assert "`<code>${escHtml(c)}</code>`" in render_markdown
    assert "`<pre><code>${escHtml(code)}</code></pre>`" in render_markdown
    assert "${escHtml(t)}</a>" in render_markdown
    assert "${escHtml(u)}" in render_markdown
    assert "html = escHtml(html);" in render_markdown
    assert "`<strong>${t}</strong>`" not in render_markdown


def test_render_markdown_payload_renders_as_text_not_image():
    src = WORKSPACE_JS.read_text(encoding="utf-8")
    js = "\n".join((
        _extract_function(src, "escHtml"),
        _extract_function(src, "renderMarkdown"),
        textwrap.dedent(
            r"""
            const normal = renderMarkdown('**texto normal**');
            if (!normal.includes('<strong>texto normal</strong>')) {
              throw new Error(`normal bold markdown broke: ${normal}`);
            }

            const payload = renderMarkdown('**<img src=x onerror=alert(1)>**');
            if (payload.includes('<img')) {
              throw new Error(`XSS payload was rendered as HTML: ${payload}`);
            }
            if (!payload.includes('<strong>&lt;img src=x onerror=alert(1)&gt;</strong>')) {
              throw new Error(`XSS payload was not escaped inside strong: ${payload}`);
            }
            """
        ),
    ))

    subprocess.run(["node", "-e", js], check=True)
