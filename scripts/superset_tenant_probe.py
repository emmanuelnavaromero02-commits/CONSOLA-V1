#!/usr/bin/env python3
"""Local/AWS Superset tenant-safety probe.

Local mode is a static guard for the repository contract. AWS mode delegates to
the SSM live probe that checks public reachability, EC2 loopback binding, and
database role posture.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from aws_ssm import REPO, redact, utc_now, utc_stamp, write_json

import aws_superset_probe


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "superset-tenant-probe"


@dataclass
class Check:
    name: str
    status: str
    evidence: str
    unblock: str = ""


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def _check(name: str, ok: bool, evidence: str, unblock: str = "") -> Check:
    return Check(name, "PASS" if ok else "FAIL", evidence, unblock)


def _local_checks() -> list[Check]:
    aws_compose = _read("infra/terraform/deploy/docker-compose.aws.yml")
    local_compose = _read("infra/docker-compose.yml")
    aws_env_example = _read("infra/terraform/deploy/.env.example")
    entrypoint = _read("scripts/aws-entrypoint.sh")
    superset_config = _read("infra/terraform/deploy/superset_config/superset_config.py")
    gold_role = _read("infra/init_gold/34_postgres_gold_role.sql")
    gold_rls = _read("infra/init_gold/35_gold_native_rls.sql")
    mcp_main = _read("mcp-infra/app/main.py")
    mcp_superset = _read("mcp-infra/app/tools/superset.py")
    client = _read("console/app/services/superset_client.py")

    checks = [
        _check(
            "AWS Superset loopback binding",
            "127.0.0.1:8088:8088" in aws_compose
            and '"8088:8088"' not in aws_compose,
            "docker-compose.aws.yml binds Superset to 127.0.0.1",
            "Bind Superset to loopback or remove host port exposure.",
        ),
        _check(
            "Local Superset dev exposure documented",
            "8088:8088" in local_compose,
            "local compose exposes Superset for developer use only",
        ),
        _check(
            "AWS Superset public URL disabled by default",
            "SUPERSET_PUBLIC_URL=" in aws_env_example
            and "10.0.2.X:8088" not in aws_env_example
            and 'SUPERSET_PUBLIC_URL="${SUPERSET_PUBLIC_URL:-}"' in entrypoint
            and 'derive_public_url "$CONSOLE_URL" 8088' not in entrypoint,
            "SUPERSET_PUBLIC_URL stays empty unless an admin-only proxy is explicit",
            "Remove derived/public Superset URL defaults from AWS runtime config.",
        ),
        _check(
            "Superset config refuses superuser fallback",
            "SQLALCHEMY_DATABASE_URI is required; refusing superuser fallback"
            in superset_config,
            "superset_config.py requires SQLALCHEMY_DATABASE_URI",
        ),
        _check(
            "Console Superset client refuses admin fallback in production",
            "admin fallback is disabled" in client
            and "SUPERSET_SERVICE_PASSWORD" in client,
            "console uses service credentials and refuses production admin fallback",
        ),
        _check(
            "Superset Gold role is dedicated",
            "CREATE ROLE omega_refinement_gold" in gold_role
            and "omega_refinement_gold" in gold_rls,
            "Gold BI role is omega_refinement_gold",
        ),
        _check(
            "Superset Gold role NOBYPASSRLS",
            "ALTER ROLE omega_refinement_gold NOBYPASSRLS" in gold_rls,
            "omega_refinement_gold is explicitly NOBYPASSRLS",
            "Do not connect Superset to a BYPASSRLS role.",
        ),
        _check(
            "Superset MCP tools are platform/admin gated",
            "superset tools require admin studio.write context" in mcp_main
            and '"studio.write"' in mcp_main,
            "MCP Superset tools require admin studio.write context",
            "Tenant-scoped tool calls must not reach Superset tools.",
        ),
        _check(
            "Superset create tools disabled outside development",
            "disabled outside development" in mcp_superset
            and "APP_ENV" in mcp_superset,
            "create/import operations are blocked outside development",
        ),
        _check(
            "No direct UI-only security assumption",
            "/api/v1/security/login" in client
            and "superset tools require admin studio.write context" in mcp_main,
            "backend clients and MCP gates enforce access beyond hidden UI links",
        ),
    ]
    return checks


def _overall_status(checks: list[Check]) -> str:
    return "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"


def _write_report(evidence_dir: Path, summary: dict) -> None:
    lines = [
        "# Superset Tenant Probe",
        "",
        f"- target: `{summary['target']}`",
        f"- status: `{summary['status']}`",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        "",
        "| Check | Status | Evidence | Unblock |",
        "|---|---|---|---|",
    ]
    for check in summary["checks"]:
        evidence = str(check["evidence"]).replace("|", "\\|")
        unblock = str(check.get("unblock") or "").replace("|", "\\|")
        lines.append(
            f"| {check['name']} | {check['status']} | {evidence} | {unblock} |"
        )
    (evidence_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_local(evidence_dir: Path) -> int:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    checks = _local_checks()
    status = _overall_status(checks)
    summary = {
        "target": "local",
        "status": status,
        "generated_at_utc": utc_now().isoformat(),
        "verdict": "restricted/admin-only proved" if status == "PASS" else "blocked",
        "checks": [asdict(check) for check in checks],
    }
    write_json(evidence_dir / "summary.json", summary)
    _write_report(evidence_dir, summary)
    print(
        json.dumps(
            {
                "status": status,
                "evidence_dir": redact(str(evidence_dir)),
                "verdict": summary["verdict"],
            },
            indent=2,
        )
    )
    return 0 if status == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Superset tenant safety.")
    parser.add_argument("--target", choices=("local", "aws"), default="local")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    args, passthrough = parser.parse_known_args(argv)
    if args.target == "aws":
        return aws_superset_probe.main(passthrough)
    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    return _run_local(evidence_dir)


if __name__ == "__main__":
    raise SystemExit(main())
