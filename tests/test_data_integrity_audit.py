from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


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


def test_gold_audit_defaults_to_beta_profile(monkeypatch):
    module = __import__("scripts.data_integrity_audit", fromlist=["_gold_checks"])
    calls: list[str] = []

    def fake_scalar(_dsn: str, statement: Any, params: tuple[Any, ...] | None = None) -> tuple[str, str]:
        if params:
            calls.append(str(params[0]))
            return "0", str(params[0])
        sql = str(statement)
        if "rolbypassrls" in sql:
            return "0", "f"
        return "0", "3"

    monkeypatch.setenv("GOLD_DATABASE_URL", "postgresql://example")
    monkeypatch.delenv("OMEGA_AUDIT_GOLD_PROFILE", raising=False)
    monkeypatch.delenv("OMEGA_AUDIT_REQUIRED_GOLD_TABLES", raising=False)
    monkeypatch.setattr(module, "_sql_scalar", fake_scalar)

    checks = module._gold_checks()

    assert "gold_consultor_mensual" in calls
    assert "gold_pnl_mensual" in calls
    assert "gold_forecast_mensual" in calls
    assert "gold_sap_successfactors_employee_360" not in calls
    assert {check.status for check in checks} == {"PASS"}


def test_gold_audit_successfactors_profile_is_explicit(monkeypatch):
    module = __import__("scripts.data_integrity_audit", fromlist=["_gold_checks"])
    calls: list[str] = []

    def fake_scalar(_dsn: str, statement: Any, params: tuple[Any, ...] | None = None) -> tuple[str, str]:
        if params:
            table = str(params[0])
            calls.append(table)
            if table == "gold_sap_successfactors_manager_hierarchy":
                return "0", ""
            return "0", table
        if "rolbypassrls" in str(statement):
            return "0", "f"
        return "0", "0"

    monkeypatch.setenv("GOLD_DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("OMEGA_AUDIT_GOLD_PROFILE", "sap_successfactors")
    monkeypatch.delenv("OMEGA_AUDIT_REQUIRED_GOLD_TABLES", raising=False)
    monkeypatch.setattr(module, "_sql_scalar", fake_scalar)

    checks = module._gold_checks()

    assert "gold_sap_successfactors_employee_360" in calls
    assert "gold_sap_successfactors_manager_hierarchy" in calls
    assert any(check.status == "BLOCKED" and "manager_hierarchy" in check.name for check in checks)
