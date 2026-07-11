"""Sprint v1.43.2 (Claude B8) — CI workflows for lint + security.

Static structural checks: the .github/workflows/*.yml files exist, are
well-formed YAML, and pin their tooling versions so a future ruff /
bandit / pip-audit release doesn't silently change what the CI
accepts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[1]
WF_DIR = REPO / ".github" / "workflows"

LINT_WF     = WF_DIR / "lint.yml"
SECURITY_WF = WF_DIR / "security.yml"
DOCKER_WF   = WF_DIR / "docker-image.yml"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_lint_workflow_exists():
    assert LINT_WF.exists(), f"missing {LINT_WF}"


def test_docker_image_workflow_exists():
    assert DOCKER_WF.exists(), f"missing {DOCKER_WF}"


def test_security_workflow_exists():
    assert SECURITY_WF.exists(), f"missing {SECURITY_WF}"


def test_lint_workflow_uses_ruff_with_pinned_version():
    doc = _load(LINT_WF)
    raw = LINT_WF.read_text(encoding="utf-8")
    # Pinned ruff version.
    assert re.search(r"ruff==\d+\.\d+\.\d+", raw), (
        "lint.yml must pin ruff to a specific version"
    )
    # Both check + format passes referenced.
    assert "ruff check" in raw
    assert "ruff format --check" in raw


def test_security_workflow_has_bandit_pinned():
    raw = SECURITY_WF.read_text(encoding="utf-8")
    assert re.search(r"bandit==\d+\.\d+\.\d+", raw), (
        "security.yml must pin bandit to a specific version"
    )
    assert "bandit -r" in raw


def test_security_workflow_has_pip_audit_pinned():
    raw = SECURITY_WF.read_text(encoding="utf-8")
    assert re.search(r"pip-audit==\d+\.\d+\.\d+", raw), (
        "security.yml must pin pip-audit to a specific version"
    )
    assert "pip-audit -r" in raw
    # v1.43.2 (DevOps R1 follow-up): switched osv → pypi after the
    # OSV API became unreachable from the v1.43.2 review sandbox.
    # Both feeds import GHSA; pypi was reproducible.
    assert "--vulnerability-service=pypi" in raw


@pytest.mark.parametrize("path", [LINT_WF, SECURITY_WF],
                         ids=lambda p: p.name)
def test_workflows_run_on_pr_and_push_main(path):
    """Both workflows must run on PR + push to main so CI catches
    regressions BEFORE merge AND on every main update."""
    doc = _load(path)
    # YAML parses ``on:`` as a Python ``True`` key sometimes
    # depending on the boolean-literal handling — accept both.
    on = doc.get("on") if "on" in doc else doc.get(True)
    assert on, f"{path.name} has no 'on:' trigger section"
    assert "pull_request" in on, f"{path.name} missing pull_request trigger"
    push = on.get("push") if isinstance(on, dict) else {}
    push_branches = (push or {}).get("branches") or []
    assert "main" in push_branches, (
        f"{path.name} must run on push to main"
    )


def test_security_workflow_runs_weekly():
    """Scheduled scan catches new CVEs against unchanged code — that's
    the whole reason pip-audit exists in CI."""
    raw = SECURITY_WF.read_text(encoding="utf-8")
    assert "schedule:" in raw
    assert re.search(r"cron:\s*['\"]?\d+\s+\d+\s+\*\s+\*\s+\d+", raw), (
        "security.yml schedule cron expression missing or malformed"
    )


def test_bandit_scans_all_python_services():
    raw = SECURITY_WF.read_text(encoding="utf-8")
    for svc in ("console", "workspace", "vault", "refinement", "mcp-infra"):
        assert svc in raw, f"security.yml does not include {svc} in bandit scope"
    # cartridges directory contains services that ship their own code.
    assert "cartridges" in raw, "security.yml missing cartridges scope"


def test_pip_audit_discovers_every_requirements_file():
    """v1.43.2 (DevOps R1 hardening): the pip-audit job now uses
    ``find`` to discover every requirements.txt in the tree —
    previously it hand-listed 5 services and missed cartridge +
    test requirements. A CVE there would have shipped undetected."""
    raw = SECURITY_WF.read_text(encoding="utf-8")
    assert "find . -name requirements.txt" in raw, (
        "security.yml must discover requirements.txt at runtime, not "
        "hand-list a static set of services"
    )
    # All real requirements.txt files in the repo must therefore be
    # implicitly covered. Sanity-check that the discovery is broad
    # enough to include cartridges + tests.
    real_paths = sorted(
        str(p.relative_to(REPO))
        for p in REPO.rglob("requirements.txt")
        if ".git" not in p.parts and "node_modules" not in p.parts
        and "vendor" not in p.parts
    )
    # We expect to see at least the core services + cartridges + tests.
    assert any("cartridges/" in p for p in real_paths), (
        "test sanity: no cartridge requirements.txt found in repo — "
        "expected the find-loop to cover them"
    )


def test_lint_workflow_scans_all_python_services():
    raw = LINT_WF.read_text(encoding="utf-8")
    for svc in ("console", "workspace", "vault", "refinement", "mcp-infra",
                "cartridges"):
        assert svc in raw, f"lint.yml does not include {svc} in ruff scope"


def test_python_coverage_is_published_in_ci():
    raw = DOCKER_WF.read_text(encoding="utf-8")
    assert "coverage run --parallel-mode" in raw
    assert "coverage combine" in raw
    assert "python-coverage.xml" in raw
    assert "python-coverage.json" in raw
    assert "name: python-coverage" in raw


def test_compose_validate_declares_required_pair_keys():
    raw = DOCKER_WF.read_text(encoding="utf-8")
    for key in (
        "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
        "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE",
        "INTERNAL_API_KEY_BANXICO_TO_CONSOLE",
        "INTERNAL_API_KEY_INEGI_TO_CONSOLE",
        "INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE",
        "INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE",
    ):
        assert key in raw


def test_console_next_coverage_is_published_in_ci():
    raw = LINT_WF.read_text(encoding="utf-8")
    package = (REPO / "console-next" / "package.json").read_text(encoding="utf-8")
    assert "test:coverage" in raw
    assert "console-next-coverage" in raw
    assert "console-next/coverage/" in raw
    assert "--coverage.all=true" in package
    assert "@vitest/coverage-v8" in package


def test_console_next_component_tests_cover_operational_shell():
    """Audit guard: keep frontend unit coverage above the old 2-test floor."""
    src = REPO / "console-next" / "src"
    test_files = {
        p.relative_to(src).as_posix()
        for p in src.rglob("*.test.ts*")
        if "node_modules" not in p.parts
    }

    assert len(test_files) >= 8
    for expected in (
        "components/AppSidebar.test.tsx",
        "components/cartridges/StatusBadge.test.tsx",
        "components/dashboard/FreshnessTable.test.tsx",
        "components/dashboard/KpiCard.test.tsx",
        "components/monitor/JobTable.test.tsx",
        "components/monitor/StatusPill.test.tsx",
    ):
        assert expected in test_files


@pytest.mark.parametrize("path", [LINT_WF, SECURITY_WF],
                         ids=lambda p: p.name)
def test_workflows_declare_least_privilege_permissions(path):
    """v1.43.2 (DevOps R1 hardening): default GITHUB_TOKEN scope is
    overly permissive (write to most APIs). Both workflows must
    explicitly downgrade to ``contents: read``."""
    doc = _load(path)
    perms = doc.get("permissions")
    assert perms is not None, (
        f"{path.name} must declare a top-level ``permissions:`` block "
        "to scope GITHUB_TOKEN — default is over-privileged."
    )
    assert perms.get("contents") == "read", (
        f"{path.name} permissions.contents must be ``read``, got "
        f"{perms.get('contents')!r}"
    )
