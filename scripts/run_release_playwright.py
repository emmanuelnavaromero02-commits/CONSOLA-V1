#!/usr/bin/env python3
"""Run Playwright with an external collection/execution reconciliation."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
def _load_harness_verifier():
    path = REPO / "scripts" / "verify_release_test_harness.py"
    spec = importlib.util.spec_from_file_location("_omega_harness_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("release harness verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_HARNESS = _load_harness_verifier()
HarnessSealError = _HARNESS.HarnessSealError
verify = _HARNESS.verify
try:
    verify()
except HarnessSealError as exc:
    print(f"RELEASE PLAYWRIGHT BLOCKED: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.verify_test_skip_policy import (  # noqa: E402
    DEFAULT_POLICY,
    PLAYWRIGHT_ROOT,
    load_policy,
    verify_playwright_report,
)

E2E = REPO / "tests-e2e"
PLAYWRIGHT = E2E / "node_modules" / ".bin" / "playwright"
PLAYWRIGHT_CLI = E2E / "node_modules" / "playwright" / "cli.js"
PLAYWRIGHT_NODE = Path(
    os.environ.get("OMEGA_RELEASE_PLAYWRIGHT_NODE", shutil.which("node") or "node")
)
PLAYWRIGHT_BROWSERS = Path(
    os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / ".cache" / "ms-playwright")
    )
)
EXPECTED_RUNTIME_SHA256_ENV = "OMEGA_RELEASE_PLAYWRIGHT_RUNTIME_SHA256"
SHA256 = re.compile(r"[0-9a-f]{64}")


class PlaywrightGateError(RuntimeError):
    pass


def _hash_runtime_path(digest: Any, path: Path, *, label: str) -> None:
    if not path.exists() and not path.is_symlink():
        raise PlaywrightGateError(f"Playwright runtime path is missing: {label}")
    paths = [path]
    if path.is_dir() and not path.is_symlink():
        paths.extend(sorted(path.rglob("*"), key=lambda item: item.as_posix()))
    for item in paths:
        relative = "." if item == path else item.relative_to(path).as_posix()
        metadata = item.lstat()
        digest.update(label.encode("utf-8") + b"\0")
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(f"{stat.S_IMODE(metadata.st_mode):04o}".encode("ascii") + b"\0")
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(b"symlink\0" + os.readlink(item).encode("utf-8") + b"\0")
        elif stat.S_ISDIR(metadata.st_mode):
            digest.update(b"directory\0")
        elif stat.S_ISREG(metadata.st_mode):
            digest.update(b"file\0")
            with item.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        else:
            raise PlaywrightGateError(
                f"Playwright runtime contains a special file: {label}/{relative}"
            )


def playwright_runtime_sha256() -> str:
    digest = hashlib.sha256()
    paths = (
        (E2E / "package-lock.json", "package-lock.json"),
        (E2E / "node_modules" / ".bin" / "playwright", ".bin/playwright"),
        (E2E / "node_modules" / "@playwright" / "test", "@playwright/test"),
        (E2E / "node_modules" / "playwright", "playwright"),
        (E2E / "node_modules" / "playwright-core", "playwright-core"),
        (PLAYWRIGHT_NODE, "node"),
        (PLAYWRIGHT_BROWSERS, "browsers"),
    )
    for path, label in paths:
        _hash_runtime_path(digest, path, label=label)
    return digest.hexdigest()


def verify_playwright_runtime() -> None:
    expected = os.environ.get(EXPECTED_RUNTIME_SHA256_ENV, "")
    required = os.environ.get("OMEGA_RELEASE_DIGEST_STACK") == "1"
    if not expected and not required:
        return
    if SHA256.fullmatch(expected) is None:
        raise PlaywrightGateError(
            "Playwright runtime authority SHA-256 is missing or invalid"
        )
    actual = playwright_runtime_sha256()
    if not hmac.compare_digest(actual, expected):
        raise PlaywrightGateError(
            "Playwright package/browser runtime differs from action-bound authority"
        )


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlaywrightGateError(f"{label} report missing or malformed") from exc
    if not isinstance(value, dict) or set(value) != {"config", "suites", "errors", "stats"}:
        raise PlaywrightGateError(f"{label} report schema is invalid")
    if value.get("errors") != [] or not isinstance(value.get("stats"), dict):
        raise PlaywrightGateError(f"{label} report contains runner errors")
    return value


def _inventory(
    report: dict[str, Any], *, require_terminal_outcomes: bool = False
) -> tuple[list[tuple[str, str, str, str]], dict[str, int]]:
    inventory: list[tuple[str, str, str, str]] = []
    outcomes = {"expected": 0, "skipped": 0}

    def visit(suites: object) -> None:
        if not isinstance(suites, list):
            raise PlaywrightGateError("Playwright suites inventory is invalid")
        for suite in suites:
            if not isinstance(suite, dict):
                raise PlaywrightGateError("Playwright suite is invalid")
            specs = suite.get("specs", [])
            if not isinstance(specs, list):
                raise PlaywrightGateError("Playwright specs inventory is invalid")
            for spec in specs:
                if not isinstance(spec, dict) or not isinstance(spec.get("tests"), list):
                    raise PlaywrightGateError("Playwright spec is invalid")
                path = spec.get("file")
                spec_id = spec.get("id")
                title = spec.get("title")
                if not all(isinstance(item, str) and item for item in (path, spec_id, title)):
                    raise PlaywrightGateError("Playwright spec identity is invalid")
                if not path.startswith(f"{PLAYWRIGHT_ROOT}/"):
                    path = f"{PLAYWRIGHT_ROOT}/{path}"
                for test in spec["tests"]:
                    project = test.get("projectName") if isinstance(test, dict) else None
                    if not isinstance(project, str) or not project:
                        raise PlaywrightGateError("Playwright project identity is invalid")
                    if require_terminal_outcomes:
                        status = test.get("status")
                        if status not in outcomes:
                            raise PlaywrightGateError(
                                "Playwright test has a non-release-clean terminal status"
                            )
                        outcomes[status] += 1
                    inventory.append((project, path, spec_id, title))
            visit(suite.get("suites", []))

    visit(report.get("suites"))
    if not inventory or len(inventory) != len(set(inventory)):
        raise PlaywrightGateError("Playwright inventory is empty or duplicated")
    return sorted(inventory), outcomes


def _run(output: Path, *extra: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output)
    environment["OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT"] = "release"
    environment["OMEGA_RELEASE_TEST_SKIP_POLICY"] = str(DEFAULT_POLICY)
    if os.environ.get(EXPECTED_RUNTIME_SHA256_ENV) or os.environ.get(
        "OMEGA_RELEASE_DIGEST_STACK"
    ) == "1":
        command = [str(PLAYWRIGHT_NODE), str(PLAYWRIGHT_CLI), "test"]
    else:
        command = [str(PLAYWRIGHT), "test"]
    command.extend(
        [
            "--config=playwright.config.ts",
            "--reporter=json",
            *extra,
        ]
    )
    return subprocess.run(command, cwd=E2E, env=environment, check=False)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args == ["--print-runtime-sha256"]:
            print(playwright_runtime_sha256())
            return 0
        if args == ["--verify-runtime-only"]:
            verify_playwright_runtime()
            print("RELEASE PLAYWRIGHT RUNTIME PASS")
            return 0
        if args:
            raise PlaywrightGateError("unreviewed Playwright launcher argument")
        configured_environment = os.environ.get("OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT")
        if configured_environment not in {None, "release"}:
            raise PlaywrightGateError("release skip environment override is forbidden")
        configured_policy = os.environ.get("OMEGA_RELEASE_TEST_SKIP_POLICY")
        if configured_policy is not None and Path(configured_policy).resolve() != DEFAULT_POLICY.resolve():
            raise PlaywrightGateError("release skip policy override is forbidden")
        verify()
        verify_playwright_runtime()
        if not PLAYWRIGHT.is_file() or not os.access(PLAYWRIGHT, os.X_OK):
            raise PlaywrightGateError("local locked Playwright binary is unavailable")
        with tempfile.TemporaryDirectory(prefix="omega-release-playwright-") as directory:
            root = Path(directory)
            collection_path = root / "collection.json"
            execution_path = root / "execution.json"
            collection_run = _run(collection_path, "--list")
            verify()
            verify_playwright_runtime()
            if collection_run.returncode != 0:
                raise PlaywrightGateError("Playwright collection failed")
            collection = _load(collection_path, "collection")
            expected, _collection_outcomes = _inventory(collection)
            execution_run = _run(execution_path)
            verify()
            verify_playwright_runtime()
            execution = _load(execution_path, "execution")
            actual, per_test_outcomes = _inventory(
                execution, require_terminal_outcomes=True
            )
            if actual != expected:
                raise PlaywrightGateError("Playwright execution inventory differs from collection")
            stats = execution["stats"]
            totals = [stats.get(key) for key in ("expected", "skipped", "unexpected", "flaky")]
            if (
                not all(isinstance(value, int) and not isinstance(value, bool) for value in totals)
                or sum(totals) != len(actual)
                or stats.get("unexpected") != 0
                or stats.get("flaky") != 0
                or stats.get("expected") != per_test_outcomes["expected"]
                or stats.get("skipped") != per_test_outcomes["skipped"]
            ):
                raise PlaywrightGateError("Playwright outcome totals are not release-clean")
            verify_playwright_report(
                execution_path,
                load_policy(),
                environment="release",
            )
            if execution_run.returncode != 0:
                raise PlaywrightGateError(
                    f"Playwright returned {execution_run.returncode}"
                )
            artifact = E2E / "playwright-report" / "results.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            temporary_artifact = artifact.with_suffix(".json.tmp")
            shutil.copyfile(execution_path, temporary_artifact)
            temporary_artifact.replace(artifact)
            verify()
            verify_playwright_runtime()
    except (HarnessSealError, PlaywrightGateError) as exc:
        print(f"RELEASE PLAYWRIGHT BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(f"RELEASE PLAYWRIGHT PASS: {len(actual)} project tests reconciled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
