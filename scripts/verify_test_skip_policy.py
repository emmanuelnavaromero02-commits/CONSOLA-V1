#!/usr/bin/env python3
"""Fail-closed release policy for pytest and Playwright skips.

The source inventory records every skip declaration, not merely a count.  In
release CI this module is also loaded as a pytest plugin and turns a runtime
skip outside the reviewed path scope into a failing test session.  Playwright's
JSON report is checked separately after each browser gate.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


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
                    raise SkipPolicyError("wildcard imports from skip-capable test APIs are forbidden")
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
        if not isinstance(node, ast.Call):
            continue
        kind = _resolve_python_name(node.func, aliases)
        if kind not in PYTEST_CALLS:
            if kind == "getattr" and len(node.args) >= 2:
                target = _resolve_python_name(node.args[0], aliases)
                attribute = node.args[1]
                if (
                    target in {"pytest", "unittest"}
                    and isinstance(attribute, ast.Constant)
                    and isinstance(attribute.value, str)
                    and attribute.value in {"skip", "skipif", "importorskip", "xfail"}
                ):
                    raise SkipPolicyError(
                        f"dynamic skip API access is forbidden: {relative}:{node.lineno}"
                    )
            continue
        call_functions.add(id(node.func))
        declarations.append(
            _python_declaration(text=text, relative=relative, node=node, kind=kind)
        )
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)) or id(node) in call_functions:
            continue
        kind = _resolve_python_name(node, aliases)
        if kind in PYTEST_CALLS:
            parent = parents.get(id(node))
            is_decorator = isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ) and node in parent.decorator_list
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


def _mask_typescript_non_code(text: str) -> str:
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
                chars[index] = " "
                if index + 1 < len(chars):
                    if chars[index + 1] != "\n":
                        chars[index + 1] = " "
                    index += 2
                    continue
            if char == quote:
                chars[index] = " "
                index += 1
                state = "code"
                continue
            if char != "\n":
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
        raise SkipPolicyError(f"cannot read Playwright test source: {relative}") from exc
    masked = _mask_typescript_non_code(text)
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
    alias_pattern = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
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
        segment = text[match.start():end]
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
            if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
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
        if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
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
    if len(scopes) != 2 or {scope.get("runner") for scope in scopes if isinstance(scope, dict)} != {
        "pytest",
        "playwright",
    }:
        raise SkipPolicyError("source policy must declare pytest and Playwright exactly once")
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
        if (
            not isinstance(declared_files, list)
            or any(
                not isinstance(item, dict)
                or set(item) != {"path", "declarations_sha256"}
                or not isinstance(item["path"], str)
                or not isinstance(item["declarations_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["declarations_sha256"])
                for item in declared_files
            )
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


def _runtime_paths(
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
        if set(details) != {"environment", "paths"}:
            raise SkipPolicyError("runtime skip scope has an invalid schema")
        paths = details["paths"]
        if (
            not isinstance(paths, list)
            or paths != sorted(set(paths))
            or any(not isinstance(path, str) for path in paths)
        ):
            raise SkipPolicyError("runtime skip paths must be a sorted unique list")
        normalized = {_safe_relative_path(path) for path in paths}
        if scope["runner"] == runner and details["environment"] == environment:
            matches.append(normalized)
    if len(matches) != 1:
        raise SkipPolicyError(
            f"runtime policy must match {runner}/{environment} exactly once"
        )
    return matches[0]


def _normalize_runtime_path(raw: str, repo: Path = REPO) -> str:
    path = raw.split("::", 1)[0].replace("\\", "/")
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
    test_path: str,
    reason: str,
) -> None:
    normalized_path = _normalize_runtime_path(test_path)
    if not reason.strip():
        raise SkipPolicyError(f"{runner} skip has no explicit reason: {normalized_path}")
    allowed = _runtime_paths(policy, runner, environment)
    if normalized_path not in allowed:
        raise SkipPolicyError(
            f"unexpected {runner} skip outside authorized scope: {normalized_path}"
        )


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
        tests = spec.get("tests")
        if not isinstance(path, str) or not isinstance(tests, list):
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
            reasons = [
                annotation.get("description")
                for annotation in annotations
                if isinstance(annotation, dict)
                and annotation.get("type") in {"skip", "fixme"}
                and isinstance(annotation.get("description"), str)
                and annotation["description"].strip()
            ]
            if not reasons:
                raise SkipPolicyError(
                    f"unexpected Playwright skip without explicit annotation: {path}"
                )
            authorize_runtime_skip(
                policy=policy,
                runner="playwright",
                environment=environment,
                test_path=path,
                reason="; ".join(reasons),
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
    _runtime_paths(policy, "pytest", environment)
    return policy, environment


def pytest_configure(config: Any) -> None:
    global _ACTIVE_PYTEST_CONFIG

    try:
        state = _policy_from_environment()
    except SkipPolicyError as exc:
        import pytest

        pytest.exit(f"RELEASE TEST SKIP POLICY BLOCKED: {exc}", returncode=4)
    setattr(config, "_omega_release_skip_state", state)
    setattr(config, "_omega_release_skip_errors", [])
    _ACTIVE_PYTEST_CONFIG = config


def _check_pytest_report(report: Any, config: Any) -> None:
    state = getattr(config, "_omega_release_skip_state", None)
    if state is None or not getattr(report, "skipped", False):
        return
    policy, environment = state
    reason = str(getattr(report, "longrepr", ""))
    try:
        authorize_runtime_skip(
            policy=policy,
            runner="pytest",
            environment=environment,
            test_path=str(getattr(report, "nodeid", "")),
            reason=reason,
        )
    except SkipPolicyError as exc:
        getattr(config, "_omega_release_skip_errors").append(str(exc))


def pytest_runtest_logreport(report: Any) -> None:
    if _ACTIVE_PYTEST_CONFIG is not None:
        _check_pytest_report(report, _ACTIVE_PYTEST_CONFIG)


def pytest_collectreport(report: Any) -> None:
    if _ACTIVE_PYTEST_CONFIG is not None:
        _check_pytest_report(report, _ACTIVE_PYTEST_CONFIG)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    errors = getattr(session.config, "_omega_release_skip_errors", [])
    if not errors:
        return
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
