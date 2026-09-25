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
    assert re.search(r"fetch-depth:\s*0", _history_job())


def test_history_job_does_not_disable_git():
    job = _history_job()
    assert "detect --source /repo --redact" in job
    assert "--no-git" not in job


def test_both_jobs_pin_the_same_scanner_version():
    versions = set(re.findall(r"zricethezav/gitleaks:(v[\d.]+)", _workflow()))
    assert len(versions) == 1, f"jobs disagree on the scanner version: {versions}"


def test_history_config_extends_the_tree_config():
    text = HISTORY_CONFIG.read_text(encoding="utf-8")
    assert re.search(r'path\s*=\s*"\.gitleaks\.toml"', text)


def test_every_allowlisted_commit_carries_a_written_reason():
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
