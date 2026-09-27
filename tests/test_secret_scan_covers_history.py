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


TREE_CONFIG = ROOT / ".gitleaks.toml"


def _pr_range_job() -> str:
    text = _workflow()
    return text[text.index("gitleaks-pr-range:") :]


def test_tree_config_exempts_no_paths():
    text = TREE_CONFIG.read_text(encoding="utf-8")
    assert not re.search(r"^\s*paths\s*=", text, re.M), "the tree scan must cover every path"
    assert "tests?/" not in text
    assert ".test" not in text


def test_every_tree_literal_allowance_is_commented():
    lines = TREE_CONFIG.read_text(encoding="utf-8").splitlines()
    literals = [index for index, line in enumerate(lines) if line.strip().startswith("'''")]
    assert len(literals) == 2
    for index in literals:
        assert lines[index - 1].strip().startswith("#"), lines[index]


def test_history_path_allowances_are_dated_and_explained():
    text = HISTORY_CONFIG.read_text(encoding="utf-8")
    block = re.search(r"paths\s*=\s*\[(.*?)\n\]", text, re.S)
    assert block
    lines = block.group(1).splitlines()
    patterns = [index for index, line in enumerate(lines) if line.strip().startswith("'''")]
    assert patterns
    for index in patterns:
        previous = [line for line in lines[:index] if line.strip()]
        assert previous[-1].strip().startswith("#") or previous[-1].strip().startswith("'''"), lines[index]
    assert "'''(^|/)tests?/'''" in block.group(1)
    assert "Historical fixtures triaged as fakes, 2026-09-26" in block.group(1)


def test_pull_request_commits_are_scanned_with_the_strict_tree_config():
    job = _pr_range_job()
    assert "if: github.event_name == 'pull_request'" in job
    assert re.search(r"fetch-depth:\s*0", job)
    assert "BASE_REF: ${{ github.base_ref }}" in job
    assert '--log-opts="origin/${BASE_REF}..HEAD"' in job
    assert "--config /repo/.gitleaks.toml" in job
    assert "--no-git" not in job
    assert "${{ github.base_ref }}" not in job.split("run: |", 1)[1]
