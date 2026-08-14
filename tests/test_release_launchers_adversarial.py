from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.run_release_playwright as playwright_launcher
from scripts.run_release_pytest import ReleasePytestError, _reconcile, _run
from scripts.verify_release_test_harness import HarnessSealError, verify
from scripts.verify_release_test_harness import discover as discover_harness

REPO = Path(__file__).resolve().parents[1]


def test_release_harness_rejects_a_regenerated_mutable_seal_before_inventory(
    tmp_path: Path,
) -> None:
    original = b'{"files":[],"schema_version":1}\n'
    seal = tmp_path / "release-test-harness-seal.json"
    seal.write_bytes(original)
    expected = hashlib.sha256(original).hexdigest()

    # Models changing a test and regenerating the mutable checkout seal. The
    # action-bound digest remains tied to the reviewed GITHUB_SHA bytes.
    seal.write_text(
        '{"files":[{"path":"tests/test_fake.py","sha256":"'
        + "0" * 64
        + '"}],"schema_version":1}\n',
        encoding="utf-8",
    )

    with pytest.raises(HarnessSealError, match="source-bound authority"):
        verify(
            seal_path=seal,
            repo=tmp_path,
            environ={
                "OMEGA_RELEASE_DIGEST_STACK": "1",
                "OMEGA_RELEASE_TEST_HARNESS_SHA256": expected,
            },
        )


def test_release_harness_requires_an_action_bound_digest(tmp_path: Path) -> None:
    seal = tmp_path / "release-test-harness-seal.json"
    seal.write_text('{"files":[],"schema_version":1}\n', encoding="utf-8")

    with pytest.raises(HarnessSealError, match="missing or invalid"):
        verify(
            seal_path=seal,
            repo=tmp_path,
            environ={"OMEGA_RELEASE_DIGEST_STACK": "1"},
        )


def test_bootstrap_verifier_and_launchers_never_execute_poisoned_policy(
    tmp_path: Path,
) -> None:
    for relative in discover_harness():
        source = REPO / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    seal_source = REPO / ".github/release-test-harness-seal.json"
    seal_destination = tmp_path / ".github/release-test-harness-seal.json"
    seal_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(seal_source, seal_destination)
    sentinel = tmp_path / "policy-executed"
    (tmp_path / "scripts/verify_test_skip_policy.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['POLICY_EXEC_SENTINEL']).write_text('executed')\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "OMEGA_RELEASE_DIGEST_STACK": "1",
        "OMEGA_RELEASE_TEST_HARNESS_SHA256": hashlib.sha256(
            seal_destination.read_bytes()
        ).hexdigest(),
        "OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT": "release",
        "POLICY_EXEC_SENTINEL": str(sentinel),
    }
    commands = (
        [sys.executable, "-I", "scripts/verify_release_test_harness.py"],
        [
            sys.executable,
            "-I",
            "scripts/run_release_pytest.py",
            "tests/test_release_dotenv.py",
        ],
        [sys.executable, "-I", "scripts/run_release_playwright.py"],
    )

    for command in commands:
        result = subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert not sentinel.exists()


