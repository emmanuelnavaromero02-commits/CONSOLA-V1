from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
APP = REPO / "console" / "app"
STATIC = APP / "static"
CONSOLE_NEXT_EXPORT = STATIC / "console-next"

_ROUTE_PAGE_RE = re.compile(r'STATIC\s*/\s*"([^"]+\.html?)"')
_PAGE_ASSET_RE = re.compile(r'(?:src|href)\s*=\s*"/static/([^"?#]+)')
_MODULE_IMPORT_RE = re.compile(
    r"""^\s*import\s+(?:[^'";]*?\s*from\s*)?['"](\.{1,2}/[^'"?#]+)""",
    re.MULTILINE,
)
_CSS_IMPORT_RE = re.compile(r"""@import\s+url\(\s*['"]?([^'")?#]+)""")
_PYTHON_ASSET_RE = re.compile(r"/static/((?:js|css)/[A-Za-z0-9_./-]+\.(?:js|css))")


def _python_sources() -> list[str]:
    return [path.read_text(encoding="utf-8") for path in sorted(APP.rglob("*.py"))]


def _served_pages() -> set[Path]:
    return {
        (STATIC / name).resolve()
        for source in _python_sources()
        for name in _ROUTE_PAGE_RE.findall(source)
    }


def _legacy_files(*suffixes: str) -> set[Path]:
    return {
        path.resolve()
        for path in STATIC.rglob("*")
        if path.is_file()
        and path.suffix in suffixes
        and CONSOLE_NEXT_EXPORT not in path.parents
    }


def _live_asset_closure() -> set[Path]:
    queue = list(_served_pages())
    for source in _python_sources():
        queue.extend((STATIC / ref).resolve() for ref in _PYTHON_ASSET_RE.findall(source))
    seen: set[Path] = set()
    while queue:
        path = queue.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix in {".html", ".htm"}:
            queue.extend((STATIC / ref).resolve() for ref in _PAGE_ASSET_RE.findall(text))
        elif path.suffix == ".js":
            queue.extend((path.parent / ref).resolve() for ref in _MODULE_IMPORT_RE.findall(text))
        elif path.suffix == ".css":
            queue.extend((path.parent / ref).resolve() for ref in _CSS_IMPORT_RE.findall(text))
    return seen


def _relative(paths: set[Path]) -> list[str]:
    return sorted(str(path.relative_to(STATIC)) for path in paths)


def test_route_served_legacy_pages_exist():
    pages = _served_pages()
    assert (STATIC / "workspace.html").resolve() in pages
    missing = {page for page in pages if not page.is_file()}
    assert not missing, f"routes serve missing legacy pages: {_relative(missing)}"


def test_legacy_studio_is_gone_and_served_by_console_next():
    studio = (STATIC / "studio.html").resolve()
    assert not studio.exists()
    assert studio not in _served_pages()
    assert studio not in _live_asset_closure()
    assert not (STATIC / "js" / "studio").exists()
    for stylesheet in ("studio.css", "studio-modern.css", "viewer.css"):
        assert not (STATIC / "css" / stylesheet).exists()
    pages_py = (APP / "routers" / "pages.py").read_text(encoding="utf-8")
    assert '_console_next_response(request, "studio/index.html")' in pages_py


def test_every_legacy_html_page_is_served_by_a_route():
    orphans = _legacy_files(".html", ".htm") - _served_pages()
    assert not orphans, (
        "console/app/static holds HTML pages that no route serves; direct /static/*.html "
        f"requests return 404, so delete them: {_relative(orphans)}"
    )


def test_every_legacy_script_and_stylesheet_is_loaded_by_a_live_page():
    orphans = _legacy_files(".js", ".mjs", ".css") - _live_asset_closure()
    assert not orphans, (
        "console/app/static holds scripts or stylesheets that no served page loads; "
        f"they stay downloadable under /static, so delete them: {_relative(orphans)}"
    )
