from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
JS = REPO / "console" / "app" / "static" / "js"
NEXT_SRC = REPO / "console-next" / "src"
SCANNED = (
    JS / "activate.js",
    JS / "forgot_password.js",
    JS / "reset_password.js",
)
NEXT_STUDIO_SOURCES = (
    NEXT_SRC / "app" / "(shell)" / "studio",
    NEXT_SRC / "components" / "studio",
    NEXT_SRC / "lib" / "studio",
)

ESCAPERS = {
    "esc",
    "escJsArg",
    "escJsonArg",
    "escapeHtml",
    "sanitizeAiHtml",
    "renderAiText",
    "safeUrl",
    "entityDomId",
    "encodeURIComponent",
    "Number",
}

EXCEPTIONS: dict[tuple[str, str], str] = {}

_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do"}
_HTML_RE = re.compile(
    r"</?(?:a|b|br|button|code|defs|details|div|em|g|h[1-6]|i|input|label|li|line|marker|option|optgroup"
    r"|p|path|pre|rect|section|select|small|span|strong|summary|svg|table|tbody|td|text|textarea|th|thead"
    r"|tr|ul)\b",
    re.IGNORECASE,
)
_LITERAL_RE = re.compile(
    r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|-?\d+(?:\.\d+)?|true|false|null|undefined"
)
_METHOD_TAIL_RE = re.compile(
    r"(?:\.(?:toLocaleString|toFixed|toUpperCase|toLowerCase|trim)\([^()]*\))*"
)
_URL_ATTR_RE = re.compile(r"\b(?:href|src|action|formaction)\s*=\s*[\"']?$", re.IGNORECASE)
_URL_SINK_RE = re.compile(
    r"\.(?:href|src)\s*=(?!=)|\.setAttribute\(\s*['\"](?:href|src)['\"]\s*,"
    r"|\bwindow\.open\(|\blocation(?:\.href)?\s*=(?!=)|\blocation\.(?:assign|replace)\("
)
_SINK_RE = re.compile(
    r"\.(?:innerHTML|outerHTML)\s*=(?!=)|\.insertAdjacentHTML\([^,()]*,"
)


