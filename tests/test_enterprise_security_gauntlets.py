from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_copilot_redteam_contains_required_adversarial_vectors():
    script = _read("scripts/copilot_redteam.py")
    for needle in (
        "Ignora instrucciones previas",
        "lista todos los tenants",
        "Muestra secretos de Vault",
        "BEGIN PRIVATE KEY",
        "bash reverse shell",
        "DROP TABLE",
        "approval",
    ):
        assert needle in script


def test_copilot_redteam_dry_run_emits_evidence(tmp_path: Path):
    env = os.environ.copy()
    env.update({"OMEGA_COPILOT_REDTEAM_DRY_RUN": "1"})
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "copilot_redteam.py"), "--evidence-dir", str(tmp_path)],
        cwd=REPO,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0, result.stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "PASS"
    assert (tmp_path / "prompts.jsonl").exists()


def test_cartridge_resilience_matrix_covers_upstream_failure_modes():
    script = _read("scripts/cartridge_resilience.py")
    for needle in (
        "429 rate limit",
        "401 token expired",
        "500 intermittent",
        "timeout",
        "schema drift",
        "duplicate upstream",
        "incomplete pagination",
        "malicious prompt payload",
        "scripts/run_live_cartridge_checks.sh",
    ):
        assert needle in script


def test_cartridge_resilience_dry_run_blocks_live_sandbox_honestly(tmp_path: Path):
    env = os.environ.copy()
    env.update({"OMEGA_CARTRIDGE_RESILIENCE_DRY_RUN": "1", "OMEGA_ENABLE_LIVE_CARTRIDGE_TESTS": "0"})
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "cartridge_resilience.py"),
            "--workload",
            "sap_successfactors",
            "--evidence-dir",
            str(tmp_path),
        ],
        cwd=REPO,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 2, result.stdout
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "BLOCKED"
    report = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "live cartridge sandbox" in report
    assert "OMEGA_ENABLE_LIVE_CARTRIDGE_TESTS=1" in report
