from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.verify_test_skip_policy import (
    DEFAULT_POLICY,
    REPO,
    SkipPolicyError,
    _file_inventory,
    _inventory_digest,
    _pytest_skip_category,
    _pytest_skip_reason,
    authorize_runtime_skip,
    load_policy,
    main,
    scan_declarations,
    verify_playwright_report,
    verify_source_policy,
)


NESTED_PYTEST_BOOTSTRAP = """
import sys
from pathlib import Path

import pytest

repo = Path(sys.argv.pop(1)).resolve(strict=True)
sys.path.insert(0, str(repo))
raise SystemExit(pytest.console_main())
"""
NESTED_RELEASE_PYTHON_VARIABLES = {
    "OMEGA_RELEASE_PYTEST_REPORT",
    "OMEGA_RELEASE_PYTEST_NONCE",
    "OMEGA_RELEASE_PYTEST_MODE",
    "OMEGA_RELEASE_PYTEST_OWNER_PID",
    "PYTHONPATH",
}


def _run_nested_pytest(
    test_file: Path, *, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTEST_PLUGINS": "scripts.verify_test_skip_policy",
        "OMEGA_RELEASE_TEST_SKIP_POLICY": str(DEFAULT_POLICY),
        "OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT": "release",
        **(extra_env or {}),
    }
    for variable in NESTED_RELEASE_PYTHON_VARIABLES:
        env.pop(variable, None)
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            NESTED_PYTEST_BOOTSTRAP,
            str(REPO),
            "-q",
            str(test_file),
        ],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_current_exact_source_inventory_is_sealed() -> None:
    policy = load_policy()
    scanned = verify_source_policy(policy)

    assert len(scanned["pytest"]) == 80
    assert len(scanned["playwright"]) == 27
    assert len({declaration.path for declaration in scanned["pytest"]}) == 53
    assert len({declaration.path for declaration in scanned["playwright"]}) == 7


def test_changed_declaration_digest_fails_closed() -> None:
    policy = copy.deepcopy(load_policy())
    policy["source_scopes"][0]["scope"]["files"][0]["declarations_sha256"] = "0" * 64

    with pytest.raises(SkipPolicyError, match="exact per-file"):
        verify_source_policy(policy)


def test_runtime_skip_requires_exact_nodeid_phase_category_and_reason() -> None:
    policy = copy.deepcopy(load_policy())
    observation = {
        "nodeid": "tests/test_e2e_full_flow.py::test_e2e_01_admin_can_login",
        "phase": "call",
        "category": "skip",
        "reason": "Stack not running",
    }
    pytest_scope = next(
        scope for scope in policy["runtime_scopes"] if scope["runner"] == "pytest"
    )
    pytest_scope["scope"]["authorizations"] = [observation]
    authorize_runtime_skip(
        policy=policy,
        runner="pytest",
        environment="release",
        observation={**observation, "reason": "  Stack   not running  "},
    )

    for field, value in (
        ("nodeid", "tests/test_e2e_full_flow.py::test_different"),
        ("phase", "setup"),
        ("category", "xfail"),
        ("reason", "dependency vanished"),
    ):
        with pytest.raises(SkipPolicyError, match="runtime skip identity"):
            authorize_runtime_skip(
                policy=policy,
                runner="pytest",
                environment="release",
                observation={**observation, field: value},
            )


