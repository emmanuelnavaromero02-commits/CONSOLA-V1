from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "data_integrity_audit.py"


def _run(tmp_path: Path, fixture: Path | None = None, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(env_overrides)
    command = [sys.executable, str(SCRIPT), "--evidence-dir", str(tmp_path / "evidence")]
    if fixture:
        command.extend(["--fixture", str(fixture)])
    return subprocess.run(
        command,
        cwd=REPO,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def _fixture(tmp_path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "cross_tenant_leaks": 0,
        "secret_leaks": 0,
        "mutations_without_approval": 0,
        "refresh_duplicates": 0,
        "duplicates": 0,
        "orphans": 0,
        "watermark_regressions": 0,
        "stuck_jobs": 0,
        "parquet_corrupt": 0,
        "gold_reconciliation": {
            "employee_360": {"expected": 1288, "actual": 1288},
            "headcount": {"expected": 1288, "actual": 1288},
            "org_structure": {"expected": 555, "actual": 555},
            "manager_hierarchy": {"expected": 100, "actual": 100},
        },
    }
    payload.update(overrides)
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_data_integrity_fixture_passes_and_writes_summary(tmp_path: Path):
    result = _run(tmp_path, _fixture(tmp_path))
    assert result.returncode == 0, result.stdout
    summary = json.loads((tmp_path / "evidence" / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "PASS"
    assert summary["fail"] == 0


def test_data_integrity_fixture_fails_on_leak_secret_or_duplicate(tmp_path: Path):
    result = _run(
        tmp_path,
        _fixture(
            tmp_path,
            cross_tenant_leaks=1,
            secret_leaks=1,
            refresh_duplicates=1,
        ),
    )
    assert result.returncode == 1, result.stdout
    report = (tmp_path / "evidence" / "REPORT.md").read_text(encoding="utf-8")
    assert "cross tenant leaks" in report
    assert "secret leaks" in report
    assert "refresh duplicates" in report
    assert "FAIL" in report


def test_data_integrity_without_live_inputs_is_blocked_not_pass(tmp_path: Path):
    result = _run(
        tmp_path,
        DATABASE_URL="",
        OMEGA_AUDIT_DATABASE_URL="",
        GOLD_DATABASE_URL="",
        OMEGA_AUDIT_GOLD_DATABASE_URL="",
        OMEGA_AUDIT_PARQUET_MANIFEST="",
    )
    assert result.returncode == 2
    report = (tmp_path / "evidence" / "REPORT.md").read_text(encoding="utf-8")
    assert "DATABASE_URL missing" in report
    assert "GOLD_DATABASE_URL missing" in report
    assert "OMEGA_AUDIT_PARQUET_MANIFEST missing" in report
    assert "BLOCKED" in report
