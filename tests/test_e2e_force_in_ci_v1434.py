from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _e2e_conftest() -> str:
    return (REPO / "tests/e2e/conftest.py").read_text(encoding="utf-8")


def _ci_workflow() -> str:
    return (REPO / ".github/workflows/docker-image.yml").read_text(
        encoding="utf-8"
    )


def test_e2e_conftest_has_require_admin_password_in_ci_fixture():
    src = _e2e_conftest()
    assert "def require_admin_password_in_ci" in src, (
        "tests/e2e/conftest.py must define require_admin_password_in_ci "
        "(v1.43.4 L1)"
    )


def test_e2e_conftest_fixture_is_autouse_and_session_scoped():
    src = _e2e_conftest()
    m = re.search(
        r"@pytest\.fixture\((?P<args>[^)]*)\)\s*\ndef require_admin_password_in_ci",
        src,
    )
    assert m, "fixture decorator not found on require_admin_password_in_ci"
    args = m.group("args")
    assert "autouse=True" in args, (
        f"fixture must be autouse=True; got: {args!r}"
    )
    assert 'scope="session"' in args or "scope='session'" in args, (
        f"fixture must be scope='session'; got: {args!r}"
    )


def test_e2e_conftest_uses_pytest_fail_not_skip_for_ci_misconfig():
    src = _e2e_conftest()
    m = re.search(
        r"def require_admin_password_in_ci\(\):\s*\n(?P<body>(?:    .*\n)+)",
        src,
    )
    assert m, "fixture body not found"
    body = m.group("body")
    assert "pytest.fail(" in body, (
        "require_admin_password_in_ci must call pytest.fail() (not "
        "pytest.skip) so CI misconfig surfaces"
    )


def test_e2e_conftest_detects_ci_via_standard_env_var():
    src = _e2e_conftest()
    assert "CI" in src and "environ" in src
    assert "RUNNING_IN_CI" in src, (
        "tests/e2e/conftest.py should define a RUNNING_IN_CI constant "
        "so the intent is searchable"
    )


def test_workflow_passes_e2e_admin_password_secret():
    src = _ci_workflow()
    m = re.search(
        r"-\s*name:\s*Run root tests/.*?(?:run:|$)",
        src,
        re.DOTALL,
    )
    assert m, "workflow missing 'Run root tests/' step"
    step = m.group(0)
    assert "E2E_ADMIN_PASSWORD" in step, (
        "Run root tests/ step must pass E2E_ADMIN_PASSWORD env"
    )
    assert "secrets.E2E_ADMIN_PASSWORD" in step, (
        "E2E_ADMIN_PASSWORD must be sourced from a repo secret, not "
        "hardcoded"
    )


def test_primary_studio_e2e_does_not_skip_visible_demo_controls():
    src = (REPO / "tests-e2e/specs/05-studio.spec.ts").read_text(encoding="utf-8")
    assert "test.skip(" not in src