def _playwright_report(
    authorization: dict[str, str], *, annotated: bool
) -> dict[str, object]:
    annotations = (
        [
            {
                "type": authorization["category"],
                "description": authorization["reason"],
            }
        ]
        if annotated
        else []
    )
    return {
        "suites": [
            {
                "specs": [
                    {
                        "file": authorization["spec_file"],
                        "id": authorization["test_id"],
                        "title": authorization["spec_title"],
                        "tests": [
                            {
                                "status": "skipped",
                                "projectName": authorization["project"],
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
    playwright_scope = next(
        scope for scope in policy["runtime_scopes"] if scope["runner"] == "playwright"
    )
    authorization = playwright_scope["scope"]["authorizations"][0]
    report = tmp_path / "results.json"
    report.write_text(
        json.dumps(_playwright_report(authorization, annotated=True)),
        encoding="utf-8",
    )

    assert verify_playwright_report(report, policy, environment="release") == 1

    for field, value in (
        ("project", "mobile-chromium"),
        ("spec_file", "tests-e2e/specs/12-control-room.spec.ts"),
        ("spec_title", "different title"),
        ("test_id", "0" * 20 + "-" + "1" * 20),
        ("category", "fixme"),
        ("reason", "different reason"),
    ):
        changed = {**authorization, field: value}
        report.write_text(
            json.dumps(_playwright_report(changed, annotated=True)),
            encoding="utf-8",
        )
        with pytest.raises(SkipPolicyError, match="runtime skip identity"):
            verify_playwright_report(report, policy, environment="release")

    report.write_text(
        json.dumps(_playwright_report(authorization, annotated=False)),
        encoding="utf-8",
    )
    with pytest.raises(SkipPolicyError, match="exactly one explicit annotation"):
        verify_playwright_report(report, policy, environment="release")


def test_pytest_plugin_blocks_runtime_skip_outside_reviewed_scope(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_unexpected_release_skip.py"
    test_file.write_text(
        "import pytest\n\ndef test_required_gate():\n    pytest.skip('unexpected')\n",
        encoding="utf-8",
    )
    result = _run_nested_pytest(test_file)

    assert result.returncode != 0
    assert "RELEASE TEST SKIP POLICY BLOCKED" in result.stdout + result.stderr


def test_pytest_plugin_blocks_undeclared_xpass_as_observed_xfail(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_undeclared_release_xfail.py"
    test_file.write_text(
        "import pytest\n\n@pytest.mark.xfail(reason='undeclared known defect')\n"
        "def test_required_gate():\n    assert True\n",
        encoding="utf-8",
    )
    result = _run_nested_pytest(test_file)

    assert result.returncode != 0
    assert "RELEASE TEST SKIP POLICY BLOCKED" in result.stdout + result.stderr


def test_nested_pytest_never_executes_repo_sitecustomize(tmp_path: Path) -> None:
    test_file = tmp_path / "test_nested_pass.py"
    test_file.write_text("def test_nested_pass():\n    assert True\n", encoding="utf-8")
    sentinel = tmp_path / "sitecustomize-executed"
    shadow = REPO / "sitecustomize.py"
    assert not shadow.exists()
    shadow.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['REPO_SITECUSTOMIZE_SENTINEL']).write_text('executed')\n",
        encoding="utf-8",
    )
    try:
        result = _run_nested_pytest(
            test_file,
            extra_env={
                "PYTHONPATH": str(REPO),
                "OMEGA_RELEASE_PYTEST_REPORT": str(tmp_path / "outer-report.json"),
                "OMEGA_RELEASE_PYTEST_NONCE": "b" * 64,
                "OMEGA_RELEASE_PYTEST_MODE": "execute",
                "OMEGA_RELEASE_PYTEST_OWNER_PID": "1",
                "REPO_SITECUSTOMIZE_SENTINEL": str(sentinel),
            },
        )
    finally:
        shadow.unlink(missing_ok=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not sentinel.exists()


@pytest.mark.parametrize(
    ("longrepr", "wasxfail", "category", "reason"),
    [
        (
            ("test_gate.py", 4, "Skipped: optional dependency"),
            None,
            "skip",
            "optional dependency",
        ),
        (
            ("test_gate.py", 4, "Skipped: could not import 'duckdb'"),
            None,
            "importorskip",
            "could not import 'duckdb'",
        ),
        (
            ("test_gate.py", 4, "ignored"),
            "reason: known defect",
            "xfail",
            "known defect",
        ),
    ],
)
def test_pytest_runtime_categories_and_reasons_are_canonical(
    longrepr: object,
    wasxfail: str | None,
    category: str,
    reason: str,
) -> None:
    report = SimpleNamespace(longrepr=longrepr, wasxfail=wasxfail)

    observed_reason = _pytest_skip_reason(report)

    assert observed_reason == reason
    assert _pytest_skip_category(report, observed_reason) == category


def test_cli_reports_exact_static_inventory(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--policy", str(DEFAULT_POLICY)]) == 0
    output = capsys.readouterr()
    assert output.out == "RELEASE_TEST_SKIP_POLICY PASS pytest=80,playwright=27\n"
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
                "scope": {"environment": "release", "authorizations": []},
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


@pytest.mark.parametrize(
    "source",
    [
        '__import__("pytest").skip("bypass")',
        'getattr(pytest, "".join(["s", "kip"]))("bypass")',
        'vars(pytest)["skip"]("bypass")',
        'pytest.__dict__["skip"]("bypass")',
    ],
)
def test_recognizable_dynamic_pytest_skip_access_is_blocked(
    tmp_path: Path, source: str
) -> None:
    test_file = tmp_path / "tests" / "test_dynamic.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        f"import pytest\n\ndef test_required():\n    {source}\n",
        encoding="utf-8",
    )

    with pytest.raises(SkipPolicyError, match="dynamic skip"):
        scan_declarations(tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "test['skip'](true, 'bypass');",
        "testInfo.skip(true, 'bypass');",
        "const { skip } = test; skip(true, 'bypass');",
    ],
)
def test_recognizable_dynamic_playwright_skip_access_is_blocked(
    tmp_path: Path, source: str
) -> None:
    spec = tmp_path / "tests-e2e" / "specs" / "dynamic.spec.ts"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "import { test } from '@playwright/test';\n"
        f"test('required', async ({{ testInfo }}) => {{ {source} }});\n",
        encoding="utf-8",
    )

    with pytest.raises(SkipPolicyError, match="indirect Playwright"):
        scan_declarations(tmp_path)


def test_dynamic_skip_in_an_authorized_file_cannot_inherit_path_authority() -> None:
    policy = load_policy()

    with pytest.raises(SkipPolicyError, match="runtime skip identity"):
        authorize_runtime_skip(
            policy=policy,
            runner="pytest",
            environment="release",
            observation={
                "nodeid": "tests/test_health_and_auth.py::test_health_endpoints_and_auth",
                "phase": "call",
                "category": "skip",
                "reason": "red-team dynamic bypass",
            },
        )

    with pytest.raises(SkipPolicyError, match="runtime skip identity"):
        authorize_runtime_skip(
            policy=policy,
            runner="playwright",
            environment="release",
            observation={
                "project": "desktop-chromium",
                "spec_file": "tests-e2e/specs/05-studio-deep.spec.ts",
                "spec_title": "renders cartridge selector dropdown",
                "test_id": "44fa61789b32b3cf8820-1d43117300198f5117b0",
                "category": "skip",
                "reason": "red-team dynamic bypass",
            },
        )