def _skip_literal(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == quote:
            return i + 1
        if quote == "`" and text.startswith("${", i):
            i = _skip_code(text, i + 2, "}") + 1
            continue
        i += 1
    return i


def _skip_code(text: str, i: int, stop: str) -> int:
    depth = 0
    while i < len(text):
        char = text[i]
        if char == stop and depth == 0:
            return i
        if char in "'\"`":
            i = _skip_literal(text, i)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        i += 1
    return i


def _top_level(text: str):
    depth, i = 0, 0
    while i < len(text):
        char = text[i]
        if char in "'\"`":
            end = _skip_literal(text, i)
            yield i, char, depth
            i = end
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        yield i, char, depth
        i += 1


def _matching(text: str, start: int) -> int | None:
    for i, char, depth in _top_level(text[start:]):
        if char in ")]}" and depth == 0:
            return start + i
    return None


def _split(expr: str, separators: tuple[str, ...]) -> list[str]:
    parts, last = [], 0
    skip_until = -1
    for i, char, depth in _top_level(expr):
        if i < skip_until or depth != 0:
            continue
        hit = next((sep for sep in separators if expr.startswith(sep, i)), None)
        if hit and not (hit == "+" and expr[i : i + 2] in {"++", "+="}):
            parts.append(expr[last:i])
            last = i + len(hit)
            skip_until = last
    parts.append(expr[last:])
    return [part.strip() for part in parts]


def _ternary(expr: str) -> tuple[str, str] | None:
    question, nested = None, 0
    for i, char, depth in _top_level(expr):
        if depth != 0:
            continue
        if char == "?" and expr[i + 1 : i + 2] not in {"?", "."} and expr[i - 1 : i] != "?":
            if question is None:
                question = i
            else:
                nested += 1
        elif char == ":" and question is not None:
            if nested == 0:
                return expr[question + 1 : i], expr[i + 1 :]
            nested -= 1
    return None


def _expression_at(text: str, start: int) -> str:
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    i, depth = start, 0
    while i < len(text):
        char = text[i]
        if char in "'\"`":
            i = _skip_literal(text, i)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and char in ";,":
            break
        elif depth == 0 and char == "\n":
            before = text[start:i].rstrip()
            after = text[i:].lstrip()
            if after[:1] not in {"?", ":", "+", ".", "|", "&"} and not before.endswith(
                ("?", ":", "+", "||", "&&", "=>", "(", "=")
            ):
                break
        i += 1
    return text[start:i].strip()


class Source:
    def __init__(self, path: Path):
        self.name = path.name
        self.text = path.read_text(encoding="utf-8")
        self.templates: list[tuple[str, list[tuple[int, str]]]] = []
        self._masked: list[tuple[int, int]] = []
        self._scan(0)
        code = list(self.text)
        for start, end in self._masked:
            for i in range(start, min(end, len(code))):
                if code[i] != "\n":
                    code[i] = " "
        self.code = "".join(code)

    def line(self, offset: int) -> int:
        return self.text.count("\n", 0, offset) + 1

    def _regex_allowed(self, i: int) -> bool:
        head = self.text[:i].rstrip()
        if not head:
            return True
        word = re.search(r"[A-Za-z_$][\w$]*$", head)
        return head[-1] in _REGEX_PRECEDERS or bool(word and word.group(0) in _REGEX_KEYWORDS)

    def _skip_regex(self, i: int) -> int:
        text, in_class = self.text, False
        i += 1
        while i < len(text) and text[i] != "\n":
            char = text[i]
            if char == "\\":
                i += 2
                continue
            if char == "[":
                in_class = True
            elif char == "]":
                in_class = False
            elif char == "/" and not in_class:
                return i + 1
            i += 1
        return i

    def _mask(self, start: int, end: int) -> int:
        self._masked.append((start, end))
        return end

    def _template(self, i: int) -> int:
        text = self.text
        chunks: list[str] = []
        expressions: list[tuple[int, str]] = []
        chunk_start = i
        i += 1
        while i < len(text):
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == "`":
                self.templates.append(("".join(chunks), expressions))
                return self._mask(chunk_start, i + 1)
            if text.startswith("${", i):
                self._mask(chunk_start, i + 2)
                end = self._scan(i + 2, stop="}")
                expressions.append((i + 2, text[i + 2 : end]))
                i = chunk_start = end
                i += 1
                continue
            chunks.append(text[i])
            i += 1
        return i

    def _scan(self, i: int, stop: str | None = None) -> int:
        text, depth = self.text, 0
        while i < len(text):
            char = text[i]
            if stop and char == stop and depth == 0:
                return i
            if text.startswith("//", i):
                end = text.find("\n", i)
                i = self._mask(i, len(text) if end == -1 else end)
                continue
            if text.startswith("/*", i):
                i = self._mask(i, text.find("*/", i) + 2)
                continue
            if char in "({[":
                depth += 1
            elif char in ")}]":
                depth -= 1
            elif char in "'\"":
                i = self._mask(i, _skip_literal(text, i))
                continue
            elif char == "`":
                i = self._template(i)
                continue
            elif char == "/" and self._regex_allowed(i):
                i = self._mask(i, self._skip_regex(i))
                continue
            i += 1
        return i


@lru_cache(maxsize=1)
def _sources() -> tuple[Source, ...]:
    return tuple(Source(path) for path in SCANNED)


def _value_at(text: str, start: int) -> tuple[int, str]:
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    return start, _expression_at(text, start)


def _in_scope(code: str, declared: int, used: int) -> bool:
    depth = 0
    for char in code[declared:used]:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return True


class Analyzer:
    def __init__(self, source: Source, active: frozenset[str] = frozenset()):
        self.source = source
        self.active = active

    def safe(self, expr: str, pos: int) -> bool:
        expr = expr.strip()
        while expr.startswith("(") and _matching(expr, 0) == len(expr) - 1:
            expr = expr[1:-1].strip()
        if not expr or _LITERAL_RE.fullmatch(expr) or expr.startswith("!"):
            return True
        if (self.source.name, " ".join(expr.split())) in EXCEPTIONS:
            return True
        if expr[0] == "`" and _skip_literal(expr, 0) == len(expr):
            return True
        branches = _ternary(expr)
        if branches:
            return all(self.safe(branch, pos) for branch in branches)
        for separators in (("||", "??"), ("+",)):
            parts = _split(expr, separators)
            if len(parts) > 1:
                return all(self.safe(part, pos) for part in parts)
        parts = _split(expr, ("&&",))
        if len(parts) > 1:
            return self.safe(parts[-1], pos)
        if len(_split(expr, ("*", "/", "%", "-"))) > 1:
            return True
        if re.fullmatch(r"[\s\S]+\.(?:length|size)|[\w$.?]+\.toFixed\(\d*\)", expr):
            return True
        call = re.match(r"([\w$]+)\(", expr)
        if call:
            end = _matching(expr, call.end() - 1)
            if end is not None and _METHOD_TAIL_RE.fullmatch(expr[end + 1 :]):
                if call.group(1) in ESCAPERS:
                    return True
                if end == len(expr) - 1 and self._function_safe(call.group(1)):
                    return True
        mapped = re.fullmatch(r"([\s\S]+?)\.map\(([\s\S]*)\)\.join\([^()]*\)", expr)
        if mapped:
            body = mapped.group(2).strip()
            arrow = re.match(r"(?:\([\w$,\s]*\)|[\w$]+)\s*=>\s*([\s\S]*)$", body)
            if arrow:
                return self._body_safe(arrow.group(1), None, pos)
            return re.fullmatch(r"[\w$]+", body) is not None and self._function_safe(body)
        if re.fullmatch(r"[A-Za-z_$][\w$]*", expr):
            return self._identifier_safe(expr, pos)
        return False

    def _body_safe(self, body: str, code: str | None, base: int) -> bool:
        if not body.strip().startswith("{"):
            return self.safe(body, base)
        values = [_value_at(body, m.end()) for m in re.finditer(r"\breturn\b", code or body)]
        return bool(values) and all(self.safe(value, base + start) for start, value in values)

    def _assigned_values(self, name: str, pos: int) -> list[tuple[int, str]]:
        code = self.source.code
        declaration = re.compile(rf"(?:\b(?:const|let|var)\s+|,\s*){re.escape(name)}\s*=(?![=>])")
        assignment = re.compile(rf"(?<![\w$.]){re.escape(name)}\s*\+?=(?![=>])")
        scoped = [m.start() for m in declaration.finditer(code) if m.start() < pos and _in_scope(code, m.start(), pos)]
        if not scoped:
            return []
        return [
            _value_at(self.source.text, m.end())
            for m in assignment.finditer(code)
            if scoped[-1] <= m.start() < pos
        ]

    def _identifier_safe(self, name: str, pos: int) -> bool:
        key = f"{name}@{pos}"
        if key in self.active:
            return True
        values = self._assigned_values(name, pos)
        nested = Analyzer(self.source, self.active | {key})
        return bool(values) and all(nested.safe(value, start) for start, value in values)

    def url_safe(self, expr: str, pos: int) -> bool:
        expr = expr.strip()
        wrapped = re.fullmatch(r"esc\(([\s\S]*)\)", expr)
        if wrapped and _matching(expr, 3) == len(expr) - 1:
            expr = wrapped.group(1).strip()
        parts = _split(expr, ("||", "??"))
        if len(parts) > 1:
            return all(self.url_safe(part, pos) for part in parts)
        if expr[:1] in {"'", '"', "`"} and _skip_literal(expr, 0) == len(expr):
            return re.match(r"['\"`](?:/(?![/\\])|#)", expr) is not None
        if expr.startswith("safeUrl(") and _matching(expr, 7) == len(expr) - 1:
            return True
        if re.fullmatch(r"[A-Za-z_$][\w$]*", expr):
            key = f"url {expr}@{pos}"
            if key in self.active:
                return True
            values = self._assigned_values(expr, pos)
            nested = Analyzer(self.source, self.active | {key})
            return bool(values) and all(nested.url_safe(value, start) for start, value in values)
        return False

    def _function_safe(self, name: str) -> bool:
        key = f"function {name}"
        if key in self.active:
            return True
        for source in _sources():
            nested = Analyzer(source, self.active | {key})
            declared = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source.code)
            if declared:
                end = _matching(source.text, declared.end() - 1)
                if end is None:
                    return False
                span = slice(declared.end() - 1, end + 1)
                return nested._body_safe(source.text[span], source.code[span], span.start)
            arrow = re.search(
                rf"\b(?:const|let)\s+{re.escape(name)}\s*=\s*(?:\([\w$,\s]*\)|[\w$]+)\s*=>",
                source.code,
            )
            if arrow:
                start, value = _value_at(source.text, arrow.end())
                return nested._body_safe(value, source.code[start : start + len(value)], start)
        return False


