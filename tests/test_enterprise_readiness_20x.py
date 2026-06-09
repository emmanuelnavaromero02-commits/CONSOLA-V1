from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "enterprise_readiness.py"


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def _run_enterprise(tmp_path: Path, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "OMEGA_ENTERPRISE_DRY_RUN": "1",
            "OMEGA_ENTERPRISE_RUN_ID": "ENT_TEST",
            "OMEGA_ENTERPRISE_EVIDENCE_ROOT": str(tmp_path),
            "PUBLIC_CONSOLE_URL": "http://modecissions-public-255609366.us-east-1.elb.amazonaws.com",
        }
    )
    env.update(env_overrides)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--target",
            "aws",
            "--workload",
            "sap_successfactors",
            "--profile",
            "beta-safe",
        ],
        cwd=REPO,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_makefile_exposes_enterprise_readiness_20x_targets():
    makefile = _read("Makefile")
    for target in (
        "stress-smoke",
        "stress-beta",
        "stress-spike",
        "stress-breakpoint",
        "stress-soak-24h",
        "stress-write-heavy",
        "data-integrity-audit",
        "copilot-redteam",
        "cartridge-resilience",
        "chaos-local",
        "chaos-aws",
        "enterprise-readiness",
        "rollback-rehearsal",
    ):
        assert f"{target}:" in makefile
    assert "scripts/enterprise_readiness.py" in makefile
    assert "scripts/data_integrity_audit.py" in makefile
    assert "scripts/copilot_redteam.py" in makefile
    assert "scripts/cartridge_resilience.py" in makefile
    for target in ("stress-smoke", "stress-beta", "stress-spike", "stress-write-heavy"):
        block = makefile[makefile.index(f"{target}:") : makefile.index("\n\n", makefile.index(f"{target}:"))]
        assert "$(MAKE) data-integrity-audit" in block


def test_enterprise_readiness_docs_define_status_and_unblock_semantics():
    doc = _read("docs/enterprise-readiness-20x.md")
    for needle in (
        "make enterprise-readiness TARGET=aws WORKLOAD=sap_successfactors PROFILE=beta-safe",
        "PASS",
        "FAIL",
        "BLOCKED",
        "cross-tenant",
        "visible secret",
        "mutation without approval",
        "p95",
        "chaos-aws",
    ):
        assert needle in doc


def test_stress_runner_supports_successfactors_workload_and_summary_gate():
    script = _read("scripts/run_stress.sh")
    locust = _read("tests/stress/locustfile.py")
    for needle in (
        "smoke)",
        "spike)",
        "breakpoint)",
        "soak-24h)",
        "write-heavy)",
        "OMEGA_STRESS_WORKLOAD",
        "scripts/stress_summary.py",
        "LOCUST_CODE",
        "SUMMARY_CODE",
    ):
        assert needle in script
    for needle in (
        "WORKLOAD",
        "sap_successfactors_employee_360",
        "/api/control-room/sap-successfactors/gold-kpis",
        "/api/bronze/query",
        "/viewer?type=schema",
        "forged workspace returned rows",
        "OMEGA_STRESS_ENABLE_SF_REFRESH",
    ):
        assert needle in locust


def test_enterprise_dry_run_writes_evidence_and_guards_prod_chaos(tmp_path: Path):
    result = _run_enterprise(tmp_path)
    assert result.returncode == 0, result.stdout

    evidence = tmp_path / "ENT_TEST"
    summary = (evidence / "summary.json").read_text(encoding="utf-8")
    report = (evidence / "REPORT.md").read_text(encoding="utf-8")
    assert '"status": "PASS"' in summary
    assert "production destructive chaos guard" in report
    assert "SAFE-ONLY" in report
    for step in (
        "stress smoke",
        "stress beta",
        "stress spike",
        "stress write-heavy",
        "copilot redteam",
        "cartridge resilience",
        "data integrity audit",
    ):
        assert step in report


def test_enterprise_aws_blocks_without_public_console_url(tmp_path: Path):
    env = os.environ.copy()
    env.update(
        {
            "OMEGA_ENTERPRISE_DRY_RUN": "1",
            "OMEGA_ENTERPRISE_RUN_ID": "ENT_BLOCKED",
            "OMEGA_ENTERPRISE_EVIDENCE_ROOT": str(tmp_path),
            "PUBLIC_CONSOLE_URL": "",
            "CONSOLE_URL": "",
        }
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--target", "aws", "--workload", "sap_successfactors"],
        cwd=REPO,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 2
    report = (tmp_path / "ENT_BLOCKED" / "REPORT.md").read_text(encoding="utf-8")
    assert "PUBLIC_CONSOLE_URL missing" in report
    assert "BLOCKED" in report
