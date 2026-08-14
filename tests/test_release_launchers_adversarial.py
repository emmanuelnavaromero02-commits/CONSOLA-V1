from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.run_release_playwright as playwright_launcher
from scripts.run_release_pytest import ReleasePytestError, _reconcile

REPO = Path(__file__).resolve().parents[1]


def _run_pytest_launcher(
    target: Path, *, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/run_release_pytest.py", str(target)],
        cwd=REPO,
        env={
            **os.environ,
            "OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT": "release",
            "OMEGA_RELEASE_TEST_SKIP_POLICY": str(
                REPO / ".github" / "release-test-skip-policy.json"
            ),
            **(extra_env or {}),
        },
        text=True,
        capture_output=True,
        check=False,
    )


def test_external_launcher_blocks_trylast_exitstatus_zero_bypass() -> None:
    target = REPO / "tests" / "release_launcher_adversarial"

    result = _run_pytest_launcher(
        target, extra_env={"OMEGA_ADVERSARIAL_EXITSTATUS_ZERO": "1"}
    )

    assert result.returncode != 0
    assert "failed test" in result.stderr


def test_launcher_owned_config_ignores_nested_pytest_ini_plugin_suppression() -> None:
    target = REPO / "tests" / "release_launcher_adversarial"

    result = _run_pytest_launcher(target)

    assert result.returncode == 0, result.stderr
    assert "RELEASE PYTEST PASS" in result.stdout


def test_reconcile_rejects_failed_report_even_if_child_exitstatus_is_zero() -> None:
    collection = {
        "selected": ["tests/test_gate.py::test_required"],
        "deselected": [],
        "collection_skips": [],
        "reports": [],
    }
    execution = {
        "selected": collection["selected"],
        "deselected": [],
        "collection_skips": [],
        "reports": [
            {"nodeid": collection["selected"][0], "phase": "setup", "outcome": "passed", "xfail": False},
            {"nodeid": collection["selected"][0], "phase": "call", "outcome": "failed", "xfail": False},
            {"nodeid": collection["selected"][0], "phase": "teardown", "outcome": "passed", "xfail": False},
        ],
    }

    with pytest.raises(ReleasePytestError, match="failed test"):
        _reconcile(collection, execution)


def test_playwright_launcher_blocks_global_setup_prewritten_green_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed_setup = tmp_path / "global-setup.ts"
    sealed_setup.write_text("export default async () => {};\n", encoding="utf-8")
    sealed_bytes = sealed_setup.read_bytes()
    fake = tmp_path / "playwright"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "path = os.environ['PLAYWRIGHT_JSON_OUTPUT_FILE']\n"
        "if '--list' in sys.argv:\n"
        "  report = {'config': {}, 'errors': [], 'stats': {'expected': 1, 'skipped': 0, 'unexpected': 0, 'flaky': 0}, 'suites': [{'specs': [{'file': 'specs/gate.spec.ts', 'id': 'gate-id', 'title': 'required', 'tests': [{'projectName': 'desktop-chromium'}]}], 'suites': []}]}\n"
        "else:\n"
        "  # Models globalSetup forging a complete green report and exiting zero.\n"
        "  open(os.environ['FAKE_SEALED_SETUP'], 'a', encoding='utf-8').write('// mutated\\n')\n"
        "  report = {'config': {}, 'errors': [], 'stats': {'expected': 1, 'skipped': 0, 'unexpected': 0, 'flaky': 0}, 'suites': [{'specs': [{'file': 'specs/gate.spec.ts', 'id': 'gate-id', 'title': 'required', 'tests': [{'projectName': 'desktop-chromium', 'status': 'expected'}]}], 'suites': []}]}\n"
        "open(path, 'w', encoding='utf-8').write(json.dumps(report))\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("FAKE_SEALED_SETUP", str(sealed_setup))
    monkeypatch.setattr(playwright_launcher, "PLAYWRIGHT", fake)

    def verify_fixture_seal() -> None:
        if sealed_setup.read_bytes() != sealed_bytes:
            raise playwright_launcher.HarnessSealError(
                "globalSetup changed during Playwright execution"
            )

    monkeypatch.setattr(playwright_launcher, "verify", verify_fixture_seal)

    assert playwright_launcher.main() == 1


def test_playwright_inventory_rejects_per_test_failure_hidden_by_clean_stats() -> None:
    report = {
        "suites": [
            {
                "specs": [
                    {
                        "file": "specs/gate.spec.ts",
                        "id": "gate-id",
                        "title": "required",
                        "tests": [
                            {
                                "projectName": "desktop-chromium",
                                "status": "unexpected",
                            }
                        ],
                    }
                ],
                "suites": [],
            }
        ]
    }

    with pytest.raises(
        playwright_launcher.PlaywrightGateError,
        match="non-release-clean terminal status",
    ):
        playwright_launcher._inventory(report, require_terminal_outcomes=True)
