"""Sprint v1.43.4 — Claude L1: E2E tests must not silently skip in
CI without making the misconfiguration visible.

Static guards for the v1.43.4 changes that close the audit finding:

  * tests/e2e/conftest.py has an autouse session-scoped fixture
    that fails (not skips) when E2E_REQUIRE_STACK=1 + CI=true +
    E2E_ADMIN_PASSWORD is missing.
  * .github/workflows/docker-image.yml passes the E2E secret to
    pytest invocations so the fixture can read it.
"""
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
    """Codex/Claude L1 explicitly requested an autouse fixture
    named ``require_admin_password_in_ci``. Lock the name so any
    future "let's just delete this" change has to face the test
    failure first."""
    src = _e2e_conftest()
    assert "def require_admin_password_in_ci" in src, (
        "tests/e2e/conftest.py must define require_admin_password_in_ci "
        "(v1.43.4 Claude L1)"
    )


def test_e2e_conftest_fixture_is_autouse_and_session_scoped():
    """Otherwise the fixture only fires when a test explicitly opts
    in (which would defeat the purpose: the misconfiguration would
    only surface for tests that already knew about the password)."""
    src = _e2e_conftest()
    # Look for the fixture decorator immediately preceding the def.
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
    """The whole point of L1: stop skipping silently. The fixture
    body must call ``pytest.fail`` (loud) instead of ``pytest.skip``."""
    src = _e2e_conftest()
    # Find the fixture body and assert pytest.fail appears in it.
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
    """Every major CI provider sets CI=true. Lock the contract so a
    future refactor doesn't accidentally narrow detection to one
    provider (e.g. GITHUB_ACTIONS only)."""
    src = _e2e_conftest()
    assert "CI" in src and "environ" in src
    assert "RUNNING_IN_CI" in src, (
        "tests/e2e/conftest.py should define a RUNNING_IN_CI constant "
        "so the intent is searchable"
    )


def test_workflow_passes_e2e_admin_password_secret():
    """The fixture is the gate; the secret has to actually reach
    pytest for the wire-up to be useful. Verify the workflow's
    'Run root tests/' step exports the secret."""
    src = _ci_workflow()
    # The secret reference must appear inside an env: block on the
    # "Run root tests/" step.
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
    """Visible Studio demo controls must fail loudly when absent or broken.

    Optional/deep Studio probes can still document unsupported surfaces, but
    the primary demo spec must not turn missing controls into green CI.
    """
    src = (REPO / "tests-e2e/specs/05-studio.spec.ts").read_text(encoding="utf-8")
    assert "test.skip(" not in src
