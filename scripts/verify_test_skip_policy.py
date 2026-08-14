#!/usr/bin/env python3
"""Fail-closed release policy for pytest and Playwright skips.

The source inventory records every skip declaration, not merely a count.  In
release CI this module is also loaded as a pytest plugin and turns every
runtime skip without an exact reviewed identity into a failing test session.
Playwright's JSON report is checked separately after each browser gate.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = REPO / ".github" / "release-test-skip-policy.json"
PYTEST_ROOTS = (
    "tests",
    "console/tests",
    "workspace/tests",
    "vault/tests",
    "refinement/tests",
    "mcp-infra/tests",
    "cartridges",
    "airflow",
)
PLAYWRIGHT_ROOT = "tests-e2e/specs"
_ACTIVE_PYTEST_CONFIG: Any | None = None
PYTEST_CALLS = {
    "pytest.skip",
    "pytest.importorskip",
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
    "pytest.xfail",
    "unittest.skip",
    "unittest.skipIf",
    "unittest.skipUnless",
}
# Playwright ``test.fail`` is deliberately not governed here: it declares an
# expected-failure assertion and does not suppress execution like skip/fixme.


class SkipPolicyError(RuntimeError):
    """The reviewed inventory or a runtime report is unsafe."""


@dataclass(frozen=True)
class Declaration:
    path: str
    line: int
    column: int
    kind: str
    source_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "kind": self.kind,
            "source_sha256": self.source_sha256,
        }


def _safe_relative_path(path: str) -> str:
    candidate = PurePosixPath(path)
    if (
        not path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or "\\" in path
        or "\n" in path
        or "\r" in path
    ):
        raise SkipPolicyError("skip policy contains an unsafe path")
    return candidate.as_posix()


def _call_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _python_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name in {"pytest", "unittest"}:
                    aliases[imported.asname or imported.name] = imported.name
        elif isinstance(node, ast.ImportFrom) and node.module in {"pytest", "unittest"}:
            for imported in node.names:
                if imported.name == "*":
                    raise SkipPolicyError(
                        "wildcard imports from skip-capable test APIs are forbidden"
                    )
                aliases[imported.asname or imported.name] = (
                    f"{node.module}.{imported.name}"
                )

    # Resolve simple assignments such as ``s = pytest.skip``.  Even when the
    # alias call cannot be proven, the source API reference itself is sealed.
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            raw = _call_name(value) if value is not None else None
            if raw is None:
                continue
            first, *rest = raw.split(".")
            resolved = ".".join([aliases.get(first, first), *rest])
            if resolved not in PYTEST_CALLS | {"pytest", "pytest.mark", "unittest"}:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and aliases.get(target.id) != resolved:
                    aliases[target.id] = resolved
                    changed = True
    return aliases


def _resolve_python_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    raw = _call_name(node)
    if raw is None:
        return None
    first, *rest = raw.split(".")
    return ".".join([aliases.get(first, first), *rest])


def _python_declaration(
    *, text: str, relative: str, node: ast.AST, kind: str
) -> Declaration:
    segment = ast.get_source_segment(text, node)
    if not segment or not hasattr(node, "lineno") or not hasattr(node, "col_offset"):
        raise SkipPolicyError(f"cannot resolve skip declaration source: {relative}")
    return Declaration(
        path=relative,
        line=node.lineno,
        column=node.col_offset + 1,
        kind=kind,
        source_sha256=hashlib.sha256(segment.encode("utf-8")).hexdigest(),
    )


def _python_declarations(path: Path, relative: str) -> list[Declaration]:
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=relative)
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise SkipPolicyError(f"cannot parse Python test source: {relative}") from exc
    aliases = _python_aliases(tree)
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    declarations: list[Declaration] = []
    call_functions: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            target = _resolve_python_name(node.value, aliases)
            if target in {"pytest", "unittest", "pytest.__dict__", "unittest.__dict__"}:
                raise SkipPolicyError(
                    f"dynamic skip-capable API subscription is forbidden: "
                    f"{relative}:{node.lineno}"
                )
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            target = _resolve_python_name(node.value, aliases)
            if target in {"pytest", "unittest"}:
                raise SkipPolicyError(
                    f"dynamic skip-capable API introspection is forbidden: "
                    f"{relative}:{node.lineno}"
                )
        if not isinstance(node, ast.Call):
            continue
        kind = _resolve_python_name(node.func, aliases)
        if kind == "__import__" and node.args:
            imported_name = node.args[0]
            if isinstance(imported_name, ast.Constant) and imported_name.value in {
                "pytest",
                "unittest",
            }:
                raise SkipPolicyError(
                    f"dynamic skip-capable module import is forbidden: "
                    f"{relative}:{node.lineno}"
                )
        if kind == "getattr" and node.args:
            target = _resolve_python_name(node.args[0], aliases)
            attribute = node.args[1] if len(node.args) >= 2 else None
            if target in {"pytest", "unittest"} and (
                not isinstance(attribute, ast.Constant)
                or not isinstance(attribute.value, str)
                or attribute.value
                in {"skip", "skipif", "skipUnless", "importorskip", "xfail"}
            ):
                raise SkipPolicyError(
                    f"dynamic skip API access is forbidden: {relative}:{node.lineno}"
                )
        if kind == "vars" and node.args:
            target = _resolve_python_name(node.args[0], aliases)
            if target in {"pytest", "unittest"}:
                raise SkipPolicyError(
                    f"dynamic skip-capable API introspection is forbidden: "
                    f"{relative}:{node.lineno}"
                )
        if kind == "object.__getattribute__" and node.args:
            target = _resolve_python_name(node.args[0], aliases)
            if target in {"pytest", "unittest"}:
                raise SkipPolicyError(
                    f"dynamic skip-capable API introspection is forbidden: "
                    f"{relative}:{node.lineno}"
                )
        if kind in {"eval", "exec"} and any(
            isinstance(argument, ast.Constant)
            and isinstance(argument.value, str)
            and re.search(r"\b(?:pytest|unittest)\b", argument.value)
            for argument in node.args
        ):
            raise SkipPolicyError(
                f"dynamic skip-capable code execution is forbidden: "
                f"{relative}:{node.lineno}"
            )
        if kind not in PYTEST_CALLS:
            continue
        call_functions.add(id(node.func))
        declarations.append(
            _python_declaration(text=text, relative=relative, node=node, kind=kind)
        )
    for node in ast.walk(tree):
        if (
            not isinstance(node, (ast.Name, ast.Attribute))
            or id(node) in call_functions
        ):
            continue
        kind = _resolve_python_name(node, aliases)
        if kind in PYTEST_CALLS:
            parent = parents.get(id(node))
            is_decorator = (
                isinstance(
                    parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
                and node in parent.decorator_list
            )
            if not is_decorator:
                raise SkipPolicyError(
                    f"indirect skip API references are forbidden: {relative}:{node.lineno}"
                )
            declarations.append(
                _python_declaration(
                    text=text,
                    relative=relative,
                    node=node,
                    kind=f"{kind}:decorator",
                )
            )
    return declarations


def _mask_typescript_non_code(text: str, *, preserve_strings: bool = False) -> str:
    chars = list(text)
    index = 0
    state = "code"
    quote = ""
    while index < len(chars):
        char = chars[index]
        nxt = chars[index + 1] if index + 1 < len(chars) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if char == "/" and nxt == "*":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if char in {"'", '"', "`"}:
                quote = char
                if not preserve_strings:
                    chars[index] = " "
                index += 1
                state = "string"
                continue
        elif state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                chars[index] = " "
            index += 1
            continue
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                chars[index] = chars[index + 1] = " "
                index += 2
                state = "code"
                continue
            if char != "\n":
                chars[index] = " "
            index += 1
            continue
        else:
            if char == "\\":
                if not preserve_strings:
                    chars[index] = " "
                if index + 1 < len(chars):
                    if not preserve_strings and chars[index + 1] != "\n":
                        chars[index + 1] = " "
                    index += 2
                    continue
            if char == quote:
                if not preserve_strings:
                    chars[index] = " "
                index += 1
                state = "code"
                continue
            if not preserve_strings and char != "\n":
                chars[index] = " "
            index += 1
            continue
        index += 1
    return "".join(chars)


def _balanced_call_end(masked: str, open_paren: int) -> int:
    depth = 0
    for index in range(open_paren, len(masked)):
        char = masked[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
    raise SkipPolicyError("unterminated Playwright test.skip declaration")


def _playwright_declarations(path: Path, relative: str) -> list[Declaration]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SkipPolicyError(
            f"cannot read Playwright test source: {relative}"
        ) from exc
    masked = _mask_typescript_non_code(text)
    comments_masked = _mask_typescript_non_code(text, preserve_strings=True)
    aliases = {"test"}
    for imported in re.finditer(
        r"import\s*\{(?P<names>[^}]*)\}\s*from\s*['\"]@playwright/test['\"]",
        text,
    ):
        for item in imported.group("names").split(","):
            match = re.fullmatch(
                r"\s*test(?:\s+as\s+(?P<alias>[A-Za-z_$][A-Za-z0-9_$]*))?\s*",
                item,
            )
            if match:
                aliases.add(match.group("alias") or "test")
    alias_pattern = "|".join(
        re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)
    )
    bracket_pattern = re.compile(
        rf"(?<![A-Za-z0-9_$\.])(?:{alias_pattern}|testInfo)"
        r"(?:\.describe)?\s*\[\s*['\"](?:skip|fixme)['\"]\s*\]"
    )
    indirect_patterns = (
        re.compile(r"(?<![A-Za-z0-9_$\.])testInfo\.(?:skip|fixme)\b"),
        re.compile(
            rf"\b(?:const|let|var)\s*\{{[^}}]*\b(?:skip|fixme)\b[^}}]*\}}"
            rf"\s*=\s*(?:{alias_pattern})\b"
        ),
    )
    indirect = bracket_pattern.search(comments_masked)
    if indirect is not None:
        raise SkipPolicyError(
            f"indirect Playwright skip API references are forbidden: "
            f"{relative}:{text.count(chr(10), 0, indirect.start()) + 1}"
        )
    for pattern in indirect_patterns:
        indirect = pattern.search(masked)
        if indirect is not None:
            raise SkipPolicyError(
                f"indirect Playwright skip API references are forbidden: "
                f"{relative}:{text.count(chr(10), 0, indirect.start()) + 1}"
            )
    declarations: list[Declaration] = []
    api_pattern = re.compile(
        rf"(?<![A-Za-z0-9_$\.])(?P<alias>{alias_pattern})"
        r"(?P<api>\.(?:skip|fixme)|\.describe\.(?:skip|fixme))\b"
    )
    for match in api_pattern.finditer(masked):
        cursor = match.end()
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        if cursor < len(masked) and masked[cursor] == "(":
            end = _balanced_call_end(masked, cursor)
        else:
            raise SkipPolicyError(
                f"indirect Playwright skip API references are forbidden: {relative}:"
                f"{text.count(chr(10), 0, match.start()) + 1}"
            )
        line = text.count("\n", 0, match.start()) + 1
        previous_newline = text.rfind("\n", 0, match.start())
        column = match.start() - previous_newline
        segment = text[match.start() : end]
        canonical_api = match.group("api").removeprefix(".")
        declarations.append(
            Declaration(
                path=relative,
                line=line,
                column=column,
                kind=f"test.{canonical_api}",
                source_sha256=hashlib.sha256(segment.encode("utf-8")).hexdigest(),
            )
        )
    return declarations


def scan_declarations(repo: Path = REPO) -> dict[str, list[Declaration]]:
    pytest_declarations: list[Declaration] = []
    for root_name in PYTEST_ROOTS:
        root = repo / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(
                part.startswith(".") or part == "__pycache__" for part in path.parts
            ):
                continue
            relative = path.relative_to(repo).as_posix()
            # Only test modules and their conftest files are in release scope.
            if path.name != "conftest.py" and not path.name.startswith("test"):
                continue
            pytest_declarations.extend(_python_declarations(path, relative))

    playwright_declarations: list[Declaration] = []
    playwright_root = repo / PLAYWRIGHT_ROOT
    if playwright_root.exists():
        for path in sorted(playwright_root.rglob("*.ts")):
            relative = path.relative_to(repo).as_posix()
            playwright_declarations.extend(_playwright_declarations(path, relative))

    def key(item: Declaration) -> tuple[str, int, int, str, str]:
        return (item.path, item.line, item.column, item.kind, item.source_sha256)

    return {
        "pytest": sorted(pytest_declarations, key=key),
        "playwright": sorted(playwright_declarations, key=key),
    }


def _inventory_digest(declarations: Iterable[Declaration]) -> str:
    payload = [declaration.as_dict() for declaration in declarations]
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_inventory(declarations: Iterable[Declaration]) -> list[dict[str, str]]:
    grouped: dict[str, list[Declaration]] = {}
    for declaration in declarations:
        grouped.setdefault(declaration.path, []).append(declaration)
    return [
        {
            "path": path,
            "declarations_sha256": _inventory_digest(grouped[path]),
        }
        for path in sorted(grouped)
    ]


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkipPolicyError("release test skip policy cannot be read") from exc
    if not isinstance(policy, dict) or set(policy) != {
        "schema_version",
        "source_scopes",
        "runtime_scopes",
    }:
        raise SkipPolicyError("release test skip policy has an invalid root schema")
    if policy["schema_version"] != 1:
        raise SkipPolicyError("release test skip policy schema_version must be 1")
    for key in ("source_scopes", "runtime_scopes"):
        if not isinstance(policy[key], list):
            raise SkipPolicyError(f"release test skip policy {key} must be a list")
    return policy


def _validate_common_scope(scope: Any, *, expected_keys: set[str]) -> None:
    if not isinstance(scope, dict) or set(scope) != expected_keys:
        raise SkipPolicyError("release test skip scope has an invalid schema")
    for field in ("id", "runner", "owner", "reason"):
        value = scope.get(field)
        if (
            not isinstance(value, str)
            or not value.strip()
            or "\n" in value
            or "\r" in value
        ):
            raise SkipPolicyError(f"release test skip scope has invalid {field}")
    if scope["runner"] not in {"pytest", "playwright"}:
        raise SkipPolicyError("release test skip scope has an unknown runner")
    if not isinstance(scope["scope"], dict):
        raise SkipPolicyError("release test skip scope must be an object")


def verify_source_policy(
    policy: dict[str, Any], repo: Path = REPO
) -> dict[str, list[Declaration]]:
    scanned = scan_declarations(repo)
    scopes = policy["source_scopes"]
    if len(scopes) != 2 or {
        scope.get("runner") for scope in scopes if isinstance(scope, dict)
    } != {
        "pytest",
        "playwright",
    }:
        raise SkipPolicyError(
            "source policy must declare pytest and Playwright exactly once"
        )
    seen_ids: set[str] = set()
    for scope in scopes:
        _validate_common_scope(
            scope,
            expected_keys={"id", "runner", "owner", "reason", "scope"},
        )
        if scope["id"] in seen_ids:
            raise SkipPolicyError("release test skip policy has duplicate ids")
        seen_ids.add(scope["id"])
        details = scope["scope"]
        if set(details) != {"files", "inventory_sha256"}:
            raise SkipPolicyError("source skip scope has an invalid schema")
        declarations = scanned[scope["runner"]]
        current_files = _file_inventory(declarations)
        declared_files = details["files"]
        digest = details["inventory_sha256"]
        if not isinstance(declared_files, list) or any(
            not isinstance(item, dict)
            or set(item) != {"path", "declarations_sha256"}
            or not isinstance(item["path"], str)
            or not isinstance(item["declarations_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["declarations_sha256"])
            for item in declared_files
        ):
            raise SkipPolicyError("source skip file inventory is invalid")
        declared_paths = [item["path"] for item in declared_files]
        if declared_paths != sorted(set(declared_paths)):
            raise SkipPolicyError("source skip paths must be sorted and unique")
        for declared_path in declared_paths:
            _safe_relative_path(declared_path)
        if declared_files != current_files:
            raise SkipPolicyError(
                f"{scope['runner']} exact per-file skip declaration inventory changed"
            )
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SkipPolicyError("source skip inventory digest is invalid")
        if digest != _inventory_digest(declarations):
            raise SkipPolicyError(f"{scope['runner']} skip inventory digest changed")
    return scanned


def _normalize_text(raw: str, *, label: str) -> str:
    if not isinstance(raw, str):
        raise SkipPolicyError(f"{label} must be text")
    normalized = " ".join(unicodedata.normalize("NFC", raw).split())
    if not normalized or len(normalized) > 2_048:
        raise SkipPolicyError(f"{label} is empty or oversized")
    return normalized


def _normalize_nodeid(raw: str, repo: Path = REPO) -> str:
    if not isinstance(raw, str) or not raw:
        raise SkipPolicyError("pytest skip has no nodeid")
    path, *parts = raw.replace("\\", "/").split("::")
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            path = candidate.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError as exc:
            raise SkipPolicyError("runtime skip points outside the repository") from exc
    normalized_path = _safe_relative_path(path)
    if any(
        not part or len(part) > 1_024 or "\n" in part or "\r" in part for part in parts
    ):
        raise SkipPolicyError("pytest skip nodeid is invalid")
    return "::".join((normalized_path, *parts))


def _normalize_runtime_authorization(runner: str, authorization: Any) -> dict[str, str]:
    if not isinstance(authorization, dict):
        raise SkipPolicyError("runtime skip authorization must be an object")
    if runner == "pytest":
        expected = {"nodeid", "phase", "category", "reason"}
        if set(authorization) != expected:
            raise SkipPolicyError("pytest runtime authorization schema is invalid")
        phase = authorization["phase"]
        category = authorization["category"]
        if phase not in {"collect", "setup", "call", "teardown"}:
            raise SkipPolicyError("pytest runtime authorization phase is invalid")
        if category not in {"skip", "xfail", "importorskip"}:
            raise SkipPolicyError("pytest runtime authorization category is invalid")
        return {
            "nodeid": _normalize_nodeid(authorization["nodeid"]),
            "phase": phase,
            "category": category,
            "reason": _normalize_text(
                authorization["reason"], label="pytest skip reason"
            ),
        }
    expected = {
        "project",
        "spec_file",
        "spec_title",
        "test_id",
        "category",
        "reason",
    }
    if set(authorization) != expected:
        raise SkipPolicyError("Playwright runtime authorization schema is invalid")
    project = authorization["project"]
    test_id = authorization["test_id"]
    category = authorization["category"]
    if (
        not isinstance(project, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", project) is None
    ):
        raise SkipPolicyError("Playwright project identity is invalid")
    if (
        not isinstance(test_id, str)
        or re.fullmatch(r"[0-9a-f]{20}-[0-9a-f]{20}", test_id) is None
    ):
        raise SkipPolicyError("Playwright test id is invalid")
    if category not in {"skip", "fixme"}:
        raise SkipPolicyError("Playwright runtime authorization category is invalid")
    spec_file = _normalize_runtime_path(authorization["spec_file"])
    if not spec_file.startswith(f"{PLAYWRIGHT_ROOT}/"):
        raise SkipPolicyError("Playwright spec is outside the release test root")
    return {
        "project": project,
        "spec_file": spec_file,
        "spec_title": _normalize_text(
            authorization["spec_title"], label="Playwright spec title"
        ),
        "test_id": test_id,
        "category": category,
        "reason": _normalize_text(
            authorization["reason"], label="Playwright skip reason"
        ),
    }


def _authorization_key(authorization: Mapping[str, str]) -> str:
    return json.dumps(
        dict(authorization),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _authorization_sort_key(
    runner: str, authorization: Mapping[str, str]
) -> tuple[str, ...]:
    fields = (
        ("nodeid", "phase", "category", "reason")
        if runner == "pytest"
        else (
            "project",
            "spec_file",
            "spec_title",
            "test_id",
            "category",
            "reason",
        )
    )
    return tuple(authorization[field] for field in fields)


def _runtime_authorizations(
    policy: dict[str, Any], runner: str, environment: str
) -> set[str]:
    matches: list[set[str]] = []
    seen_ids: set[str] = set()
    for scope in policy["runtime_scopes"]:
        _validate_common_scope(
            scope,
            expected_keys={"id", "runner", "owner", "reason", "scope"},
        )
        if scope["id"] in seen_ids:
            raise SkipPolicyError("release test skip policy has duplicate runtime ids")
        seen_ids.add(scope["id"])
        details = scope["scope"]
        if set(details) != {"environment", "authorizations"}:
            raise SkipPolicyError("runtime skip scope has an invalid schema")
        declared = details["authorizations"]
        if (
            not isinstance(details["environment"], str)
            or not details["environment"].strip()
            or not isinstance(declared, list)
        ):
            raise SkipPolicyError("runtime skip authorizations are invalid")
        normalized_items = [
            _normalize_runtime_authorization(scope["runner"], item) for item in declared
        ]
        keys = [_authorization_key(item) for item in normalized_items]
        if len(keys) != len(set(keys)) or normalized_items != sorted(
            normalized_items,
            key=lambda item: _authorization_sort_key(scope["runner"], item),
        ):
            raise SkipPolicyError(
                "runtime skip authorizations must be canonical, sorted, and unique"
            )
        if declared != normalized_items:
            raise SkipPolicyError(
                "runtime skip authorizations are not canonically normalized"
            )
        if scope["runner"] == runner and details["environment"] == environment:
            matches.append(set(keys))
    if len(matches) != 1:
        raise SkipPolicyError(
            f"runtime policy must match {runner}/{environment} exactly once"
        )
    return matches[0]


def _normalize_runtime_path(raw: str, repo: Path = REPO) -> str:
    if not isinstance(raw, str):
        raise SkipPolicyError("runtime skip path must be text")
    path = raw.replace("\\", "/")
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            path = candidate.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError as exc:
            raise SkipPolicyError("runtime skip points outside the repository") from exc
    return _safe_relative_path(path)


def authorize_runtime_skip(
    *,
    policy: dict[str, Any],
    runner: str,
    environment: str,
    observation: Mapping[str, str],
) -> None:
    normalized = _normalize_runtime_authorization(runner, dict(observation))
    allowed = _runtime_authorizations(policy, runner, environment)
    if _authorization_key(normalized) not in allowed:
        identity = normalized.get("nodeid") or (
            f"{normalized.get('project')}/{normalized.get('spec_file')}/"
            f"{normalized.get('test_id')}"
        )
        raise SkipPolicyError(f"unexpected {runner} runtime skip identity: {identity}")


def _iter_playwright_specs(suites: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(suites, list):
        raise SkipPolicyError("Playwright report suites must be a list")
    for suite in suites:
        if not isinstance(suite, dict):
            raise SkipPolicyError("Playwright report suite is invalid")
        specs = suite.get("specs", [])
        if not isinstance(specs, list):
            raise SkipPolicyError("Playwright report specs must be a list")
        for spec in specs:
            if not isinstance(spec, dict):
                raise SkipPolicyError("Playwright report spec is invalid")
            yield spec
        yield from _iter_playwright_specs(suite.get("suites", []))


def verify_playwright_report(
    report_path: Path,
    policy: dict[str, Any],
    *,
    environment: str,
) -> int:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkipPolicyError("Playwright JSON report cannot be read") from exc
    if not isinstance(report, dict) or not isinstance(report.get("stats"), dict):
        raise SkipPolicyError("Playwright JSON report schema is invalid")
    skipped = 0
    for spec in _iter_playwright_specs(report.get("suites")):
        path = spec.get("file")
        spec_id = spec.get("id")
        spec_title = spec.get("title")
        tests = spec.get("tests")
        if (
            not isinstance(path, str)
            or not isinstance(spec_id, str)
            or not isinstance(spec_title, str)
            or not isinstance(tests, list)
        ):
            raise SkipPolicyError("Playwright report test identity is invalid")
        if not path.startswith(f"{PLAYWRIGHT_ROOT}/"):
            path = f"{PLAYWRIGHT_ROOT}/{path}"
        for test in tests:
            if not isinstance(test, dict):
                raise SkipPolicyError("Playwright report test is invalid")
            if test.get("status") != "skipped":
                continue
            skipped += 1
            annotations = test.get("annotations")
            if not isinstance(annotations, list):
                raise SkipPolicyError("Playwright skipped test has no annotations")
            skip_annotations = [
                annotation
                for annotation in annotations
                if isinstance(annotation, dict)
                and annotation.get("type") in {"skip", "fixme"}
                and isinstance(annotation.get("description"), str)
                and annotation["description"].strip()
            ]
            if len(skip_annotations) != 1:
                raise SkipPolicyError(
                    f"Playwright skip must have exactly one explicit annotation: {path}"
                )
            annotation = skip_annotations[0]
            project = test.get("projectName")
            if not isinstance(project, str):
                raise SkipPolicyError("Playwright skipped test has no project identity")
            authorize_runtime_skip(
                policy=policy,
                runner="playwright",
                environment=environment,
                observation={
                    "project": project,
                    "spec_file": path,
                    "spec_title": spec_title,
                    "test_id": spec_id,
                    "category": annotation["type"],
                    "reason": annotation["description"],
                },
            )
    declared_total = report["stats"].get("skipped")
    if not isinstance(declared_total, int) or declared_total != skipped:
        raise SkipPolicyError("Playwright skipped count does not match report contents")
    return skipped


def _policy_from_environment() -> tuple[dict[str, Any], str] | None:
    raw_path = os.environ.get("OMEGA_RELEASE_TEST_SKIP_POLICY")
    environment = os.environ.get("OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT")
    if raw_path is None and environment is None:
        return None
    if not raw_path or not environment:
        raise SkipPolicyError("pytest release skip guard environment is incomplete")
    policy = load_policy(Path(raw_path))
    verify_source_policy(policy)
    _runtime_authorizations(policy, "pytest", environment)
    return policy, environment


def pytest_configure(config: Any) -> None:
    global _ACTIVE_PYTEST_CONFIG

    try:
        state = _policy_from_environment()
    except SkipPolicyError as exc:
        import pytest

        pytest.exit(f"RELEASE TEST SKIP POLICY BLOCKED: {exc}", returncode=4)
    config._omega_release_skip_state = state
    config._omega_release_skip_errors = []
    config._omega_release_selected = []
    config._omega_release_deselected = []
    config._omega_release_collection_skips = []
    config._omega_release_runtest_reports = []
    _ACTIVE_PYTEST_CONFIG = config


def _pytest_skip_reason(report: Any) -> str:
    was_xfail = getattr(report, "wasxfail", None)
    if isinstance(was_xfail, str) and was_xfail.strip():
        raw = was_xfail
        if raw.startswith("reason: "):
            raw = raw.removeprefix("reason: ")
        return _normalize_text(raw, label="pytest xfail reason")
    longrepr = getattr(report, "longrepr", "")
    if isinstance(longrepr, tuple) and len(longrepr) >= 3:
        raw = str(longrepr[2])
    else:
        reprcrash = getattr(longrepr, "reprcrash", None)
        message = getattr(reprcrash, "message", None)
        raw = str(message if message is not None else longrepr)
    if raw.startswith("Skipped: "):
        raw = raw.removeprefix("Skipped: ")
    return _normalize_text(raw, label="pytest skip reason")


def _pytest_skip_category(report: Any, reason: str) -> str:
    if isinstance(getattr(report, "wasxfail", None), str):
        return "xfail"
    if reason.startswith("could not import "):
        return "importorskip"
    return "skip"


def _check_pytest_report(report: Any, config: Any) -> None:
    state = getattr(config, "_omega_release_skip_state", None)
    observed_xfail = isinstance(getattr(report, "wasxfail", None), str)
    if state is None or not (getattr(report, "skipped", False) or observed_xfail):
        return
    policy, environment = state
    try:
        reason = _pytest_skip_reason(report)
        category = _pytest_skip_category(report, reason)
        phase = str(getattr(report, "when", "collect"))
        if phase not in {"setup", "call", "teardown"}:
            phase = "collect"
        authorize_runtime_skip(
            policy=policy,
            runner="pytest",
            environment=environment,
            observation={
                "nodeid": str(getattr(report, "nodeid", "")),
                "phase": phase,
                "category": category,
                "reason": reason,
            },
        )
    except SkipPolicyError as exc:
        config._omega_release_skip_errors.append(str(exc))


def pytest_runtest_logreport(report: Any) -> None:
    if _ACTIVE_PYTEST_CONFIG is not None:
        phase = str(getattr(report, "when", ""))
        outcome = str(getattr(report, "outcome", ""))
        nodeid = str(getattr(report, "nodeid", ""))
        if phase in {"setup", "call", "teardown"} and outcome in {
            "passed",
            "failed",
            "skipped",
        } and nodeid:
            _ACTIVE_PYTEST_CONFIG._omega_release_runtest_reports.append(
                {
                    "nodeid": nodeid,
                    "phase": phase,
                    "outcome": outcome,
                    "xfail": isinstance(getattr(report, "wasxfail", None), str),
                }
            )
        _check_pytest_report(report, _ACTIVE_PYTEST_CONFIG)


def pytest_collectreport(report: Any) -> None:
    if _ACTIVE_PYTEST_CONFIG is not None:
        if getattr(report, "skipped", False):
            _ACTIVE_PYTEST_CONFIG._omega_release_collection_skips.append(
                str(getattr(report, "nodeid", ""))
            )
        _check_pytest_report(report, _ACTIVE_PYTEST_CONFIG)


def pytest_collection_finish(session: Any) -> None:
    session.config._omega_release_selected = sorted(
        str(item.nodeid) for item in session.items
    )


def pytest_deselected(items: list[Any]) -> None:
    if _ACTIVE_PYTEST_CONFIG is not None:
        _ACTIVE_PYTEST_CONFIG._omega_release_deselected.extend(
            str(item.nodeid) for item in items
        )


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    errors = getattr(session.config, "_omega_release_skip_errors", [])
    report_path = os.environ.get("OMEGA_RELEASE_PYTEST_REPORT")
    nonce = os.environ.get("OMEGA_RELEASE_PYTEST_NONCE")
    mode = os.environ.get("OMEGA_RELEASE_PYTEST_MODE")
    if report_path or nonce or mode:
        if (
            not report_path
            or not nonce
            or not re.fullmatch(r"[0-9a-f]{64}", nonce)
            or mode not in {"collect", "execute"}
        ):
            errors.append("external pytest report environment is incomplete")
        else:
            payload = {
                "schema_version": 1,
                "kind": "omega-release-pytest-report",
                "nonce": nonce,
                "mode": mode,
                "blocked": bool(errors),
                "errors": sorted(set(errors)),
                "selected": sorted(session.config._omega_release_selected),
                "deselected": sorted(session.config._omega_release_deselected),
                "collection_skips": sorted(
                    session.config._omega_release_collection_skips
                ),
                "reports": sorted(
                    session.config._omega_release_runtest_reports,
                    key=lambda item: (item["nodeid"], item["phase"]),
                ),
            }
            try:
                with Path(report_path).open("x", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            except OSError:
                errors.append("external pytest report could not be created exclusively")
    if errors:
        for error in sorted(set(errors)):
            print(f"RELEASE TEST SKIP POLICY BLOCKED: {error}", file=sys.stderr)
        import pytest

        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--environment", default="release")
    parser.add_argument("--playwright-report", type=Path)
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.policy)
        scanned = verify_source_policy(policy)
        skipped = None
        if args.playwright_report is not None:
            skipped = verify_playwright_report(
                args.playwright_report,
                policy,
                environment=args.environment,
            )
    except SkipPolicyError as exc:
        print(f"RELEASE_TEST_SKIP_POLICY BLOCKED: {exc}", file=sys.stderr)
        return 1
    counts = ",".join(f"{runner}={len(items)}" for runner, items in scanned.items())
    suffix = f" runtime_skipped={skipped}" if skipped is not None else ""
    print(f"RELEASE_TEST_SKIP_POLICY PASS {counts}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
