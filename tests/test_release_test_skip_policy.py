from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.verify_test_skip_policy import (
    DEFAULT_POLICY,
    REPO,
    SkipPolicyError,
    _file_inventory,
    _inventory_digest,
    authorize_runtime_skip,
    load_policy,
    main,
    scan_declarations,
    verify_playwright_report,
    verify_source_policy,
)


def test_current_exact_source_inventory_is_sealed() -> None:
    policy = load_policy()
    scanned = verify_source_policy(policy)

    assert len(scanned["pytest"]) == 61
    assert len(scanned["playwright"]) == 27
    assert len({declaration.path for declaration in scanned["pytest"]}) == 39
    assert len({declaration.path for declaration in scanned["playwright"]}) == 7


def test_changed_declaration_digest_fails_closed() -> None:
    policy = copy.deepcopy(load_policy())
    policy["source_scopes"][0]["scope"]["files"][0][
        "declarations_sha256"
    ] = "0" * 64

    with pytest.raises(SkipPolicyError, match="exact per-file"):
        verify_source_policy(policy)


def test_runtime_skip_requires_an_exact_reviewed_path_and_reason() -> None:
    policy = load_policy()
    authorize_runtime_skip(
        policy=policy,
        runner="pytest",
        environment="release",
        test_path="tests/test_e2e_full_flow.py::test_e2e_01_admin_can_login",
        reason="Stack not running",
    )

    with pytest.raises(SkipPolicyError, match="outside authorized scope"):
        authorize_runtime_skip(
            policy=policy,
            runner="pytest",
            environment="release",
            test_path="tests/test_required_release_gate.py::test_required",
            reason="dependency vanished",
        )
    with pytest.raises(SkipPolicyError, match="no explicit reason"):
        authorize_runtime_skip(
            policy=policy,
            runner="pytest",
            environment="release",
            test_path="tests/test_e2e_full_flow.py::test_e2e_01_admin_can_login",
            reason="",
        )


def _playwright_report(path: str, *, annotated: bool) -> dict[str, object]:
    annotations = [{"type": "skip", "description": "optional upstream"}] if annotated else []
    return {
        "suites": [
            {
                "specs": [
                    {
                        "file": path,
                        "tests": [
                            {
                                "status": "skipped",
                                "annotations": annotations,
                            }
                        ],
                    }
                ],
                "suites": [],
            }
        ],
        "stats": {"skipped": 1},
    }


def test_playwright_report_accepts_only_explicit_reviewed_skips(tmp_path: Path) -> None:
    policy = load_policy()
    report = tmp_path / "results.json"
    report.write_text(
        json.dumps(_playwright_report("01-login.spec.ts", annotated=True)),
        encoding="utf-8",
    )

    assert verify_playwright_report(report, policy, environment="release") == 1

    report.write_text(
        json.dumps(_playwright_report("12-control-room.spec.ts", annotated=True)),
        encoding="utf-8",
    )
    with pytest.raises(SkipPolicyError, match="outside authorized scope"):
        verify_playwright_report(report, policy, environment="release")

    report.write_text(
        json.dumps(_playwright_report("01-login.spec.ts", annotated=False)),
        encoding="utf-8",
    )
    with pytest.raises(SkipPolicyError, match="without explicit annotation"):
        verify_playwright_report(report, policy, environment="release")


def test_pytest_plugin_blocks_runtime_skip_outside_reviewed_scope(tmp_path: Path) -> None:
    test_file = tmp_path / "test_unexpected_release_skip.py"
    test_file.write_text(
        "import pytest\n\ndef test_required_gate():\n    pytest.skip('unexpected')\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTEST_PLUGINS": "scripts.verify_test_skip_policy",
        "OMEGA_RELEASE_TEST_SKIP_POLICY": str(DEFAULT_POLICY),
        "OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT": "release",
    }

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_file)],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "RELEASE TEST SKIP POLICY BLOCKED" in result.stdout + result.stderr


def test_cli_reports_exact_static_inventory(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--policy", str(DEFAULT_POLICY)]) == 0
    output = capsys.readouterr()
    assert output.out == "RELEASE_TEST_SKIP_POLICY PASS pytest=61,playwright=27\n"
    assert output.err == ""


def test_scanner_inventory_paths_are_exact_and_sorted() -> None:
    scanned = scan_declarations()
    for runner in ("pytest", "playwright"):
        paths = [declaration.path for declaration in scanned[runner]]
        assert paths == sorted(paths)
        assert all("\\" not in path and not path.startswith("/") for path in paths)


def _policy_for_repo(repo: Path) -> dict[str, object]:
    scanned = scan_declarations(repo)
    source_scopes = []
    runtime_scopes = []
    for runner in ("pytest", "playwright"):
        declarations = scanned[runner]
        paths = sorted({declaration.path for declaration in declarations})
        source_scopes.append(
            {
                "id": f"{runner}-source",
                "runner": runner,
                "owner": "release-engineering",
                "reason": "test fixture exact inventory",
                "scope": {
                    "files": _file_inventory(declarations),
                    "inventory_sha256": _inventory_digest(declarations),
                },
            }
        )
        runtime_scopes.append(
            {
                "id": f"{runner}-runtime",
                "runner": runner,
                "owner": "release-engineering",
                "reason": "test fixture runtime scope",
                "scope": {"environment": "release", "paths": paths},
            }
        )
    return {
        "schema_version": 1,
        "source_scopes": source_scopes,
        "runtime_scopes": runtime_scopes,
    }


def test_alias_bypasses_inside_an_authorized_file_are_rejected(tmp_path: Path) -> None:
    python_test = tmp_path / "tests/test_authorized.py"
    playwright_test = tmp_path / "tests-e2e/specs/authorized.spec.ts"
    python_test.parent.mkdir(parents=True)
    playwright_test.parent.mkdir(parents=True)
    python_test.write_text(
        "import pytest\n\ndef test_optional():\n    pytest.skip('declared')\n"
        "\ndef test_expected_failure():\n    pytest.xfail('declared xfail')\n",
        encoding="utf-8",
    )
    playwright_test.write_text(
        "import { test } from '@playwright/test';\n"
        "test('optional', () => { test.fixme(true, 'declared'); });\n"
        "test.describe.skip('optional group', () => {});\n",
        encoding="utf-8",
    )
    policy = _policy_for_repo(tmp_path)
    scanned = verify_source_policy(policy, tmp_path)
    assert {declaration.kind for declaration in scanned["pytest"]} == {
        "pytest.skip",
        "pytest.xfail",
    }
    assert {declaration.kind for declaration in scanned["playwright"]} == {
        "test.fixme",
        "test.describe.skip",
    }

    python_test.write_text(
        "import pytest\n\ndef test_optional():\n"
        "    alias = pytest.skip\n    alias('bypass')\n",
        encoding="utf-8",
    )
    with pytest.raises(SkipPolicyError, match="indirect skip API references"):
        verify_source_policy(policy, tmp_path)

    python_test.write_text(
        "from pytest import skip as declared_skip\n\n"
        "def test_optional():\n    declared_skip('declared')\n",
        encoding="utf-8",
    )
    playwright_test.write_text(
        "import { test } from '@playwright/test';\n"
        "const alias = test.skip;\n"
        "test('optional', () => { alias(true, 'bypass'); });\n",
        encoding="utf-8",
    )
    with pytest.raises(SkipPolicyError, match="indirect Playwright"):
        scan_declarations(tmp_path)