def _unsafe_interpolations() -> list[str]:
    findings = []
    for source in _sources():
        analyzer = Analyzer(source)
        for chunks, expressions in source.templates:
            if not _HTML_RE.search(chunks):
                continue
            for offset, expression in expressions:
                if not analyzer.safe(expression, offset):
                    flat = " ".join(expression.split())[:120]
                    findings.append(f"{source.name}:{source.line(offset)} ${{{flat}}}")
    return findings


def _unsafe_sinks() -> list[str]:
    findings = []
    for source in _sources():
        analyzer = Analyzer(source)
        for match in _SINK_RE.finditer(source.code):
            start, value = _value_at(source.text, match.end())
            if not analyzer.safe(value, start):
                flat = " ".join(value.split())[:120]
                findings.append(f"{source.name}:{source.line(match.start())} {flat}")
    return findings


def _unsafe_urls() -> list[str]:
    findings = []
    for source in _sources():
        analyzer = Analyzer(source)
        for _, expressions in source.templates:
            for offset, expression in expressions:
                if _URL_ATTR_RE.search(source.text[max(0, offset - 40) : offset - 2]):
                    if not analyzer.url_safe(expression, offset):
                        findings.append(f"{source.name}:{source.line(offset)} ${{{' '.join(expression.split())}}}")
        for match in _URL_SINK_RE.finditer(source.text):
            if source.code[match.start() + 1] == " ":
                continue
            start, value = _value_at(source.text, match.end())
            if not analyzer.url_safe(value, start):
                findings.append(f"{source.name}:{source.line(match.start())} {' '.join(value.split())[:120]}")
    return findings


