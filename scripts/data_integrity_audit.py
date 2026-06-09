#!/usr/bin/env python3
"""Enterprise data integrity audit.

Exit codes:
- 0: PASS
- 1: FAIL
- 2: BLOCKED, unless OMEGA_AUDIT_ALLOW_BLOCKED=1
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STATUS_ORDER = {"PASS": 0, "BLOCKED": 1, "FAIL": 2}
SECRET_KEYS = (
    "ANTHROPIC_API_KEY",
    "HUBSPOT_ACCESS_TOKEN",
    "INTERNAL_API_KEY",
    "SECURITY_CONTEXT_SIGNING_KEY",
    "VAULT_ENCRYPTION_KEY",
    "SAP_PASSWORD",
    "SF_PRIVATE_KEY",
)


@dataclass
class Check:
    name: str
    status: str
    evidence: str
    unblock: str = ""


def _evidence_root() -> Path:
    raw = os.environ.get("OMEGA_AUDIT_EVIDENCE_DIR")
    if raw:
        return Path(raw)
    return Path("docs/release-evidence/enterprise-readiness") / os.environ.get("OMEGA_ENTERPRISE_RUN_ID", "manual") / "data-integrity"


def _status(checks: list[Check]) -> str:
    return max((check.status for check in checks), key=lambda item: STATUS_ORDER[item], default="PASS")


def _redact(text: str) -> str:
    output = text
    for key in SECRET_KEYS:
        value = os.environ.get(key)
        if value:
            output = output.replace(value, "***REDACTED***")
    return output


def _fixture_checks(path: Path) -> list[Check]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    checks: list[Check] = []
    zero_fail_fields = {
        "cross_tenant_leaks": "cross tenant leaks",
        "secret_leaks": "secret leaks",
        "mutations_without_approval": "mutations without approval",
        "refresh_duplicates": "refresh duplicates",
        "duplicates": "business-key duplicates",
        "orphans": "tenant/workspace orphans",
        "watermark_regressions": "watermark regressions",
        "stuck_jobs": "stuck jobs",
        "parquet_corrupt": "corrupt parquet objects",
    }
    for key, label in zero_fail_fields.items():
        count = int(payload.get(key, 0) or 0)
        checks.append(Check(label, "FAIL" if count else "PASS", f"{key}={count}"))
    gold = payload.get("gold_reconciliation") or {}
    for dataset in ("employee_360", "headcount", "org_structure", "manager_hierarchy"):
        item = gold.get(dataset)
        if item is None:
            checks.append(Check(f"gold reconciliation {dataset}", "BLOCKED", "missing fixture field"))
            continue
        expected = item.get("expected")
        actual = item.get("actual")
        checks.append(
            Check(
                f"gold reconciliation {dataset}",
                "PASS" if expected == actual else "FAIL",
                f"expected={expected} actual={actual}",
            )
        )
    return checks


def _psql_scalar(dsn: str, sql: str) -> tuple[str, str]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(sql)
        path = handle.name
    try:
        proc = subprocess.run(
            ["psql", dsn, "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-f", path],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    return str(proc.returncode), _redact(proc.stdout.strip())


def _db_checks() -> list[Check]:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("OMEGA_AUDIT_DATABASE_URL")
    if not dsn:
        return [
            Check(
                "operational DB audit",
                "BLOCKED",
                "DATABASE_URL missing",
                "DATABASE_URL=postgresql://... make data-integrity-audit",
            )
        ]
    checks: list[Check] = []
    sql = """
    SELECT count(*) FROM pg_roles
     WHERE rolname IN ('omega_workspace','omega_mcp_infra','omega_vault')
       AND rolbypassrls = true;
    """
    code, out = _psql_scalar(dsn, sql)
    if code != "0":
        checks.append(Check("operational RLS role audit", "BLOCKED", out, "Install psql and provide DATABASE_URL"))
    else:
        count = int(out or "0")
        checks.append(Check("operational RLS role audit", "FAIL" if count else "PASS", f"bypass_roles={count}"))
    stuck_sql = """
    SELECT count(*) FROM jobs
     WHERE status = 'running'
       AND created_at < now() - interval '2 hours';
    """
    code, out = _psql_scalar(dsn, stuck_sql)
    if code != "0":
        checks.append(Check("stuck jobs audit", "BLOCKED", out, "DATABASE_URL=postgresql://... make data-integrity-audit"))
    else:
        count = int(out or "0")
        checks.append(Check("stuck jobs audit", "FAIL" if count else "PASS", f"stuck_jobs={count}"))
    return checks


def _gold_checks() -> list[Check]:
    dsn = os.environ.get("GOLD_DATABASE_URL") or os.environ.get("OMEGA_AUDIT_GOLD_DATABASE_URL")
    if not dsn:
        return [
            Check(
                "Gold DB reconciliation",
                "BLOCKED",
                "GOLD_DATABASE_URL missing",
                "GOLD_DATABASE_URL=postgresql://... make data-integrity-audit",
            )
        ]
    checks: list[Check] = []
    for table in (
        "gold_sap_successfactors_employee_360",
        "gold_sap_successfactors_headcount_by_department",
        "gold_sap_successfactors_org_structure",
        "gold_sap_successfactors_manager_hierarchy",
    ):
        code, out = _psql_scalar(dsn, f"SELECT count(*) FROM {table};")
        if code != "0":
            checks.append(Check(f"Gold row count {table}", "BLOCKED", out, "Verify Gold DB URL and migrations"))
            continue
        count = int(out or "0")
        checks.append(Check(f"Gold row count {table}", "FAIL" if count < 0 else "PASS", f"row_count={count}"))
    code, out = _psql_scalar(dsn, "SELECT rolbypassrls FROM pg_roles WHERE rolname='omega_refinement_gold';")
    if code != "0" or out == "":
        checks.append(Check("Gold NOBYPASSRLS", "BLOCKED", out or "role not visible"))
    else:
        checks.append(Check("Gold NOBYPASSRLS", "FAIL" if out.lower() == "t" else "PASS", f"rolbypassrls={out}"))
    return checks


def _parquet_checks() -> list[Check]:
    manifest = os.environ.get("OMEGA_AUDIT_PARQUET_MANIFEST")
    if not manifest:
        return [
            Check(
                "parquet consistency",
                "BLOCKED",
                "OMEGA_AUDIT_PARQUET_MANIFEST missing",
                "OMEGA_AUDIT_PARQUET_MANIFEST=/path/to/parquet_manifest.json make data-integrity-audit",
            )
        ]
    payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
    corrupt = int(payload.get("corrupt", 0) or 0)
    partial = int(payload.get("partial", 0) or 0)
    return [Check("parquet consistency", "FAIL" if corrupt or partial else "PASS", f"corrupt={corrupt} partial={partial}")]


def _write_outputs(root: Path, checks: list[Check]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "checks.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name", "status", "evidence", "unblock"])
        writer.writeheader()
        for check in checks:
            writer.writerow(check.__dict__)
    summary = {
        "status": _status(checks),
        "checks": [check.__dict__ for check in checks],
        "pass": sum(1 for check in checks if check.status == "PASS"),
        "blocked": sum(1 for check in checks if check.status == "BLOCKED"),
        "fail": sum(1 for check in checks if check.status == "FAIL"),
    }
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    lines = ["# Data Integrity Audit", "", f"- status: `{summary['status']}`", ""]
    lines.append("| Check | Status | Evidence | Unblock |")
    lines.append("|---|---|---|---|")
    for check in checks:
        evidence = _redact(check.evidence).replace("|", "\\|")
        unblock = check.unblock.replace("|", "\\|")
        lines.append(f"| {check.name} | {check.status} | {evidence} | {unblock} |")
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--evidence-dir", type=Path, default=_evidence_root())
    args = parser.parse_args(argv)
    if args.fixture:
        checks = _fixture_checks(args.fixture)
    else:
        checks = []
        checks.extend(_db_checks())
        checks.extend(_gold_checks())
        checks.extend(_parquet_checks())
    _write_outputs(args.evidence_dir, checks)
    status = _status(checks)
    print(json.dumps({"status": status, "evidence_dir": str(args.evidence_dir)}, indent=2))
    if status == "FAIL":
        return 1
    if status == "BLOCKED" and os.environ.get("OMEGA_AUDIT_ALLOW_BLOCKED") != "1":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
