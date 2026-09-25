from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "console" / "app" / "static"
STUDIO_JS = STATIC / "js" / "studio"
BOOTSTRAP_JS = STUDIO_JS / "legacy-bootstrap.js"
HANDLER_MODULES = (STUDIO_JS / "legacy.js", STUDIO_JS / "sql-runner.js")

_EVENT_ATTR_RE = re.compile(
    r"\bon(click|dblclick|change|input|key[a-z]+|blur|focus[a-z]*|mouse[a-z]+|pointer[a-z]+|"
    r"touch[a-z]+|drag[a-z]*|drop|submit|load|error|paste|scroll|wheel|contextmenu)"
    r"\s*=\s*([\"'])"
)
_CALL_RE = re.compile(r"(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(")
_STRING_LITERAL_RE = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")
_KEYWORDS = {"if", "return", "typeof"}
_SAFE_TEMPLATE_ARG_RE = re.compile(
    r"escJsArg\([\w.]+\)"
    r"|escJsonArg\([\w.]+\)"
    r"|Number\([\w.]+\)(?:\s*\|\|\s*0)?"
    r"|![\w.]+"
    r"|safeId"
)


def _template_end(text: str, start: int) -> int:
    depth = 1
    index = start + 2
    while index < len(text) and depth:
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
        index += 1
    return index


def _inline_handlers() -> list[tuple[str, str, list[str]]]:
    handlers = []
    sources = sorted(STUDIO_JS.glob("*.js")) + [STATIC / "studio.html"]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for match in _EVENT_ATTR_RE.finditer(text):
            quote = match.group(2)
            index = match.end()
            body: list[str] = []
            expressions: list[str] = []
            while index < len(text) and text[index] != quote:
                if text.startswith("${", index):
                    end = _template_end(text, index)
                    expressions.append(text[index + 2 : end - 1].strip())
                    body.append("0")
                    index = end
                    continue
                body.append(text[index])
                index += 1
            handlers.append((path.name, "".join(body), expressions))
    return handlers


def _handler_names() -> set[str]:
    names: set[str] = set()
    for _, body, _ in _inline_handlers():
        code = _STRING_LITERAL_RE.sub("''", body)
        names.update(name for name in _CALL_RE.findall(code) if name not in _KEYWORDS)
    return names


def _bootstrap() -> str:
    return BOOTSTRAP_JS.read_text(encoding="utf-8")


def _allowlist() -> set[str]:
    match = re.search(r"const LEGACY_HANDLER_ALLOWLIST = new Set\(\[(.*?)\]\);", _bootstrap(), re.S)
    assert match, "legacy-bootstrap.js must declare LEGACY_HANDLER_ALLOWLIST"
    return set(re.findall(r"'([A-Za-z_$][\w$]*)'", match.group(1)))


def _function_body(source: str, name: str) -> str:
    match = re.search(rf"function {name}\([^)]*\) \{{(.*?)\n\}}", source, re.S)
    assert match, f"{name} not found in legacy-bootstrap.js"
    return match.group(1)


def test_studio_templates_still_use_inline_handlers():
    assert len(_inline_handlers()) > 50


def test_allowlist_matches_the_handlers_studio_templates_call():
    bootstrap_literals = set(re.findall(r"callWindow\('([^']+)'", _bootstrap()))
    assert bootstrap_literals <= _allowlist()
    assert _allowlist() == _handler_names()


def test_allowlisted_handlers_are_exported_studio_functions():
    exported = set()
    for path in HANDLER_MODULES:
        exported.update(
            re.findall(r"export (?:async )?function ([A-Za-z_$][\w$]*)\(", path.read_text(encoding="utf-8"))
        )
    assert _allowlist() <= exported


def test_call_window_rejects_names_outside_the_allowlist_before_lookup():
    body = _function_body(_bootstrap(), "callWindow")
    guard = body.find("LEGACY_HANDLER_ALLOWLIST.has(name)")
    lookup = body.find("window[name]")
    assert guard != -1 and lookup != -1
    assert guard < lookup
    assert "return;" in body[guard:lookup]


def test_inline_handler_arguments_are_attribute_escaped():
    unsafe = [
        (name, expression)
        for name, _, expressions in _inline_handlers()
        for expression in expressions
        if not _SAFE_TEMPLATE_ARG_RE.fullmatch(expression)
    ]
    assert not unsafe, f"inline handler interpolations must be escaped: {unsafe}"
    legacy = (STUDIO_JS / "legacy.js").read_text(encoding="utf-8")
    assert "return esc(JSON.stringify(String(s || '')));" in legacy
    assert "return esc(JSON.stringify(value ?? null));" in legacy
