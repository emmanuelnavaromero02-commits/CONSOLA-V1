#!/usr/bin/env python3
"""Run Playwright with an external collection/execution reconciliation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.verify_test_skip_policy import (
    DEFAULT_POLICY,
    PLAYWRIGHT_ROOT,
    load_policy,
    verify_playwright_report,
)
from scripts.verify_release_test_harness import HarnessSealError, verify

E2E = REPO / "tests-e2e"
PLAYWRIGHT = E2E / "node_modules" / ".bin" / "playwright"


class PlaywrightGateError(RuntimeError):
    pass


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
    command = [
        str(PLAYWRIGHT),
        "test",
        "--config=playwright.config.ts",
        "--reporter=json",
        *extra,
    ]
    return subprocess.run(command, cwd=E2E, env=environment, check=False)


def main() -> int:
    try:
        configured_environment = os.environ.get("OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT")
        if configured_environment not in {None, "release"}:
            raise PlaywrightGateError("release skip environment override is forbidden")
        configured_policy = os.environ.get("OMEGA_RELEASE_TEST_SKIP_POLICY")
        if configured_policy is not None and Path(configured_policy).resolve() != DEFAULT_POLICY.resolve():
            raise PlaywrightGateError("release skip policy override is forbidden")
        verify()
        if not PLAYWRIGHT.is_file() or not os.access(PLAYWRIGHT, os.X_OK):
            raise PlaywrightGateError("local locked Playwright binary is unavailable")
        with tempfile.TemporaryDirectory(prefix="omega-release-playwright-") as directory:
            root = Path(directory)
            collection_path = root / "collection.json"
            execution_path = root / "execution.json"
            collection_run = _run(collection_path, "--list")
            verify()
            if collection_run.returncode != 0:
                raise PlaywrightGateError("Playwright collection failed")
            collection = _load(collection_path, "collection")
            expected, _collection_outcomes = _inventory(collection)
            execution_run = _run(execution_path)
            verify()
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
    except (HarnessSealError, PlaywrightGateError) as exc:
        print(f"RELEASE PLAYWRIGHT BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(f"RELEASE PLAYWRIGHT PASS: {len(actual)} project tests reconciled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
