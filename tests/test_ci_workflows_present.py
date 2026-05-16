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


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_lint_workflow_exists():
    assert LINT_WF.exists(), f"missing {LINT_WF}"


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
    # OSV is the documented vulnerability service in the plan.
    assert "--vulnerability-service=osv" in raw


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
    # cartridges directory contains 4 services that ship their own code.
    assert "cartridges" in raw, "security.yml missing cartridges scope"


def test_pip_audit_iterates_each_requirements_file():
    raw = SECURITY_WF.read_text(encoding="utf-8")
    for svc in ("console", "workspace", "vault", "refinement", "mcp-infra"):
        assert f"{svc}/requirements.txt" in raw, (
            f"security.yml pip-audit step missing {svc}/requirements.txt"
        )


def test_lint_workflow_scans_all_python_services():
    raw = LINT_WF.read_text(encoding="utf-8")
    for svc in ("console", "workspace", "vault", "refinement", "mcp-infra",
                "cartridges"):
        assert svc in raw, f"lint.yml does not include {svc} in ruff scope"
