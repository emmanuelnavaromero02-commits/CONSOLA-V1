#!/usr/bin/env python3
"""Report AWS public TLS/HTTPS posture without creating paid resources."""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path

from aws_ssm import (
    DEFAULT_REGION,
    REPO,
    aws_json,
    redact,
    utc_now,
    utc_stamp,
    write_json,
)


DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "aws-tls-status"


@dataclass
class Check:
    name: str
    status: str
    evidence: str
    unblock: str = ""


def _find_alb_by_dns(region: str, hostname: str) -> dict | None:
    payload = aws_json(["elbv2", "describe-load-balancers"], region=region, timeout=60)
    for item in payload.get("LoadBalancers", []):
        if str(item.get("DNSName", "")).lower() == hostname.lower():
            return item
    return None


def _listener_checks(region: str, alb: dict | None) -> list[Check]:
    if not alb:
        return [
            Check(
                "ALB discovered",
                "BLOCKED",
                "No ALB matched the provided public hostname via AWS API.",
                "Provide PUBLIC_CONSOLE_URL that maps to the application ALB or run from an AWS-authenticated shell.",
            )
        ]
    arn = alb["LoadBalancerArn"]
    listeners = aws_json(
        ["elbv2", "describe-listeners", "--load-balancer-arn", arn],
        region=region,
        timeout=60,
    ).get("Listeners", [])
    ports = {int(item.get("Port", 0)): item for item in listeners}
    checks = [
        Check(
            "ALB discovered",
            "PASS",
            f"name={alb.get('LoadBalancerName')} scheme={alb.get('Scheme')}",
        ),
        Check(
            "HTTP listener",
            "PASS" if 80 in ports else "BLOCKED",
            f"ports={sorted(ports)}",
        ),
    ]
    https = ports.get(443)
    if https:
        certs = https.get("Certificates") or []
        checks.append(Check("HTTPS listener", "PASS", f"port=443 certs={len(certs)}"))
    else:
        checks.append(
            Check(
                "HTTPS listener",
                "BLOCKED",
                f"ports={sorted(ports)}",
                "P1: add ACM certificate, ALB HTTPS listener :443, HTTP->HTTPS redirect, and secure cookie validation.",
            )
        )
    http = ports.get(80) or {}
    actions = http.get("DefaultActions") or []
    redirects = [item for item in actions if item.get("Type") == "redirect"]
    checks.append(
        Check(
            "HTTP redirects to HTTPS",
            "PASS" if redirects else "BLOCKED",
            f"redirect_actions={len(redirects)}",
            "P1: configure ALB port 80 redirect to HTTPS once certificate exists.",
        )
    )
    return checks


def _write_report(evidence_dir: Path, summary: dict) -> None:
    lines = [
        "# AWS TLS Status",
        "",
        f"- status: `{summary['status']}`",
        f"- generated_at_utc: `{summary['generated_at_utc']}`",
        f"- public_url: `{summary['public_url']}`",
        f"- region: `{summary['region']}`",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check AWS TLS/HTTPS status without mutating infrastructure."
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--public-url",
        default=os.environ.get("PUBLIC_CONSOLE_URL")
        or os.environ.get("CONSOLE_URL")
        or "",
    )
    parser.add_argument("--evidence-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.public_url:
        raise SystemExit("PUBLIC_CONSOLE_URL or --public-url is required")
    evidence_dir = args.evidence_dir or DEFAULT_EVIDENCE_ROOT / utc_stamp()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlsplit(args.public_url)
    checks = [
        Check("public URL configured", "PASS", args.public_url),
        Check(
            "public URL scheme HTTPS",
            "PASS" if parsed.scheme == "https" else "BLOCKED",
            f"scheme={parsed.scheme}",
            "P1: use HTTPS public URL after ACM/ALB listener is configured.",
        ),
    ]
    try:
        checks.extend(
            _listener_checks(
                args.region, _find_alb_by_dns(args.region, parsed.hostname or "")
            )
        )
    except Exception as exc:  # noqa: BLE001 - evidence-only path
        checks.append(
            Check(
                "AWS ALB API probe",
                "BLOCKED",
                redact(str(exc)),
                "Run with AWS elbv2 read permissions.",
            )
        )
    status = (
        "FAIL"
        if any(c.status == "FAIL" for c in checks)
        else "BLOCKED"
        if any(c.status == "BLOCKED" for c in checks)
        else "PASS"
    )
    summary = {
        "status": status,
        "generated_at_utc": utc_now().isoformat(),
        "public_url": args.public_url,
        "region": args.region,
        "checks": [asdict(check) for check in checks],
    }
    write_json(evidence_dir / "summary.json", summary)
    _write_report(evidence_dir, summary)
    print(json.dumps({"status": status, "evidence_dir": str(evidence_dir)}, indent=2))
    return 0 if status == "PASS" else 2 if status == "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
