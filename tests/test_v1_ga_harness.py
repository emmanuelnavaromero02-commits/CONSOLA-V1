from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "v1_stress" / "run_v1_ga.py"


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def _run_harness(tmp_path: Path, mode: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "OMEGA_V1_GA_DRY_RUN": "1",
            "OMEGA_V1_GA_RUN_ID": "STRESS_TEST_RUN",
            "OMEGA_V1_GA_EVIDENCE_ROOT": str(tmp_path),
        }
    )
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(SCRIPT), mode],
        cwd=REPO,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_makefile_exposes_unified_v1_ga_targets():
    makefile = _read("Makefile")
    for target, mode in (
        ("v1-ga-lite-local", "lite-local"),
        ("v1-ga-lite-aws", "lite-aws"),
        ("v1-ga-max-aws", "max-aws"),
        ("v1-ga-cleanup", "cleanup"),
        ("v1-ga-report", "report"),
    ):
        assert f"{target}:" in makefile
        assert f"scripts/v1_stress/run_v1_ga.py {mode}" in makefile
    assert "unified v1 GA lite harness locally" in makefile
    assert "requires dedicated staging + chaos confirmation" in makefile


def test_lite_local_dry_run_writes_all_required_evidence(tmp_path: Path):
    result = _run_harness(tmp_path, "lite-local")
    assert result.returncode == 0, result.stdout

    evidence = tmp_path / "STRESS_TEST_RUN" / "local"
    for relative in (
        "REPORT.md",
        "summary.json",
        "A-seed.log",
        "B-isolation-matrix.csv",
        "C-security-gauntlet.csv",
        "D-locust-report.html",
        "E-control-room-e2e.json",
        "F-copilot-adversarial/prompts.jsonl",
        "G-factory/security-scan.txt",
        "H-chaos/commands-log.sh",
        "I-db-audit/rls-status.csv",
        "cleanup-proof.md",
    ):
        assert (evidence / relative).exists(), relative

    report = (evidence / "REPORT.md").read_text(encoding="utf-8")
    for phase in "ABCDEFGHI":
        assert f"| {phase} |" in report
    assert "overall: `PASS`" in report
    assert "v1.0 public requires `v1-ga-max-aws` PASS" in report


def test_lite_aws_requires_public_console_url(tmp_path: Path):
    result = _run_harness(tmp_path, "lite-aws", PUBLIC_CONSOLE_URL="")
    assert result.returncode == 2
    report = (tmp_path / "STRESS_TEST_RUN" / "aws" / "REPORT.md").read_text(encoding="utf-8")
    assert "PUBLIC_CONSOLE_URL required" in report
    assert "BLOCKED" in report


def test_max_aws_blocks_without_dedicated_staging_and_chaos(tmp_path: Path):
    result = _run_harness(
        tmp_path,
        "max-aws",
        PUBLIC_CONSOLE_URL="http://modecissions-public-255609366.us-east-1.elb.amazonaws.com",
    )
    assert result.returncode == 2
    report = (tmp_path / "STRESS_TEST_RUN" / "aws" / "REPORT.md").read_text(encoding="utf-8")
    assert "OMEGA_V1_STRESS_TARGET=staging is required" in report
    assert "OMEGA_V1_STRESS_ALLOW_CHAOS=1 is required" in report
    assert "Dedicated staging confirmation is required" in report
    assert "Public shared AWS URL cannot run Max" in report


def test_security_gauntlet_csv_has_required_categories(tmp_path: Path):
    result = _run_harness(tmp_path, "lite-local")
    assert result.returncode == 0, result.stdout
    gauntlet = (tmp_path / "STRESS_TEST_RUN" / "local" / "C-security-gauntlet.csv").read_text(
        encoding="utf-8"
    )
    for category in ("auth/session", "api/web", "copilot/mcp", "infra", "secrets"):
        assert category in gauntlet
    for vector in ("csrf_forged", "sqli_payload", "aws_metadata_ssrf", "bearer_redaction"):
        assert vector in gauntlet


def test_max_aws_live_credentials_are_blocked_not_done(tmp_path: Path):
    result = _run_harness(
        tmp_path,
        "max-aws",
        PUBLIC_CONSOLE_URL="https://staging.example.test",
        OMEGA_V1_STRESS_TARGET="staging",
        OMEGA_V1_STRESS_ALLOW_CHAOS="1",
        OMEGA_V1_GA_DEDICATED_STAGING="1",
        OMEGA_V1_GA_PUBLIC_URL_OVERRIDE="1",
        ANTHROPIC_API_KEY="",
        HUBSPOT_ACCESS_TOKEN="",
    )
    assert result.returncode == 2
    report = (tmp_path / "STRESS_TEST_RUN" / "aws" / "REPORT.md").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY missing" in report
    assert "HubSpot live sandbox token missing" in report or "AWS max 50-tenant seed" in report
    assert "BLOCKED" in report