def test_isolated_pytest_parent_blocks_sitecustomize_before_child_start(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "sitecustomize-executed"
    (tmp_path / "sitecustomize.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['SITECUSTOMIZE_SENTINEL']).write_text('executed')\n"
        "report = Path(os.environ.get('OMEGA_RELEASE_PYTEST_REPORT', 'forged.json'))\n"
        "report.write_text('{}')\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(REPO / "scripts/run_release_pytest.py"),
            "tests/release_launcher_adversarial",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "OMEGA_RELEASE_TEST_HARNESS_SHA256": hashlib.sha256(
                (REPO / ".github/release-test-harness-seal.json").read_bytes()
            ).hexdigest(),
            "OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT": "release",
            "OMEGA_RELEASE_TEST_SKIP_POLICY": str(
                REPO / ".github/release-test-skip-policy.json"
            ),
            "PYTHONPATH": str(tmp_path),
            "SITECUSTOMIZE_SENTINEL": str(sentinel),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert not sentinel.exists()
    assert "PYTHONPATH is forbidden" in result.stderr


def test_isolated_pytest_child_does_not_execute_report_forging_sitecustomize(
    tmp_path: Path,
) -> None:
    poison_root = tmp_path / "poison-root"
    poison_root.mkdir()
    sentinel = tmp_path / "sitecustomize-executed"
    (poison_root / "sitecustomize.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "Path(os.environ['SITECUSTOMIZE_SENTINEL']).write_text('executed')\n"
        "mode = os.environ['OMEGA_RELEASE_PYTEST_MODE']\n"
        "nodeid = 'tests/release_launcher_adversarial/test_exitstatus_bypass.py::test_required_gate'\n"
        "reports = [] if mode == 'collect' else [\n"
        "  {'nodeid': nodeid, 'phase': phase, 'outcome': 'passed', 'xfail': False}\n"
        "  for phase in ('setup', 'call', 'teardown')\n"
        "]\n"
        "payload = {\n"
        "  'schema_version': 1, 'kind': 'omega-release-pytest-report',\n"
        "  'nonce': os.environ['OMEGA_RELEASE_PYTEST_NONCE'], 'mode': mode,\n"
        "  'blocked': False, 'errors': [], 'selected': [nodeid],\n"
        "  'deselected': [], 'collection_skips': [], 'reports': reports,\n"
        "}\n"
        "Path(os.environ['OMEGA_RELEASE_PYTEST_REPORT']).write_text(json.dumps(payload))\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    report = tmp_path / "collection.json"
    target = "tests/release_launcher_adversarial/test_exitstatus_bypass.py"
    previous = os.environ.get("SITECUSTOMIZE_SENTINEL")
    os.environ["SITECUSTOMIZE_SENTINEL"] = str(sentinel)
    try:
        _completed, payload = _run(
            args=[target],
            config=config,
            report=report,
            mode="collect",
            import_roots=[poison_root, REPO],
        )
    finally:
        if previous is None:
            os.environ.pop("SITECUSTOMIZE_SENTINEL", None)
        else:
            os.environ["SITECUSTOMIZE_SENTINEL"] = previous

    assert not sentinel.exists()
    assert payload["selected"] == [
        f"{target}::test_required_gate"
    ]


@pytest.mark.parametrize("shadow", ("pytest.py", "pytest_asyncio.py", "anyio.py"))
def test_pytest_child_never_imports_repo_runner_shadow(
    tmp_path: Path, shadow: str
) -> None:
    sentinel = tmp_path / "runner-shadow-executed"
    shadow_path = REPO / shadow
    shadow_path.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['RUNNER_SHADOW_SENTINEL']).write_text('executed')\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    try:
        result = _run_pytest_launcher(
            REPO / "tests" / "release_launcher_adversarial",
            extra_env={"RUNNER_SHADOW_SENTINEL": str(sentinel)},
        )
    finally:
        shadow_path.unlink(missing_ok=True)

    assert result.returncode != 0, result.stdout + result.stderr
    assert not sentinel.exists()
    assert "runner shadow" in result.stderr


def _run_pytest_launcher(
    target: Path, *, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "scripts/run_release_pytest.py", str(target)],
        cwd=REPO,
        env={
            **os.environ,
            "OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT": "release",
            "OMEGA_RELEASE_TEST_SKIP_POLICY": str(
                REPO / ".github" / "release-test-skip-policy.json"
            ),
            "OMEGA_RELEASE_TEST_HARNESS_SHA256": hashlib.sha256(
                (REPO / ".github/release-test-harness-seal.json").read_bytes()
            ).hexdigest(),
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


def test_playwright_launcher_blocks_replaced_runner_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    e2e = tmp_path / "tests-e2e"
    modules = e2e / "node_modules"
    for relative in ("@playwright/test", "playwright", "playwright-core", ".bin"):
        (modules / relative).mkdir(parents=True, exist_ok=True)
    (e2e / "package-lock.json").write_text('{"lockfileVersion":3}\n')
    cli = modules / "playwright" / "cli.js"
    cli.write_text("// reviewed cli\n", encoding="utf-8")
    runner = modules / ".bin" / "playwright"
    runner.symlink_to("../playwright/cli.js")
    (modules / "@playwright" / "test" / "index.js").write_text("// test\n")
    (modules / "playwright-core" / "index.js").write_text("// core\n")
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    node.chmod(0o755)
    browsers = tmp_path / "browsers"
    browsers.mkdir()
    (browsers / "chromium").write_text("reviewed-browser", encoding="utf-8")
    sentinel = tmp_path / "fake-playwright-executed"

    monkeypatch.setattr(playwright_launcher, "E2E", e2e)
    monkeypatch.setattr(playwright_launcher, "PLAYWRIGHT", runner)
    monkeypatch.setattr(playwright_launcher, "PLAYWRIGHT_CLI", cli)
    monkeypatch.setattr(playwright_launcher, "PLAYWRIGHT_NODE", node)
    monkeypatch.setattr(playwright_launcher, "PLAYWRIGHT_BROWSERS", browsers)
    expected = playwright_launcher.playwright_runtime_sha256()
    runner.unlink()
    runner.write_text(
        "#!/bin/sh\nprintf executed >\"${PLAYWRIGHT_SENTINEL}\"\nexit 0\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)
    monkeypatch.setenv("OMEGA_RELEASE_DIGEST_STACK", "1")
    monkeypatch.setenv(
        playwright_launcher.EXPECTED_RUNTIME_SHA256_ENV,
        expected,
    )
    monkeypatch.setenv("PLAYWRIGHT_SENTINEL", str(sentinel))
    monkeypatch.setattr(playwright_launcher, "verify", lambda: None)

    assert playwright_launcher.main([]) == 1
    assert not sentinel.exists()


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
