from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/secret-scan.yml"
SECURITY_WORKFLOW = ROOT / ".github/workflows/security.yml"
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


def test_every_scan_pins_the_same_scanner_version():
    text = _workflow() + SECURITY_WORKFLOW.read_text(encoding="utf-8")
    versions = set(re.findall(r"zricethezav/gitleaks:(v[\d.]+)", text))
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


def _security_jobs() -> dict:
    return yaml.safe_load(SECURITY_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _run(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


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
    job = _security_jobs()["gitleaks-pr-range"]
    assert job["if"] == "github.event_name == 'pull_request'"
    assert job["steps"][0]["with"]["fetch-depth"] == 0
    step = job["steps"][1]
    assert step["env"] == {"BASE_REF": "${{ github.base_ref }}"}
    assert '--log-opts="origin/${BASE_REF}..HEAD"' in step["run"]
    assert "--config /repo/.gitleaks.toml" in step["run"]
    assert "--no-git" not in step["run"]
    assert "${{" not in step["run"]


def test_strict_scans_gate_the_merge_through_security_gate():
    jobs = _security_jobs()
    tree = _run(jobs["gitleaks-tree"])
    assert "if" not in jobs["gitleaks-tree"]
    assert "needs" not in jobs["gitleaks-tree"]
    assert "--no-git" in tree and "--config /repo/.gitleaks.toml" in tree
    gate = jobs["security-gate"]
    assert {"gitleaks-tree", "gitleaks-pr-range"} <= set(gate["needs"])
    script = gate["steps"][0]["run"]
    assert 'expect_result "gitleaks-tree" "${GITLEAKS_TREE_RESULT-}" "success"' in script
    assert 'expect_result "gitleaks-pr-range" "${GITLEAKS_PR_RANGE_RESULT-}" "$expected_pr_range"' in script


def test_secret_scan_workflow_keeps_only_the_history_scan():
    jobs = yaml.safe_load(_workflow())["jobs"]
    assert set(jobs) == {"gitleaks-history"}
    assert "--config /repo/.gitleaks-history.toml" in _run(jobs["gitleaks-history"])
