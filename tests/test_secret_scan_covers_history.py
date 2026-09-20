"""The secret-scanning gate must look at history, not only at the tree.

Removing a file from the working tree does not remove it from a published
repository. Between 2026-05-10 and 2026-09-20 an infra/.env.save was readable
by anyone who cloned this repository, and the tree-only gate was green the
whole time because the file had been deleted in #72.

These checks keep the history job honest. The failure mode they guard against
is not a missing job — it is a job that runs and finds nothing: a shallow
clone, or --no-git, would make it pass while reading almost no history.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/secret-scan.yml"
HISTORY_CONFIG = ROOT / ".gitleaks-history.toml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _history_job() -> str:
    text = _workflow()
    start = text.index("gitleaks-history:")
    return text[start:]


def test_a_history_job_exists():
    assert "gitleaks-history:" in _workflow()


def test_history_job_checks_out_every_commit():
    # actions/checkout defaults to depth 1. Without this the job would scan a
    # single commit and report success.
    assert re.search(r"fetch-depth:\s*0", _history_job())


def test_history_job_does_not_disable_git():
    # --no-git turns the scan into a working-tree scan, which is the very gap
    # this job exists to close.
    job = _history_job()
    assert "detect --source /repo --redact" in job
    assert "--no-git" not in job


def test_both_jobs_pin_the_same_scanner_version():
    versions = set(re.findall(r"zricethezav/gitleaks:(v[\d.]+)", _workflow()))
    assert len(versions) == 1, f"jobs disagree on the scanner version: {versions}"


def test_history_config_extends_the_tree_config():
    # One ruleset, two questions. If the history config drifted into its own
    # rules, a secret could be caught in the tree and ignored in history.
    text = HISTORY_CONFIG.read_text(encoding="utf-8")
    assert re.search(r'path\s*=\s*"\.gitleaks\.toml"', text)


def test_every_allowlisted_commit_carries_a_written_reason():
    # An allowlist entry is a decision to accept a known exposure. Undocumented
    # entries become invisible, and the next reader cannot tell an accepted
    # finding from one that was silenced to make CI green.
    text = HISTORY_CONFIG.read_text(encoding="utf-8")
    block = re.search(r"commits\s*=\s*\[(.*?)\]", text, re.S)
    assert block, "the history config must declare its commit allowlist"
    body = block.group(1)
    shas = re.findall(r'"([0-9a-f]{40})"', body)
    assert shas, "allowlisted commits must be pinned by full 40-character SHA"
    for sha in shas:
        before = body[: body.index(sha)]
        commented = [ln for ln in before.splitlines() if ln.strip().startswith("#")]
        assert commented, f"commit {sha[:12]} is allowlisted without any explanation"