def test_legacy_studio_scripts_are_gone():
    assert not (JS / "studio").exists()
    assert all(path.is_file() for path in SCANNED)


def test_next_studio_never_renders_server_html():
    sources = [
        path
        for root in NEXT_STUDIO_SOURCES
        for path in sorted(root.rglob("*.ts*"))
        if ".test." not in path.name
    ]
    assert sources, "console-next Studio sources not found"
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for sink in ("dangerouslySetInnerHTML", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            assert sink not in text, f"{path.relative_to(REPO)} uses {sink}"
        assert "eval(" not in text and "new Function" not in text, path


def test_html_template_interpolations_are_escaped():
    findings = _unsafe_interpolations()
    assert not findings, "unescaped interpolation in HTML template:\n" + "\n".join(findings)


def test_html_sinks_only_receive_escaped_markup():
    findings = _unsafe_sinks()
    assert not findings, "HTML sink receives unescaped data:\n" + "\n".join(findings)


def test_url_sinks_accept_only_http_or_same_origin_paths():
    findings = _unsafe_urls()
    assert not findings, "URL sink without safeUrl():\n" + "\n".join(findings)
    client = (NEXT_SRC / "lib" / "studio" / "client.ts").read_text(encoding="utf-8")
    assert 'url.protocol !== "http:" && url.protocol !== "https:"' in client
    assert 'path.startsWith("//")' in client


def test_exceptions_are_still_needed():
    unused = []
    for key in EXCEPTIONS:
        source = next(s for s in _sources() if s.name == key[0])
        if key[1] not in " ".join(source.text.split()):
            unused.append(key)
    assert not unused, unused
